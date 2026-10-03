import React, { useEffect, useMemo, useState } from 'react'
import {
  Activity, ArrowDownRight, ArrowUpRight, Coins, Gauge, Lock, Receipt, TrendingUp, Users, Zap,
} from 'lucide-react'
import { Panel, Stat, Chip, Bar, moodEmoji, fmtMoney, fmtNum, fmtPct } from '../components/Glass'
import { BotMap3D } from '../components/BotMap3D'
import { useStore } from '../state/store'
import type { PageKey } from '../components/Layout'

/**
 * The Command Deck: one thing only — the 3D bot work zone with the six numbers
 * the operator must never lose sight of.  Everything else lives in the menu
 * (Open Positions, Scan, Closed Trades, Roster, Logs, Settings).
 */
export const Dashboard: React.FC<{ goTo?: (p: PageKey) => void }> = ({ goTo }) => {
  const { status, equity, stats, openTrades, bots, links, logs, scan, pulses, sos } = useStore()
  const [selected, setSelected] = useState<string | null>(null)

  const wf = status?.workflow?.stages || {}
  const groups = useMemo(() => {
    const g: Record<string, { total: number; working: number }> = {}
    for (const b of bots) {
      g[b.group] = g[b.group] || { total: 0, working: 0 }
      g[b.group].total++
      if (b.status === 'working' || b.status === 'success' || b.status === 'celebrating') g[b.group].working++
    }
    return g
  }, [bots])

  const winRate = stats?.win_rate ?? 0
  const total = stats?.total_trades ?? 0
  const working = bots.filter((b) => b.status === 'working').length
  const risk = status?.risk
  const critic = sos.active && sos.level === 'critical'
  const trailing = openTrades.filter((t) => t.trail_active).length
  const pulsesThisCycle = pulses.length
  const nameOf = (id: string) =>
    bots.find((b) => b.bot_id === id)?.name || id.replace(/-bot$/, '').replace(/-/g, ' ').toUpperCase()

  // the newest engine line drives the "what is the swarm doing" ticker
  const activity = useMemo(() => {
    const line = logs?.[0]
    if (!line) return 'waiting for the first engine cycle…'
    return `${line.bot_id || line.level}: ${line.message}`
  }, [logs])

  useEffect(() => {
    if (!selected) return
    const t = window.setTimeout(() => setSelected(null), 45_000)
    return () => window.clearTimeout(t)
  }, [selected])

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      {/* ── the work zone ─────────────────────────────────────────────── */}
      <Panel
        title="Bot Work Zone — live 3D swarm"
        right={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Chip>cycle #{status?.cycle ?? 0}</Chip>
            <Chip color="var(--color-cyan)">{working} working · {pulsesThisCycle} pulses</Chip>
            {trailing > 0 && <Chip color="var(--color-cyan)">🔒 {trailing} trailing</Chip>}
            <Chip color="var(--color-amber)">
              bar closes in {Math.max(0, Math.round(scan.seconds_to_close || 0))}s
            </Chip>
          </div>
        }
        bodyClass="p-2"
      >
        <div className="relative h-[calc(100vh-21rem)] min-h-[30rem] w-full overflow-hidden rounded-xl">
          <BotMap3D
            bots={bots}
            links={links}
            pulses={pulses}
            danger={critic}
            onSelect={setSelected}
            className="h-full w-full"
          />

          {/* live activity tickers (pure bot activity — no charts here) */}
          <div className="pointer-events-none absolute left-3 top-3 max-w-[46%]">
            <p className="text-[0.58rem] uppercase tracking-[0.18em] dim">swarm activity</p>
            <p className="mono truncate text-[0.7rem]" title={activity}>{activity}</p>
            {pulses.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {pulses.slice(0, 4).map((p) => (
                  <span key={p.id} className="chip" style={{ fontSize: '0.56rem' }}>
                    {nameOf(p.from)} → {nameOf(p.to)}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* team load — the only overlay the operator asks for mid-flight */}
          <div className="absolute right-3 top-14 flex w-[13.5rem] flex-col gap-1">
            {risk?.label && <Chip>{risk.label}</Chip>}
            {Object.entries(groups).map(([g, v]) => (
              <button
                key={g}
                onClick={() => goTo?.('bots')}
                className="glass-row flex items-center justify-between px-2 py-1 text-left"
                style={{ cursor: 'pointer' }}
                title={`${g}: ${v.working}/${v.total} active`}
              >
                <span className="flex items-center gap-1.5 text-[0.62rem] uppercase tracking-wider dim">
                  <Users size={10} /> {g}
                </span>
                <span className="mono text-[0.68rem]">
                  <span style={{ color: v.working ? 'var(--color-cyan)' : 'var(--sr-text)' }}>{v.working}</span>
                  <span className="dim">/{v.total}</span>
                </span>
              </button>
            ))}
          </div>

          {/* bot inspector — floats over the zone, never pushes the layout */}
          {selected && (
            <div className="glass-solid absolute bottom-3 right-3 max-w-[22rem] p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-[0.6rem] uppercase tracking-wider dim">bot activity</span>
                <button className="chip" style={{ cursor: 'pointer' }}
                  onClick={() => setSelected(null)}>close</button>
              </div>
              {(() => {
                const b = bots.find((x) => x.bot_id === selected)
                if (!b) {
                  return (
                    <p className="pt-1 text-[0.7rem] dim">
                      That rail is a whole team — open the Bot Roster for its members.
                    </p>
                  )
                }
                const stage = wf[stageFor(b.group)]?.detail || ''
                return (
                  <div className="pt-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xl">{moodEmoji(b.mood)}</span>
                      <div>
                        <p className="mono text-[0.82rem]">{b.name}</p>
                        <p className="text-[0.64rem] dim">{b.role} · {b.rank}</p>
                      </div>
                    </div>
                    <div className="mt-2">
                      <Bar value={b.progress} color={b.color} glow />
                      <p className="pt-1 text-[0.68rem]">{b.task || b.message || b.status}</p>
                      {stage && <p className="text-[0.62rem] dim">stage: {stage}</p>}
                    </div>
                    <div className="mono mt-2 flex gap-3 text-[0.62rem] dim">
                      <span>score {Math.round(b.metrics?.score ?? 0)}</span>
                      <span>tasks {b.metrics?.tasks_done ?? 0}</span>
                      <span style={{ color: 'var(--color-bull)' }}>w {b.metrics?.wins ?? 0}</span>
                      <span style={{ color: 'var(--color-bear)' }}>e {b.metrics?.errors ?? 0}</span>
                      <span>api {b.metrics?.api_spent_window ?? 0}</span>
                    </div>
                  </div>
                )
              })()}
            </div>
          )}
        </div>
      </Panel>

      {/* ── the six numbers ───────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Starting Balance"
          value={fmtMoney(equity?.starting_balance)}
          locked={equity?.starting_locked}
          icon={<Lock size={13} />}
          tone="accent"
          sub={equity?.starting_at
            ? `locked ${new Date(equity.starting_at).toLocaleString()}`
            : 'locks on the first connection'}
        />
        <Stat
          label="Current Equity"
          value={fmtMoney(equity?.equity)}
          icon={<Coins size={13} />}
          tone={(equity?.growth_pct ?? 0) >= 0 ? 'good' : 'bad'}
          sub={`${fmtPct(equity?.growth_pct)} since start · free ${fmtMoney(equity?.available)}`}
        />
        <Stat
          label="Opened Positions"
          value={`${equity?.open_positions ?? 0} / ${equity?.max_trades ?? 10}`}
          icon={<Activity size={13} />}
          tone="warn"
          sub={trailing > 0
            ? `${trailing} protected by the ROI trail`
            : `margin ${fmtMoney(equity?.margin_used)} of ${fmtMoney(equity?.margin_budget)}`}
        />
        <Stat
          label="Realised P&L"
          value={fmtMoney(equity?.released_pnl)}
          icon={<TrendingUp size={13} />}
          tone={(equity?.released_pnl ?? 0) >= 0 ? 'good' : 'bad'}
          sub={`unrealised ${fmtMoney(equity?.unrealized)}`}
        />
        <Stat
          label="Fees Paid"
          value={fmtMoney(equity?.fees_paid_total ?? equity?.fees_paid)}
          icon={<Receipt size={13} />}
          tone="bad"
          sub={`incl. ${fmtMoney(equity?.open_entry_fees)} on open positions · funding ${fmtMoney(equity?.funding_net)} net`}
        />
        <Stat
          label="Win Rate"
          value={`${winRate.toFixed(1)}%`}
          icon={<Gauge size={13} />}
          tone={winRate >= 50 ? 'good' : 'warn'}
          sub={`${stats?.wins ?? 0}W / ${stats?.losses ?? 0}L · ${total} closed · PF ${fmtNum(stats?.profit_factor, 2)}`}
        />
      </div>
    </div>
  )
}

function stageFor(group: string): string {
  switch (group) {
    case 'core': return 'connector'
    case 'scanner': return 'scan'
    case 'analyst': return 'analyze'
    case 'execution': return 'execute'
    case 'verify': return 'verify'
    case 'monitor': return 'monitor'
    default: return 'close'
  }
}

export const _unused = { ArrowUpRight, ArrowDownRight, Zap }
