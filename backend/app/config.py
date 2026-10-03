"""
Shadow Rail — persistent configuration store.

All settings live in  <repo>/data/config.json  and survive restarts.
Secrets (Binance API key / secret) are encrypted at rest with a Fernet key
stored in  <repo>/data/.secret.key  (never committed, chmod 600).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.environ.get("SHADOW_RAIL_DATA_DIR", REPO_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "shadow_rail.sqlite3"
CONFIG_PATH = DATA_DIR / "config.json"
KEY_PATH = DATA_DIR / ".secret.key"
WEB_DIR = BACKEND_DIR / "web"          # built frontend (vite outDir)


# --------------------------------------------------------------------------
# secret encryption
# --------------------------------------------------------------------------
def _fernet():
    from cryptography.fernet import Fernet

    if KEY_PATH.exists():
        key = KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        KEY_PATH.write_bytes(key)
        try:
            KEY_PATH.chmod(0o600)
        except OSError:
            pass
    return Fernet(key)


def encrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().encrypt(value.encode()).decode()
    except Exception:
        return ""


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        from cryptography.fernet import InvalidToken

        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return ""
    except Exception:
        return ""


def mask(secret: str, keep: int = 4) -> str:
    """'sk-abcdef1234' -> '••••••••1234'  (for display only)."""
    if not secret:
        return ""
    if len(secret) <= keep:
        return "•" * len(secret)
    return "•" * 8 + secret[-keep:]


# --------------------------------------------------------------------------
# settings models
# --------------------------------------------------------------------------
class BinanceSettings(BaseModel):
    api_key_enc: str = ""
    api_secret_enc: str = ""
    testnet: bool = False
    # live   = real orders on real funds (as requested)
    # paper  = real market data, simulated fills (safe rehearsal)
    # sim    = fully synthetic market + fills (offline / demo)
    mode: Literal["live", "paper", "sim"] = "live"
    # 'auto' picks binance when reachable, otherwise sim (with SOS raised)
    transport: Literal["auto", "binance", "sim"] = "auto"
    ip_whitelist: str = ""               # the IP the operator whitelisted in Binance
    ip_whitelist_confirmed: bool = False
    recv_window_ms: int = 5000

    # plaintext helpers are handled by the store, never serialised directly
    @property
    def configured(self) -> bool:
        return bool(self.api_key_enc and self.api_secret_enc)


class IndicatorSettings(BaseModel):
    """Mirrors the Pine inputs 1:1 — do not rename without updating the port."""
    swingBars: int = 5
    railSpread: float = 1.6
    railDrive: float = 95.0
    ghostBlur: int = 10
    ghostOffset: float = 0.0
    ghostPlacement: Literal["Trend", "Above", "Below"] = "Trend"
    ghostEase: int = 5
    # filters — HTF locked to 1 hour per operator decision
    mtfGate: bool = True
    mtfFrame: str = "60"
    mtfEmaBars: int = 50
    minTrendPct: float = 0.0
    require_strong_flip: bool = False   # only cleanRatio >= 0.60 flips
    timeframe: str = "5m"
    kline_warmup: int = 1000


class RiskSettings(BaseModel):
    """
    Exactly ONE take-profit / stop-loss system is ever active.
    risk_mode decides which; the unused values are ignored (never double).
    """
    risk_mode: Literal["indicator_default", "shadow_3x", "custom"] = "indicator_default"
    # indicator_default -> 1.5 / 3.0   (Pine "Risk Map" defaults)
    # shadow_3x         -> 3.0 / none  (exit on reverse signal, wide stop)
    # custom            -> your numbers below
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 3.0
    tp_enabled: bool = True
    custom_sl_atr_mult: float = 1.5
    custom_tp_atr_mult: float = 3.0
    custom_tp_enabled: bool = True

    use_reverse_signal_exit: bool = True
    leverage: int = 10
    margin_type: Literal["CROSS", "ISOLATED"] = "CROSS"
    size_pct_per_trade: float = 8.0      # % of equity used as margin
    max_concurrent_trades: int = 10
    max_margin_utilization_pct: float = 100.0
    min_confidence: float = 60.0
    liq_safety_buffer_pct: float = 35.0  # stop kept <= 65% of the way to liq
    max_stop_distance_pct: float = 8.0   # sanity cap (reject beyond this)
    daily_drawdown_stop_pct: float = 0.0  # 0 = disabled
    min_notional_override: float = 0.0

    def active_tp_sl(self) -> tuple[float, float, bool]:
        """Returns (sl_atr_mult, tp_atr_mult, tp_enabled) for the ONE active system."""
        if self.risk_mode == "indicator_default":
            return 1.5, 3.0, True
        if self.risk_mode == "shadow_3x":
            return 3.0, 0.0, False
        return self.custom_sl_atr_mult, self.custom_tp_atr_mult, self.custom_tp_enabled


class EngineSettings(BaseModel):
    scanner_bots: int = 5
    assets_per_bot: int = 30
    universe_size: int = 150
    monitored_timeframe: str = "5m"
    connector_health_interval_s: int = 300   # 5 minutes, as specified
    scan_cycle_s: int = 300                  # one 5m candle
    monitor_tick_s: int = 5
    analyst_slots: int = 10
    monitor_bots: int = 4
    execution_bots: int = 2
    api_weight_limit_per_min: int = 2400     # Binance USDT-M IP budget
    api_budget_pct: float = 95.0             # hard stop at 95%
    allow_critical_above_cap: bool = False   # strict: nobody may exceed 95%
    sim_time_accel: float = 30.0             # sim only: candle every 10s
    autostart: bool = True
    simulate_when_offline: bool = True       # fall back to sim engine offline
    universe_quote: str = "USDT"


class UIConfig(BaseModel):
    theme: Literal["hacker_liquid_glass"] = "hacker_liquid_glass"
    reduce_motion: bool = False
    celebrations: bool = True
    sound_alerts: bool = False


class DeveloperInfo(BaseModel):
    name: str = "badalworld"
    role: str = "Full-Stack Quant Developer"
    github: str = "https://github.com/badalworld"
    project: str = "Shadow Rail — Automatic Trading Engine"
    tagline: str = "Trend in friend. Trend filter Trading Ghost."
    email: str = ""
    telegram: str = ""


class AppConfig(BaseModel):
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    indicator: IndicatorSettings = Field(default_factory=IndicatorSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    engine: EngineSettings = Field(default_factory=EngineSettings)
    ui: UIConfig = Field(default_factory=UIConfig)
    developer: DeveloperInfo = Field(default_factory=DeveloperInfo)

    @field_validator("risk")
    @classmethod
    def _clamp_risk(cls, v: RiskSettings) -> RiskSettings:
        v.size_pct_per_trade = max(0.1, min(100.0, v.size_pct_per_trade))
        v.leverage = max(1, min(125, v.leverage))
        v.max_concurrent_trades = max(1, min(50, v.max_concurrent_trades))
        v.liq_safety_buffer_pct = max(0.0, min(90.0, v.liq_safety_buffer_pct))
        return v


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------
class ConfigStore:
    """Thread-safe JSON config store with encrypted secrets."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._cfg = self._load()

    def _load(self) -> AppConfig:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text())
                return AppConfig.model_validate(raw)
            except Exception:
                # never lose the operator's config because of one bad field
                try:
                    backup = self.path.with_suffix(".corrupt.json")
                    self.path.replace(backup)
                except OSError:
                    pass
        return AppConfig()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cfg.model_dump(), indent=2))
            tmp.replace(self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    # ---- access -----------------------------------------------------------
    @property
    def cfg(self) -> AppConfig:
        with self._lock:
            return self._cfg

    def get(self) -> AppConfig:
        return self.cfg

    def update(self, patch: dict[str, Any]) -> AppConfig:
        """
        Deep-merge a partial patch, encrypting the secret fields.

        `api_key` / `api_secret` arrive in plain text but are not model fields —
        they are mapped onto the encrypted `*_enc` columns.  Only the sections
        (and keys) the caller actually sent are touched, so a partial patch can
        never erase the rest of the configuration.
        """
        secrets = {"api_key": "api_key_enc", "api_secret": "api_secret_enc"}
        known = {key: set(self._cfg.model_dump().get(key, {}) or {})
                 for key in ("binance", "risk", "indicator", "engine", "ui", "developer")}
        with self._lock:
            current = self._cfg.model_dump()
            for section, values in patch.items():
                if section not in current or not isinstance(values, dict):
                    continue
                for key, value in values.items():
                    if key in secrets:
                        # empty string = clear the stored secret
                        enc_key = secrets[key]
                        current[section][enc_key] = (
                            encrypt(str(value).strip()) if str(value).strip() else "")
                        continue
                    if key not in current[section]:
                        # tolerate a round-tripped payload: a key we do not model
                        # is reported rather than swallowed
                        if key in known.get(section, ()):
                            current[section][key] = value
                        continue
                    current[section][key] = value
            self._cfg = AppConfig.model_validate(current)
            self.save()
            return self._cfg

    def unknown_keys(self, patch: dict[str, Any]) -> list[str]:
        """Keys a client sent that this config does not model (UI drift guard)."""
        known = {"binance": set(BinanceSettings.model_fields),
                 "risk": set(RiskSettings.model_fields),
                 "indicator": set(IndicatorSettings.model_fields),
                 "engine": set(EngineSettings.model_fields),
                 "ui": set(UIConfig.model_fields),
                 "developer": set(DeveloperInfo.model_fields)}
        known["binance"] |= {"api_key", "api_secret"}
        out: list[str] = []
        for section, values in (patch or {}).items():
            if not isinstance(values, dict):
                continue
            if section not in known:
                out.append(section)
                continue
            out.extend(f"{section}.{k}" for k in values if k not in known[section])
        return out

    # ---- secrets ----------------------------------------------------------
    def api_key(self) -> str:
        return decrypt(self.cfg.binance.api_key_enc)

    def api_secret(self) -> str:
        return decrypt(self.cfg.binance.api_secret_enc)

    def public_view(self) -> dict[str, Any]:
        """Config safe to send to the browser (secrets masked)."""
        data = self.cfg.model_dump()
        key = self.api_key()
        secret = self.api_secret()
        data["binance"]["api_key_masked"] = mask(key)
        data["binance"]["api_secret_masked"] = mask(secret)
        data["binance"]["api_key_len"] = len(key)
        data["binance"]["has_key"] = bool(key)
        data["binance"]["has_secret"] = bool(secret)
        data["binance"].pop("api_key_enc", None)
        data["binance"].pop("api_secret_enc", None)
        data["risk"]["active_system"] = {
            "risk_mode": self.cfg.risk.risk_mode,
            "sl_atr_mult": self.cfg.risk.active_tp_sl()[0],
            "tp_atr_mult": self.cfg.risk.active_tp_sl()[1],
            "tp_enabled": self.cfg.risk.active_tp_sl()[2],
            "reverse_signal_exit": self.cfg.risk.use_reverse_signal_exit,
        }
        return data


STORE = ConfigStore()
