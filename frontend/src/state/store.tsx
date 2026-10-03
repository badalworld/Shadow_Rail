import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from 'react'
import { endpoints, wsUrl } from '../lib/api'
import type {
  ApiState, Bot, EquityState, Link, LogRow, ScanRow, Stats, Trade, WsEvent, EngineStatus,
} from '../lib/types'

export interface Toast {
  id: number
  kind: 'info' | 'success' | 'error' | 'warn' | 'sos'
  title: string
  body?: string
}

export interface Celebration {
  id: number
  win: boolean
  symbol: string
  net: number
  reason: string
  message: string
}

interface StoreShape {
  status: EngineStatus | null
  bots: Bot[]
  botMap: Record<string, Bot>
  links: Link[]
  equity: EquityState | null
  api: ApiState | null
  logs: LogRow[]
  openTrades: Trade[]
  scan: { by_bot: Record<string, ScanRow[]>; opportunities: any[]; cycle: number; updated_at: number; seconds_to_close: number }
  stats: Stats | null
  connected: boolean
  sos: { active: boolean; level: string; reasons: string[]; since: number }
  pulses: { id: number; from: string; to: string; stage: string }[]
  toasts: Toast[]
  celebration: Celebration | null
  promotions: { id: number; name: string; from: string; to: string }[]
  refresh: () => void
  pushToast: (t: Omit<Toast, 'id'>) => void
  dismissToast: (id: number) => void
}

const StoreCtx = createContext<StoreShape | null>(null)

let uid = 1

/* ── boot payload ──────────────────────────────────────────────────────────
 * index.html ships a window.__SHADOW_RAIL_BOOT__ snapshot (the same frame the
 * websocket sends on connect) so the first paint already shows live numbers
 * instead of a flash of zeros while the socket handshakes.                */
type Boot = {
  status?: EngineStatus
  bots?: Bot[]
  links?: Link[]
  equity?: EquityState
  stats?: Stats
  scan?: StoreShape['scan']
  open_trades?: Trade[]
  closed_trades?: { trades: Trade[]; total: number }
  config?: any
  ip?: any
  about?: any
  curve?: { ts: number; cum: number }[]
  logs?: LogRow[]
}
export const BOOT: Boot = (() => {
  const g = globalThis as unknown as { __SHADOW_RAIL_BOOT__?: Boot }
  return g.__SHADOW_RAIL_BOOT__ || {}
})()

export const StoreProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [status, setStatus] = useState<EngineStatus | null>(BOOT.status ?? null)
  const [bots, setBots] = useState<Bot[]>(BOOT.bots ?? [])
  const [links, setLinks] = useState<Link[]>(BOOT.links ?? [])
  const [equity, setEquity] = useState<EquityState | null>(BOOT.equity ?? null)
  const [api, setApi] = useState<ApiState | null>(BOOT.status?.api ?? null)
  const [logs, setLogs] = useState<LogRow[]>(BOOT.logs ?? [])
  const [openTrades, setOpenTrades] = useState<Trade[]>(BOOT.open_trades ?? [])
  const [scan, setScan] = useState<StoreShape['scan']>(BOOT.scan ?? {
    by_bot: {}, opportunities: [], cycle: 0, updated_at: 0, seconds_to_close: 0,
  })
  const [stats, setStats] = useState<Stats | null>(BOOT.stats ?? null)
  const [connected, setConnected] = useState(false)
  const [sos, setSos] = useState({ active: false, level: 'none', reasons: [] as string[], since: 0 })
  const [pulses, setPulses] = useState<StoreShape['pulses']>([])
  const [toasts, setToasts] = useState<Toast[]>([])
  const [celebration, setCelebration] = useState<Celebration | null>(null)
  const [promotions, setPromotions] = useState<StoreShape['promotions']>([])
  const botMapRef = useRef<Record<string, Bot>>({})

  const pushToast = useCallback((t: Omit<Toast, 'id'>) => {
    const toast = { ...t, id: uid++ }
    setToasts((prev) => [...prev.slice(-4), toast])
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== toast.id)), 7000)
  }, [])

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((x) => x.id !== id))
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [st, eq, ot, sc, bt] = await Promise.all([
        endpoints.status(), endpoints.equity(), endpoints.openTrades(),
        endpoints.scan(), endpoints.bots(),
      ])
      setStatus(st.status)
      setBots(st.bots || bt.bots)
      setLinks(st.links || bt.links || [])
      botMapRef.current = Object.fromEntries((st.bots || []).map((b: Bot) => [b.bot_id, b]))
      setEquity(eq)
      setStats(eq.stats || null)
      setApi(st.status?.api || null)
      setSos(st.status?.sos || { active: false, level: 'none', reasons: [], since: 0 })
      setOpenTrades(ot.trades || [])
      setScan({
        by_bot: sc.by_bot || {}, opportunities: sc.opportunities || [],
        cycle: sc.cycle || 0, updated_at: sc.updated_at || 0,
        seconds_to_close: sc.seconds_to_close || 0,
      })
    } catch {
      /* transient — the websocket will catch us up */
    }
  }, [])

  const refreshTrades = useCallback(async () => {
    try {
      const [ot, eq] = await Promise.all([endpoints.openTrades(), endpoints.equity()])
      setOpenTrades(ot.trades || [])
      setEquity(eq)
      setStats(eq.stats || null)
    } catch { /* ignore */ }
  }, [])

  // ── websocket ───────────────────────────────────────────────────────────
  useEffect(() => {
    let ws: WebSocket | null = null
    let retry = 0
    let closed = false
    let poll: number | undefined

    const connect = () => {
      ws = new WebSocket(wsUrl())
      ws.onopen = () => {
        setConnected(true)
        retry = 0
        refresh()
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          retry = Math.min(retry + 1, 12)
          setTimeout(connect, 600 * retry)
        }
      }
      ws.onerror = () => setConnected(false)
      ws.onmessage = (raw) => {
        let evt: WsEvent
        try { evt = JSON.parse(raw.data) } catch { return }
        handle(evt)
      }
    }

    const handle = (evt: WsEvent) => {
      const d = evt.data || {}
      switch (evt.topic) {
        case 'hello':
          setBots(d.bots || [])
          botMapRef.current = Object.fromEntries((d.bots || []).map((b: Bot) => [b.bot_id, b]))
          setLinks(d.links || [])
          setStatus(d.status)
          setEquity(d.equity)
          if (d.status?.api) setApi(d.status.api)
          setLogs((d.logs || []).slice(0, 400))
          break
        case 'bot.update':
          setBots((prev) => {
            const next = prev.filter((b) => b.bot_id !== d.bot_id)
            next.push(d)
            next.sort((a, b) => (a.group + a.slot).localeCompare(b.group + b.slot))
            botMapRef.current = { ...botMapRef.current, [d.bot_id]: d }
            return next
          })
          break
        case 'bots.snapshot':
          setBots(d.bots || [])
          botMapRef.current = Object.fromEntries((d.bots || []).map((b: Bot) => [b.bot_id, b]))
          break
        case 'bot.promoted':
          setPromotions((prev) => [...prev.slice(-3), { id: uid++, name: d.name, from: d.from, to: d.to }])
          pushToast({ kind: 'success', title: `🎖 ${d.name} promoted`, body: `${d.from} → ${d.to}` })
          setTimeout(() => setPromotions((prev) => prev.slice(1)), 6000)
          break
        case 'bot.demoted':
          pushToast({ kind: 'warn', title: `${d.name} demoted`, body: `${d.from} → ${d.to}` })
          break
        case 'equity.update':
          setEquity(d)
          setStats(d.stats || null)
          break
        case 'api.weight':
          setApi(d)
          break
        case 'workflow.update':
          setStatus((prev) => (prev ? { ...prev, workflow: d } : prev))
          break
        case 'connector.health':
          setStatus((prev) => (prev ? { ...prev, health: d } : prev))
          break
        case 'sos.on':
          setSos({ active: true, level: d.level, reasons: d.reasons || [], since: d.since || Date.now() })
          pushToast({
            kind: 'sos',
            title: `☠️ SOS ${String(d.level || '').toUpperCase()}`,
            body: (d.reasons || []).join(' · ').slice(0, 180),
          })
          break
        case 'sos.off':
          setSos({ active: false, level: 'none', reasons: [], since: 0 })
          pushToast({ kind: 'success', title: 'Connection restored', body: 'SOS cleared — all systems green' })
          break
        case 'log.append':
          setLogs((prev) => [d, ...prev].slice(0, 500))
          break
        case 'trade.opened':
          pushToast({
            kind: 'success',
            title: `▲ ${d.trade?.symbol} ${d.trade?.side} opened`,
            body: `conf ${Number(d.proposal?.confidence || 0).toFixed(1)} · SL ${d.trade?.sl_price}`,
          })
          refreshTrades()
          break
        case 'trade.closed':
        case 'trade.win':
        case 'trade.loss':
          refreshTrades()
          break
        case 'celebration':
          setCelebration({
            id: uid++, win: !!d.win, symbol: d.symbol, net: d.net || 0,
            reason: d.reason || '', message: d.message || '',
          })
          setTimeout(() => setCelebration((c) => (c && c.id === uid - 1 ? null : c)), 4200)
          refreshTrades()
          break
        case 'link.pulse':
          setPulses((prev) => [...prev.slice(-14), { id: uid++, from: d.from, to: d.to, stage: d.stage }])
          setTimeout(() => setPulses((prev) => prev.slice(1)), 2600)
          break
        case 'trade.tick':
          setOpenTrades((prev) => prev.map((t) => (t.id === d.trade_id
            ? { ...t, mark: d.mark, unrealized: d.unrealized, liquidation_live: d.liq }
            : t)))
          break
        case 'ping':
          if (d.api) setApi(d.api)
          setConnected(true)
          break
        default:
          break
      }
    }

    connect()
    poll = window.setInterval(() => { refresh() }, 6000)
    return () => {
      closed = true
      if (poll) window.clearInterval(poll)
      ws?.close()
    }
  }, [pushToast, refresh, refreshTrades])

  // danger level drives the whole theme
  useEffect(() => {
    const level = sos.active ? sos.level : status?.paused ? 'warning' : 'none'
    document.documentElement.dataset.sos = sos.active ? level : 'none'
  }, [sos, status?.paused])

  const botMap = useMemo(() => {
    const map: Record<string, Bot> = {}
    bots.forEach((b) => { map[b.bot_id] = b })
    return map
  }, [bots])

  const value: StoreShape = {
    status, bots, botMap, links, equity, api, logs, openTrades, scan, stats,
    connected, sos, pulses, toasts, celebration, promotions,
    refresh, pushToast, dismissToast,
  }

  return <StoreCtx.Provider value={value}>{children}</StoreCtx.Provider>
}

export function useStore(): StoreShape {
  const ctx = useContext(StoreCtx)
  if (!ctx) throw new Error('useStore must be used inside <StoreProvider>')
  return ctx
}
