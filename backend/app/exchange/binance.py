"""
Binance USDT-M Futures client.

Covers everything the engine needs:
  • public market data (exchangeInfo, 24h tickers, premium index, klines)
  • signed account data (balance, positionRisk, income, userTrades)
  • order management (leverage, margin type, market orders, protective
    STOP_MARKET / TAKE_PROFIT_MARKET with closePosition=true, cancels)
  • user-data websocket (listenKey) for instant fill/position events
  • WS kline streams for live price + candle close events

Every request passes through the WeightGovernor first (95% hard ceiling),
and the exchange's own X-MBX-USED-WEIGHT-1M header is fed back into it.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import httpx
import websockets

from ..bus import BUS
from ..ratelimit import GOVERNOR, RateLimitHalt
from ..util import fnum, now_ms
from .base import (AccountSnapshot, ExchangeError, Fill, OrderResult, Position,
                   SymbolFilter, Ticker)

REST_BASE = "https://fapi.binance.com"
REST_TESTNET = "https://testnet.binancefuture.com"
WS_BASE = "wss://fstream.binance.com"
WS_TESTNET = "wss://stream.binancefuture.com"


class BinanceFutures:
    """Async Binance USDT-M USDⓈ-M futures client."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False,
                 recv_window: int = 5000, bot_id: str = "connector-bot"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.recv_window = recv_window
        self.bot_id = bot_id
        self.rest_base = REST_TESTNET if testnet else REST_BASE
        self.ws_base = WS_TESTNET if testnet else WS_BASE
        self._client: httpx.AsyncClient | None = None
        self._filters: dict[str, SymbolFilter] = {}
        self._filters_at: float = 0.0
        self._prices: dict[str, float] = {}
        self._listen_key: str = ""
        self._listen_key_at: float = 0.0
        self.last_weight_header: int = 0
        self.healthy: bool = True
        self.last_error: str = ""

    # ----------------------------------------------------------------- setup
    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.rest_base,
                timeout=httpx.Timeout(15.0, connect=8.0),
                limits=httpx.Limits(max_connections=24, max_keepalive_connections=12),
                headers={"X-MBX-APIKEY": self.api_key, "User-Agent": "ShadowRail/1.0"},
            )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------- internals
    async def _request(self, method: str, path: str, *, signed: bool = False,
                       op: str = "ping", params: dict | None = None,
                       critical: bool = False) -> Any:
        if self._client is None:
            await self.start()
        params = dict(params or {})
        if signed:
            params["timestamp"] = now_ms()
            params["recvWindow"] = self.recv_window
            query = urlencode(params, doseq=True)
            signature = hmac.new(self.api_secret.encode(), query.encode(),
                                 hashlib.sha256).hexdigest()
            params["signature"] = signature

        # ── 95% guard: reserve weight BEFORE the socket is touched ────────
        try:
            await GOVERNOR.acquire(self.bot_id, op, critical=critical)
        except RateLimitHalt as exc:
            self.last_error = str(exc)
            raise ExchangeError(str(exc), code=-1003, retryable=True) from exc

        try:
            res = await self._client.request(method, path, params=params)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError, httpx.NetworkError) as exc:
            self.healthy = False
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise ExchangeError(f"network error: {exc}", retryable=True) from exc

        weight = res.headers.get("X-MBX-USED-WEIGHT-1M") or res.headers.get("x-mbx-used-weight-1m")
        if weight and weight.isdigit():
            self.last_weight_header = int(weight)
            GOVERNOR.sync_from_header(int(weight))

        if res.status_code in (418, 429):
            self.healthy = False
            retry_after = fnum(res.headers.get("Retry-After"), 60.0)
            self.last_error = f"rate limited ({res.status_code}), retry after {retry_after}s"
            BUS.publish("api.limited", {"status": res.status_code, "retry_after": retry_after})
            raise ExchangeError(self.last_error, status=res.status_code, retryable=True)
        if res.status_code >= 500:
            self.healthy = False
            self.last_error = f"exchange {res.status_code}"
            raise ExchangeError(self.last_error, status=res.status_code, retryable=True)
        if res.status_code >= 400:
            try:
                body = res.json()
            except Exception:
                body = {"msg": res.text[:300]}
            self.last_error = str(body.get("msg") or body)
            raise ExchangeError(self.last_error, code=body.get("code"),
                                status=res.status_code,
                                retryable=res.status_code in (408, 504))
        self.healthy = True
        try:
            return res.json()
        except Exception:
            return None

    # ---------------------------------------------------------- market data
    async def ping(self) -> float:
        t0 = time.perf_counter()
        await self._request("GET", "/fapi/v1/ping", op="ping")
        return (time.perf_counter() - t0) * 1000.0

    async def server_time(self) -> int:
        data = await self._request("GET", "/fapi/v1/time", op="time")
        return int(data.get("serverTime", 0))

    async def filters(self, refresh: bool = False) -> dict[str, SymbolFilter]:
        if self._filters and not refresh and (time.time() - self._filters_at) < 3600:
            return self._filters
        info = await self._request("GET", "/fapi/v1/exchangeInfo", op="exchangeInfo")
        out: dict[str, SymbolFilter] = {}
        for s in info.get("symbols", []):
            if s.get("quoteAsset") != "USDT" or s.get("contractType") != "PERPETUAL":
                continue
            if s.get("status") != "TRADING":
                continue
            f = SymbolFilter(symbol=s["symbol"], quote=s["quoteAsset"],
                             contract_type=s["contractType"], status=s["status"],
                             price_precision=int(s.get("pricePrecision", 2)),
                             qty_precision=int(s.get("quantityPrecision", 3)))
            for flt in s.get("filters", []):
                t = flt.get("filterType")
                if t == "PRICE_FILTER":
                    f.tick_size = fnum(flt.get("tickSize"), f.tick_size)
                elif t == "LOT_SIZE":
                    f.step_size = fnum(flt.get("stepSize"), f.step_size)
                    f.min_qty = fnum(flt.get("minQty"), f.min_qty)
                    f.max_qty = fnum(flt.get("maxQty"), f.max_qty)
                elif t == "MIN_NOTIONAL":
                    f.min_notional = fnum(flt.get("notional"), f.min_notional)
            out[s["symbol"]] = f
        self._filters = out
        self._filters_at = time.time()
        return out

    async def tickers(self) -> dict[str, Ticker]:
        raw = await self._request("GET", "/fapi/v1/ticker/24hr", op="ticker24hr_all")
        out: dict[str, Ticker] = {}
        for t in raw:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            last = fnum(t.get("lastPrice"))
            out[sym] = Ticker(
                symbol=sym, last=last,
                bid=fnum(t.get("bidPrice"), last), ask=fnum(t.get("askPrice"), last),
                quote_volume=fnum(t.get("quoteVolume")),
                price_change_pct=fnum(t.get("priceChangePercent")),
                high=fnum(t.get("highPrice")), low=fnum(t.get("lowPrice")),
                updated_at=now_ms())
            self._prices[sym] = last
        return out

    async def premium_index(self) -> dict[str, dict]:
        raw = await self._request("GET", "/fapi/v1/premiumIndex", op="premiumIndex_all")
        out = {}
        for p in raw:
            out[p.get("symbol", "")] = {
                "mark": fnum(p.get("markPrice")),
                "index": fnum(p.get("indexPrice")),
                "funding": fnum(p.get("lastFundingRate")),
                "next_funding": int(fnum(p.get("nextFundingTime"))),
            }
        return out

    async def klines(self, symbol: str, interval: str = "5m", limit: int = 500,
                     start_ms: int | None = None, end_ms: int | None = None) -> list:
        from ..util import Candle
        op = "klines" if limit <= 100 else ("klines_mid" if limit <= 500 else "klines_big")
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        raw = await self._request("GET", "/fapi/v1/klines", op=op, params=params)
        out = []
        for k in raw:
            out.append(Candle(int(k[0]), fnum(k[1]), fnum(k[2]), fnum(k[3]),
                              fnum(k[4]), fnum(k[5])))
        if out:
            self._prices[symbol] = out[-1].c
        return out

    async def mark_price(self, symbol: str) -> float:
        data = await self._request("GET", "/fapi/v1/premiumIndex", op="premiumIndex_all",
                                   params={"symbol": symbol})
        if isinstance(data, list):
            data = data[0] if data else {}
        return fnum(data.get("markPrice"))

    def last_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)

    # -------------------------------------------------------------- account
    async def account(self) -> AccountSnapshot:
        data = await self._request("GET", "/fapi/v2/account", signed=True, op="account")
        snap = AccountSnapshot(
            total_wallet_balance=fnum(data.get("totalWalletBalance")),
            total_margin_balance=fnum(data.get("totalMarginBalance")),
            available_balance=fnum(data.get("availableBalance")),
            total_unrealized_pnl=fnum(data.get("totalUnrealizedProfit")),
            total_initial_margin=fnum(data.get("totalInitialMargin")),
            total_maint_margin=fnum(data.get("totalMaintMargin")),
            source="binance", ts=now_ms())
        for p in data.get("positions", []):
            amt = fnum(p.get("positionAmt"))
            if abs(amt) < 1e-12:
                continue
            snap.positions.append(Position(
                symbol=p.get("symbol", ""),
                side="LONG" if amt > 0 else "SHORT",
                qty=abs(amt), entry_price=fnum(p.get("entryPrice")),
                mark_price=fnum(p.get("markPrice")),
                liquidation_price=fnum(p.get("liquidationPrice")),
                leverage=int(fnum(p.get("leverage"), 10)),
                margin_type="CROSS" if str(p.get("marginType", "")).upper().startswith("CROSS") else "ISOLATED",
                unrealized_pnl=fnum(p.get("unrealizedProfit")),
                isolated_margin=fnum(p.get("isolatedMargin")),
                notional=abs(fnum(p.get("notional"), abs(amt) * fnum(p.get("markPrice")))),
            ))
        return snap

    async def positions(self) -> list[Position]:
        raw = await self._request("GET", "/fapi/v2/positionRisk", signed=True, op="positionRisk")
        out = []
        for p in raw:
            amt = fnum(p.get("positionAmt"))
            if abs(amt) < 1e-12:
                continue
            out.append(Position(
                symbol=p.get("symbol", ""),
                side="LONG" if amt > 0 else "SHORT",
                qty=abs(amt), entry_price=fnum(p.get("entryPrice")),
                mark_price=fnum(p.get("markPrice")),
                liquidation_price=fnum(p.get("liquidationPrice")),
                leverage=int(fnum(p.get("leverage"), 10)),
                margin_type="CROSS" if str(p.get("marginType", "")).upper().startswith("CROSS") else "ISOLATED",
                unrealized_pnl=fnum(p.get("unRealizedProfit", p.get("unrealizedProfit"))),
                notional=abs(amt) * fnum(p.get("markPrice")),
            ))
        return out

    async def income(self, start_ms: int | None = None, income_type: str | None = None,
                     limit: int = 1000) -> list[Fill]:
        params: dict[str, Any] = {"limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        if income_type:
            params["incomeType"] = income_type
        raw = await self._request("GET", "/fapi/v1/income", signed=True, op="income",
                                  params=params)
        out = []
        for r in raw:
            out.append(Fill(symbol=r.get("symbol", ""), side=r.get("incomeType", ""),
                            qty=0.0, price=0.0, commission=0.0,
                            realized_pnl=fnum(r.get("income")), ts=int(fnum(r.get("time"))),
                            trade_id=str(r.get("tranId", "")), kind="FUNDING_FEE"
                            if r.get("incomeType") == "FUNDING_FEE" else r.get("incomeType", "TRADE")))
        return out

    async def user_trades(self, symbol: str, start_ms: int | None = None,
                          limit: int = 500) -> list[Fill]:
        params: dict[str, Any] = {"symbol": symbol, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        raw = await self._request("GET", "/fapi/v1/userTrades", signed=True, op="userTrades",
                                  params=params)
        out = []
        for r in raw:
            out.append(Fill(
                symbol=r.get("symbol", symbol),
                side="BUY" if r.get("side") == "BUY" else "SELL",
                qty=fnum(r.get("qty")), price=fnum(r.get("price")),
                commission=fnum(r.get("commission")),
                commission_asset=r.get("commissionAsset", "USDT"),
                realized_pnl=fnum(r.get("realizedPnl")),
                ts=int(fnum(r.get("time"))), order_id=str(r.get("orderId", "")),
                trade_id=str(r.get("id", "")), is_maker=bool(r.get("maker"))))
        return out

    # --------------------------------------------------------------- trading
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._request("POST", "/fapi/v1/leverage", signed=True, op="leverage",
                            params={"symbol": symbol, "leverage": int(leverage)})

    async def set_margin_type(self, symbol: str, margin_type: str) -> None:
        try:
            await self._request("POST", "/fapi/v1/marginType", signed=True, op="marginType",
                                params={"symbol": symbol, "marginType": margin_type.upper()})
        except ExchangeError as exc:
            # -4046 = "No need to change margin type" — not an error for us
            if exc.code != -4046:
                raise

    async def leverage_brackets(self) -> list[dict]:
        return await self._request("GET", "/fapi/v1/leverageBracket", signed=True,
                                   op="leverageBracket")

    async def market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False, client_id: str = "") -> OrderResult:
        params: dict[str, Any] = {
            "symbol": symbol, "side": side.upper(), "type": "MARKET",
            "quantity": f"{qty:.10f}".rstrip("0").rstrip("."),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_id:
            params["newClientOrderId"] = client_id[:36]
        data = await self._request("POST", "/fapi/v1/order", signed=True, op="order",
                                   params=params, critical=True)
        avg = fnum(data.get("avgPrice")) or fnum(data.get("price"))
        return OrderResult(order_id=str(data.get("orderId")), symbol=symbol,
                           side=side.upper(), type="MARKET",
                           status=data.get("status", "NEW"), qty=fnum(data.get("executedQty"), qty),
                           avg_price=avg, client_id=data.get("clientOrderId", ""), raw=data)

    async def stop_market(self, symbol: str, side: str, stop_price: float,
                          close_position: bool = True, qty: float | None = None,
                          working_type: str = "MARK_PRICE",
                          client_id: str = "") -> OrderResult:
        params: dict[str, Any] = {
            "symbol": symbol, "side": side.upper(), "type": "STOP_MARKET",
            "stopPrice": f"{stop_price:.10f}".rstrip("0").rstrip("."),
            "workingType": working_type, "priceProtect": "true",
        }
        if close_position:
            params["closePosition"] = "true"
        elif qty:
            params["quantity"] = f"{qty:.10f}".rstrip("0").rstrip(".")
        if client_id:
            params["newClientOrderId"] = client_id[:36]
        data = await self._request("POST", "/fapi/v1/order", signed=True, op="order",
                                   params=params, critical=True)
        return OrderResult(order_id=str(data.get("orderId")), symbol=symbol,
                           side=side.upper(), type="STOP_MARKET",
                           status=data.get("status", "NEW"), qty=qty or 0.0,
                           stop_price=stop_price, client_id=data.get("clientOrderId", ""),
                           raw=data)

    async def take_profit_market(self, symbol: str, side: str, stop_price: float,
                                 close_position: bool = True, qty: float | None = None,
                                 working_type: str = "MARK_PRICE",
                                 client_id: str = "") -> OrderResult:
        params: dict[str, Any] = {
            "symbol": symbol, "side": side.upper(), "type": "TAKE_PROFIT_MARKET",
            "stopPrice": f"{stop_price:.10f}".rstrip("0").rstrip("."),
            "workingType": working_type, "priceProtect": "true",
        }
        if close_position:
            params["closePosition"] = "true"
        elif qty:
            params["quantity"] = f"{qty:.10f}".rstrip("0").rstrip(".")
        if client_id:
            params["newClientOrderId"] = client_id[:36]
        data = await self._request("POST", "/fapi/v1/order", signed=True, op="order",
                                   params=params, critical=True)
        return OrderResult(order_id=str(data.get("orderId")), symbol=symbol,
                           side=side.upper(), type="TAKE_PROFIT_MARKET",
                           status=data.get("status", "NEW"), qty=qty or 0.0,
                           stop_price=stop_price, client_id=data.get("clientOrderId", ""),
                           raw=data)

    async def cancel_all(self, symbol: str) -> None:
        try:
            await self._request("DELETE", "/fapi/v1/allOpenOrders", signed=True,
                                op="allOpenOrders", params={"symbol": symbol}, critical=True)
        except ExchangeError as exc:
            if exc.code not in (-2011,):    # -2011 = unknown order / nothing to cancel
                raise

    async def open_orders(self, symbol: str | None = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        return await self._request("GET", "/fapi/v1/openOrders", signed=True,
                                   op="openOrders", params=params)

    async def query_order(self, symbol: str, order_id: str) -> dict:
        return await self._request("GET", "/fapi/v1/order", signed=True, op="order",
                                   params={"symbol": symbol, "orderId": order_id})

    # -------------------------------------------------------------- streams
    async def new_listen_key(self) -> str:
        data = await self._request("POST", "/fapi/v1/listenKey", signed=True, op="listenKey")
        self._listen_key = data.get("listenKey", "")
        self._listen_key_at = time.time()
        return self._listen_key

    async def keepalive_listen_key(self) -> str:
        # Binance expires listen keys after 60 min; refresh every ~25 min
        if self._listen_key and (time.time() - self._listen_key_at) < 1500:
            return self._listen_key
        if self._listen_key:
            try:
                await self._request("PUT", "/fapi/v1/listenKey", signed=True, op="listenKey",
                                    params={"listenKey": self._listen_key})
                self._listen_key_at = time.time()
                return self._listen_key
            except ExchangeError:
                pass
        return await self.new_listen_key()

    async def stream_klines(self, symbols: Iterable[str], interval: str,
                            on_candle: Callable[[dict], Any],
                            on_close: Callable[[dict], Any] | None = None,
                            chunk: int = 150) -> None:
        """
        Subscribe to kline streams in chunks (Binance allows 200 streams per
        connection; we stay well under). Emits the *forming* candle so the
        dashboard price ticks, and a close event when x=true.
        """
        syms = [s.lower() for s in symbols]
        for i in range(0, len(syms), chunk):
            part = syms[i:i + chunk]
            streams = "/".join(f"{s}@kline_{interval}" for s in part)
            url = f"{self.ws_base}/stream?streams={streams}"
            asyncio.create_task(self._kline_worker(url, on_candle, on_close))

    async def _kline_worker(self, url: str, on_candle: Callable, on_close: Callable | None) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=180, ping_timeout=60,
                                              max_queue=4096, close_timeout=8) as ws:
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        data = msg.get("data") or msg
                        if data.get("e") != "kline":
                            continue
                        k = data.get("k", {})
                        payload = {
                            "symbol": data.get("s"),
                            "open_time": int(k.get("t", 0)),
                            "close_time": int(k.get("T", 0)),
                            "open": fnum(k.get("o")), "high": fnum(k.get("h")),
                            "low": fnum(k.get("l")), "close": fnum(k.get("c")),
                            "volume": fnum(k.get("v")), "closed": bool(k.get("x")),
                        }
                        self._prices[payload["symbol"]] = payload["close"]
                        try:
                            res = on_candle(payload)
                            if asyncio.iscoroutine(res):
                                await res
                            if payload["closed"] and on_close:
                                res = on_close(payload)
                                if asyncio.iscoroutine(res):
                                    await res
                        except Exception as exc:              # never kill the feed
                            BUS.publish("ws.error", {"error": str(exc)[:200], "where": "kline"})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                BUS.publish("ws.reconnect", {"where": "kline", "error": str(exc)[:200],
                                             "backoff": backoff})
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 1.8)

    async def stream_user(self, on_event: Callable[[dict], Any]) -> None:
        """ORDER_TRADE_UPDATE / ACCOUNT_UPDATE / MARGIN_CALL stream."""
        backoff = 1.0
        while True:
            try:
                key = await self.keepalive_listen_key()
                url = f"{self.ws_base}/ws/{key}"
                async with websockets.connect(url, ping_interval=180, ping_timeout=60,
                                              close_timeout=8) as ws:
                    backoff = 1.0
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        res = on_event(msg)
                        if asyncio.iscoroutine(res):
                            await res
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                BUS.publish("ws.reconnect", {"where": "user", "error": str(exc)[:200],
                                             "backoff": backoff})
                self._listen_key = ""
                await asyncio.sleep(backoff)
                backoff = min(60.0, backoff * 1.8)
