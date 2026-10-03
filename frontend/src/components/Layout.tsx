import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity, AlertOctagon, BarChart3, Bot as BotIcon, Info, LayoutDashboard, ListTree,
  Pause, Play, Power, Radar, Settings as SettingsIcon, ShieldAlert, Square, Terminal, Zap,
} from 'lucide-react'
import { useStore } from '../state/store'
import { endpoints } from '../lib/api'
import { Chip, fmtMoney, fmtPct, statusColor } from './Glass'
import { Toasts } from './Toasts'
import { CelebrationOverlay } from './Celebration'

export type PageKey = 'dashboard' | 'scan' | 'trades' | 'bots' | 'logs' | 'settings' | 'about'

const NAV: { key: PageKey; label: string; icon: React.ReactNode; hint: string }[] = [
  { key: 'dashboard', label: 'Command Deck', icon: <LayoutDashboard size={17} />, hint: 'Bot workflow + balance' },
  { key: 'scan', label: 'Market Scan', icon: <Radar size={17} />, hint: 'Scanner bots × assets' },
  { key: 'trades', label: 'Closed Trades', icon: <BarChart3 size={17} />, hint: 'History & P&L' },
  { key: 'bots', label: 'Bot Roster', icon: <BotIcon size={17} />, hint: '29 agents' },
  { key: 'logs', label: 'Workflow Log', icon: <Terminal size={17} />, hint: 'Full audit trail' },
  { key: 'settings', label: 'Settings', icon: <SettingsIcon size={17} />, hint: 'Keys, risk, IP' },
  { key: 'about', label: 'About', icon: <Info size={17} />, hint: 'Developer' },
]

export const Layout: React.FC<{
  page: PageKey
  setPage: (p: PageKey) => void
  children: React.ReactNode
}> = ({ page, setPage, children }) => {
  const { status, equity, api, sos, connected, pushToast } = useStore()
  const [busy, setBusy] = useState(false)
  const critical = sos.active && sos.level === 'critical'
  const warning = sos.active && sos.level === 'warning'
  const mode = status?.mode || '—'
  const transport = status?.transport || '—'

  const act = async (fn: () => Promise<any>, label: string) => {
    setBusy(true)
    try {
      await fn()
      pushToast({ kind: 'success', title: label })
    } catch (e: any) {
      pushToast({ kind: 'error', title: `${label} failed`, body: String(e?.message || e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative z-10 flex h-screen w-screen">
      {/* ───────────────────────── sidebar ───────────────────────── */}
      <aside className="glass m-3 mr-0 flex w-[16.5rem] shrink-0 flex-col p-3">
        <div className="flex items-center gap-3 px-1 py-2">
          <img src="/logo.svg" alt="Shadow Rail" width={44} height={44} className="rounded-xl" />
          <div className="min-w-0">
            <div className="truncate text-[0.95rem] font-bold tracking-[0.14em] accent-text glow-text">
              SHADOW RAIL
            </div>
            <div className="truncate text-[0.6rem] dim">AUTO TRADING ENGINE · v1.0</div>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap gap-1.5 px-1">
          <Chip color={mode === 'live' ? 'var(--color-bull)' : mode === 'paper' ? 'var(--color-amber)' : 'var(--color-cyan)'}>
            {mode === 'sim' ? '◈ simulation' : mode === 'live' ? '● live' : '◐ paper'}
          </Chip>
          <Chip color={transport === 'binance' ? 'var(--color-bull)' : 'var(--color-amber)'}>
            {transport === 'binance' ? 'binance usdt-m' : 'sim feed'}
          </Chip>
        </div>

        <nav className="mt-4 flex flex-col gap-1">
          {NAV.map((n) => {
            const active = page === n.key
            return (
              <button key={n.key} onClick={() => setPage(n.key)}
                className="group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-all"
                style={{
                  background: active ? 'color-mix(in oklab, var(--sr-accent) 14%, transparent)' : 'transparent',
                  border: `1px solid ${active ? 'color-mix(in oklab, var(--sr-accent) 40%, transparent)' : 'transparent'}`,
                }}>
                <span style={{ color: active ? 'var(--sr-accent)' : 'var(--sr-dim)' }}>{n.icon}</span>
                <span className="flex-1">
                  <span className="block text-[0.82rem] font-medium">{n.label}</span>
                  <span className="block text-[0.6rem] dim">{n.hint}</span>
                </span>
                {active && <span className="h-6 w-[2px] rounded-full" style={{ background: 'var(--sr-accent)' }} />}
              </button>
            )
          })}
        </nav>

        <div className="mt-auto flex flex-col gap-2 px-1 pt-3">
          <div className="glass-solid p-2.5">
            <div className="mb-1.5 flex items-center justify-between text-[0.6rem] dim">
              <span>ENGINE</span>
              <span className="flex items-center gap-1">
                <span className="inline-block h-1.5 w-1.5 rounded-full pulse-dot"
                  style={{ background: status?.running ? 'var(--color-bull)' : 'var(--color-bear)' }} />
                {status?.running ? 'RUNNING' : 'STOPPED'}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-1.5">
              {status?.running ? (
                <>
                  <button disabled={busy} className="chip justify-center hover:opacity-80"
                    onClick={() => act(() => endpoints.enginePause(!status?.paused), status?.paused ? 'Resumed' : 'Paused')}>
                    {status?.paused ? <Play size={11} /> : <Pause size={11} />} {status?.paused ? 'resume' : 'pause'}
                  </button>
                  <button disabled={busy} className="chip justify-center hover:opacity-80"
                    onClick={() => act(() => endpoints.engineStop(), 'Engine stopped')}>
                    <Square size={11} /> stop
                  </button>
                </>
              ) : (
                <button disabled={busy} className="chip col-span-2 justify-center hover:opacity-80"
                  onClick={() => act(() => endpoints.engineStart(), 'Engine started')}>
                  <Power size={11} /> start engine
                </button>
              )}
              <button disabled={busy} className="chip col-span-2 justify-center hover:opacity-80"
                style={{ borderColor: 'color-mix(in oklab, var(--color-bear) 45%, transparent)', color: 'var(--color-bear)' }}
                onClick={() => {
                  if (confirm('Flatten EVERY open position at market? This is the panic button.')) {
                    act(() => endpoints.emergencyClose(), 'Emergency flatten sent')
                  }
                }}>
                <ShieldAlert size={11} /> flatten all
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between px-1 text-[0.6rem] dim">
            <span className="flex items-center gap-1" title={
              connected
                ? 'Realtime websocket connected — trades, bots and P&L update instantly'
                : 'Websocket reconnecting — the REST poll keeps every panel fresh meanwhile'
            }>
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${connected ? 'pulse-dot' : ''}`}
                style={{
                  background: connected ? 'var(--color-bull)'
                    : status ? 'var(--color-amber)' : 'var(--sr-dim)',
                }} />
              {connected ? 'stream live' : status ? 'polling' : 'connecting'}
            </span>
            <span className="mono">cycle #{status?.cycle ?? 0}</span>
          </div>
        </div>
      </aside>

      {/* ───────────────────────── main column ───────────────────────── */}
      <main className="flex min-w-0 flex-1 flex-col p-3">
        <header className="glass mb-3 flex flex-wrap items-center gap-3 px-4 py-2.5">
          <div className="flex items-center gap-2">
            <Zap size={15} className="accent-text" />
            <span className="text-[0.7rem] font-semibold uppercase tracking-[0.18em] dim">
              {NAV.find((n) => n.key === page)?.label}
            </span>
          </div>

          <div className="mx-1 hidden h-6 w-px md:block" style={{ background: 'var(--sr-border)' }} />

          <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
            <HeaderStat label="Equity" value={fmtMoney(equity?.equity)} tone="accent" />
            <HeaderStat label="Starting" value={fmtMoney(equity?.starting_balance)} locked />
            <HeaderStat label="Released P&L" value={fmtMoney(equity?.released_pnl)}
              tone={(equity?.released_pnl ?? 0) >= 0 ? 'good' : 'bad'} />
            <HeaderStat label="Open" value={`${equity?.open_positions ?? 0}/${status?.max_trades ?? 10}`} />
            <HeaderStat
              label="Daily"
              value={fmtPct(equity?.day_start_equity ? (equity.daily_pnl / equity.day_start_equity) * 100 : 0)}
              tone={(equity?.daily_pnl ?? 0) >= 0 ? 'good' : 'bad'} />
          </div>

          <div className="ml-auto flex items-center gap-3">
            <div className="hidden items-center gap-2 lg:flex">
              <span className="text-[0.6rem] uppercase tracking-wider dim">api</span>
              <div className="h-1.5 w-24 overflow-hidden rounded-full" style={{ background: 'rgba(255,255,255,0.08)' }}>
                <div className="h-full rounded-full transition-[width] duration-500"
                  style={{
                    width: `${Math.min(100, api?.cap_used_pct ?? 0)}%`,
                    background: (api?.cap_used_pct ?? 0) > 80 ? 'var(--color-bear)' : 'var(--sr-accent)',
                    boxShadow: '0 0 8px currentColor',
                  }} />
              </div>
              <span className="mono text-[0.65rem] dim">{(api?.used_pct ?? 0).toFixed(1)}%</span>
            </div>
            <Chip color={critical ? 'var(--color-bear)' : warning ? 'var(--color-amber)' : 'var(--color-bull)'}>
              {critical ? '☠ sos critical' : warning ? '⚠ degraded' : '● all systems green'}
            </Chip>
          </div>
        </header>

        <AnimatePresence>
          {sos.active && (
            <motion.div
              initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className={`glass mb-3 flex items-center gap-3 px-4 py-2.5 ${critical ? 'sos-flash shake' : ''}`}
              style={{
                borderColor: critical ? 'var(--color-bear)' : 'var(--color-amber)',
                background: critical ? 'rgba(255,45,85,0.12)' : 'rgba(251,191,36,0.10)',
              }}>
              <AlertOctagon size={20} style={{ color: critical ? 'var(--color-bear)' : 'var(--color-amber)' }} />
              <div className="min-w-0 flex-1">
                <div className="text-[0.8rem] font-semibold tracking-wide"
                  style={{ color: critical ? 'var(--color-bear)' : 'var(--color-amber)' }}>
                  {critical ? '☠️ SOS — CONNECTION LOST. TRADING HALTED.' : '⚠️ CONNECTION DEGRADED'}
                </div>
                <div className="truncate text-[0.7rem] dim">
                  {sos.reasons.join(' · ') || 'Connector Bot reported a problem'}
                  {!critical && ' — monitoring continues'}
                </div>
              </div>
              <button className="chip hover:opacity-80"
                onClick={() => act(() => endpoints.probe(), 'Connection re-probed')}>
                <Activity size={11} /> re-probe now
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="min-h-0 flex-1">{children}</div>
      </main>

      <Toasts />
      <CelebrationOverlay />
    </div>
  )
}

const HeaderStat: React.FC<{
  label: string; value: string; tone?: 'default' | 'good' | 'bad' | 'accent'; locked?: boolean
}> = ({ label, value, tone = 'default', locked }) => {
  const color = tone === 'good' ? 'var(--color-bull)' : tone === 'bad' ? 'var(--color-bear)'
    : tone === 'accent' ? 'var(--sr-accent)' : 'var(--sr-text)'
  return (
    <div className="leading-tight">
      <div className="flex items-center gap-1 text-[0.58rem] uppercase tracking-[0.14em] dim">
        {label}{locked && <span title="Locked at first connect">🔒</span>}
      </div>
      <div className="mono text-[0.9rem] tabular" style={{ color }}>{value}</div>
    </div>
  )
}
