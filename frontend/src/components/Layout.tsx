import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity, AlertOctagon, BarChart3, Bot as BotIcon, LayoutDashboard, ListTree,
  MoreHorizontal, Pause, Play, Power, Radar, Settings as SettingsIcon, ShieldAlert,
  Square, Terminal, Wallet, Zap, Waves,
} from 'lucide-react'
import { useStore } from '../state/store'
import { endpoints } from '../lib/api'
import { Chip, statusColor } from './Glass'
import { Toasts } from './Toasts'

export type PageKey = 'dashboard' | 'positions' | 'account' | 'scan' | 'trades' | 'bots' | 'logs'
  | 'settings'

const NAV: { key: PageKey; label: string; icon: React.ReactNode; hint: string }[] = [
  { key: 'dashboard', label: 'Command Deck', icon: <LayoutDashboard size={19} />, hint: '3D bot work zone' },
  { key: 'positions', label: 'Open Positions', icon: <Activity size={19} />, hint: 'Live trades + trail' },
  { key: 'account', label: 'Account', icon: <Wallet size={19} />, hint: 'Balance, equity, fees, win rate' },
  { key: 'scan', label: 'Market Scan', icon: <Radar size={19} />, hint: 'Scanner bots × assets' },
  { key: 'trades', label: 'Closed Trades', icon: <BarChart3 size={19} />, hint: 'History & P&L' },
  { key: 'bots', label: 'Bot Roster', icon: <BotIcon size={19} />, hint: '29 agents + pipeline' },
  { key: 'logs', label: 'Workflow Log', icon: <Terminal size={19} />, hint: 'Full audit trail' },
  { key: 'settings', label: 'Settings', icon: <SettingsIcon size={19} />, hint: 'Keys, risk, IP' },
]

export const Layout: React.FC<{
  page: PageKey
  setPage: (p: PageKey) => void
  children: React.ReactNode
}> = ({ page, setPage, children }) => {
  const { status, api, sos, connected, pushToast } = useStore()
  const [busy, setBusy] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const critical = sos.active && sos.level === 'critical'
  const warning = sos.active && sos.level === 'warning'
  /* a keyless simulator is a *notice*, not a problem: the header says so and
     nothing is raised over the dashboard */
  const notice = !sos.active && sos.level === 'notice'
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
      <aside
        className="glass m-3 mr-0 flex shrink-0 flex-col overflow-hidden p-2.5 transition-[width] duration-300"
        style={{ width: menuOpen ? '15.5rem' : '4.35rem' }}>

        {/* header: logo (always) + ⋯ to expand the full menu */}
        <div className="flex items-center gap-2">
          <img src="/logo.svg" alt="Shadow Rail" width={menuOpen ? 38 : 32}
            height={menuOpen ? 38 : 32} className="shrink-0 rounded-xl" />
          {menuOpen && (
            <div className="min-w-0 flex-1">
              <div className="truncate text-[0.9rem] font-bold tracking-[0.14em] accent-text glow-text">
                SHADOW RAIL
              </div>
              <div className="truncate text-[0.58rem] dim">AUTO TRADING ENGINE · v1.0</div>
            </div>
          )}
          <button
            onClick={() => setMenuOpen((v) => !v)}
            title={menuOpen ? 'Collapse menu' : 'Open menu'}
            aria-label={menuOpen ? 'Collapse menu' : 'Open menu'}
            className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-lg transition-colors hover:opacity-80"
            style={{
              border: '1px solid var(--sr-border)',
              background: menuOpen ? 'color-mix(in oklab, var(--sr-accent) 16%, transparent)' : 'transparent',
              cursor: 'pointer',
            }}>
            <MoreHorizontal size={15} style={{ color: menuOpen ? 'var(--sr-accent)' : 'var(--sr-dim)' }} />
          </button>
        </div>

        {/* short labels — the compact rail keeps the whole menu visible */}
        <nav className="scroll-thin mt-3 flex flex-1 flex-col gap-1 overflow-y-auto overflow-x-hidden">
          {NAV.map((n) => {
            const active = page === n.key
            return (
              <button key={n.key} onClick={() => setPage(n.key)} title={n.label}
                aria-label={n.label}
                className={`group relative flex items-center rounded-xl py-2.5 text-left transition-all ${
                  menuOpen ? 'gap-3 px-2.5' : 'justify-center px-0'}`}
                style={{
                  background: active ? 'color-mix(in oklab, var(--sr-accent) 14%, transparent)' : 'transparent',
                  border: `1px solid ${active ? 'color-mix(in oklab, var(--sr-accent) 40%, transparent)' : 'transparent'}`,
                  cursor: 'pointer',
                }}>
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
                  style={{
                    color: active ? 'var(--sr-accent)' : 'var(--sr-dim)',
                    background: active ? undefined : 'transparent',
                  }}>{n.icon}</span>
                {/* collapsed = symbol only; the ⋯ expander brings the text back */}
                {menuOpen && (
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[0.82rem] font-medium">{n.label}</span>
                    <span className="block truncate text-[0.6rem] dim">{n.hint}</span>
                  </span>
                )}
                {active && <span className="absolute right-1 h-5 w-[2px] rounded-full"
                  style={{ background: 'var(--sr-accent)' }} />}
              </button>
            )
          })}
        </nav>

        <div className="flex flex-col gap-1.5 px-0.5 pb-1">
          {menuOpen ? (
            <>
              <Chip color={mode === 'live' ? 'var(--color-bull)'
                : mode === 'paper' ? 'var(--color-amber)' : 'var(--color-cyan)'}>
                {mode === 'sim' ? '◈ simulation' : mode === 'live' ? '● live' : '◐ paper'}
              </Chip>
              <Chip color={transport === 'binance' ? 'var(--color-bull)' : 'var(--color-amber)'}>
                {transport === 'binance' ? 'binance usdt-m' : 'sim feed'}
              </Chip>
            </>
          ) : (
            <span className="mx-auto inline-block h-2 w-2 rounded-full" title={
              `${mode === 'sim' ? 'simulation' : mode} · ${transport === 'binance' ? 'binance' : 'sim feed'}`
            } style={{
              background: mode === 'live' ? 'var(--color-bull)'
                : mode === 'paper' ? 'var(--color-amber)' : 'var(--color-cyan)',
            }} />
          )}
        </div>

        {!menuOpen && (
          <div className="mt-auto flex flex-col items-center gap-1.5 pt-2">
            <IconButton title={status?.paused ? 'Resume the engine' : 'Pause the engine'}
              onClick={() => act(() => endpoints.enginePause(!status?.paused),
                                 status?.paused ? 'Resumed' : 'Paused')} disabled={busy}>
              {status?.paused ? <Play size={14} /> : <Pause size={14} />}
            </IconButton>
            <IconButton title="Stop the engine"
              onClick={() => act(() => endpoints.engineStop(), 'Engine stopped')} disabled={busy}>
              <Square size={14} />
            </IconButton>
            <IconButton title="Flatten every open position"
              danger
              onClick={() => {
                if (confirm('Flatten EVERY open position at market? This is the panic button.')) {
                  act(() => endpoints.emergencyClose(), 'Emergency flatten sent')
                }
              }} disabled={busy}>
              <ShieldAlert size={14} />
            </IconButton>
            <span className="mt-1 inline-block h-1.5 w-1.5 rounded-full"
              title={connected ? 'stream live' : 'polling'}
              style={{ background: connected ? 'var(--color-bull)'
                : status ? 'var(--color-amber)' : 'var(--sr-dim)' }} />
          </div>
        )}

        <div className="mt-auto flex flex-col gap-2 px-1 pt-2"
          style={{ display: menuOpen ? undefined : 'none' }}>
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

          {/* the header carries *system* state only — money lives on the Account
              page now, and the page itself is whatever the menu opened */}
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
            <Chip color={critical ? 'var(--color-bear)' : warning ? 'var(--color-amber)'
              : notice ? 'var(--color-cyan)' : 'var(--color-bull)'}>
              {critical ? '☠ sos critical' : warning ? '⚠ degraded'
                : notice ? '◈ simulator' : '● all systems green'}
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
    </div>
  )
}

const IconButton: React.FC<{
  title: string
  onClick: () => void
  disabled?: boolean
  danger?: boolean
  children: React.ReactNode
}> = ({ title, onClick, disabled, danger, children }) => (
  <button title={title} aria-label={title} onClick={onClick} disabled={disabled}
    className="flex h-8 w-8 items-center justify-center rounded-xl transition-opacity hover:opacity-80"
    style={{
      border: `1px solid ${danger ? 'color-mix(in oklab, var(--color-bear) 45%, transparent)' : 'var(--sr-border)'}`,
      color: danger ? 'var(--color-bear)' : 'var(--sr-dim)',
      cursor: 'pointer',
    }}>
    {children}
  </button>
)
