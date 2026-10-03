import React, { useEffect, useMemo, useState } from 'react'
import {
  Activity, Boxes, Coins, Gauge, Lock, Map, Receipt, TrendingUp, Users,
} from 'lucide-react'
import { Panel, Stat, Chip, Bar, moodEmoji, fmtMoney, fmtNum, fmtPct } from '../components/Glass'
import { BotMap3D } from '../components/BotMap3D'
import { HQ } from '../components/hq/HQ'
import type { HQSnapshot } from '../components/hq/HQ'
import { HQPanel } from '../components/hq/HQPanel'
import { DetailToggle, readDetail } from '../components/hq/DetailToggle'
import { RenderpeopleCredits } from '../components/hq/RenderpeopleCredits'
import { STATIONS, STATION_BY_GROUP, stationOf } from '../components/hq/layout'
import type { Detail, StationKey } from '../components/hq/layout'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'
import type { PageKey } from '../components/Layout'

/**
 * The Command Deck: one thing on top — the 3D AI Trading Bot Headquarters, a
 * real trading floor where all 29 agents have a body, a desk and a job — and
 * below it exactly the six numbers the operator must never lose sight of.
 * Everything else lives in the menu (Open Positions, Scan, Closed Trades,
 * Roster, Logs, Settings).
 */
export const Dashboard: React.FC<{ goTo?: (p: PageKey) => void }> = ({ goTo }) => {
  const { status, equity, stats, openTrades, bots, links, logs, scan, pulses, sos, pushToast } = useStore()
  const [selected, setSelected] = useState<string | null>(null)
  const [focus, setFocus] = useState<StationKey | null>(null)
  const [detail, setDetail] = useState<Detail>(() => readDetail())
  const [view, setView] = useState<'hq' | 'map'>('hq')
  const [blotter, setBlotter] = useState<number[]>([])

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

  /* ordered station list for the team-load column + the snapshot the HQ draws */
  const stationLoad = useMemo(
    () => STATIONS.map((st) => {
      const g = groups[st.group] || { total: 0, working: 0 }
      return { key: st.key, label: st.label, accent: st.accent, working: g.working, total: g.total }
    }),
    [groups])

  const winRate = stats?.win_rate ?? 0
  const total = stats?.total_trades ?? 0
  const working = bots.filter((b) => b.status === 'working').length
  const risk = status?.risk
  const critic = sos.active && sos.level === 'critical'
  const trailing = openTrades.filter((t) => t.trail_active).length
  const pulsesThisCycle = pulses.length
  const nameOf = (id: string) =>
    bots.find((b) => b.bot_id === id)?.name || id.replace(/-bot$/, '').replace(/-/g, ' ').toUpperCase()

  const snapshot: HQSnapshot = useMemo(() => ({
    cycle: status?.cycle,
    stage: status?.workflow?.stage,
    working,
    open: equity?.open_positions ?? 0,
    max: equity?.max_trades ?? status?.max_trades ?? 10,
    equity: equity?.equity,
    starting: equity?.starting_balance,
    released: equity?.released_pnl,
    unrealized: equity?.unrealized,
    fees: equity?.fees_paid_total ?? equity?.fees_paid,
    winRate,
    trades: total,
    marginUsed: equity?.margin_used,
    marginBudget: equity?.margin_budget,
    trailArmed: trailing,
    drawdown: equity?.drawdown_pct,
    peak: equity?.peak_equity,
    riskLabel: risk?.label,
    scanSeconds: scan.seconds_to_close,
    sosReasons: sos.reasons,
    groups: stationLoad.map((s) => ({ key: s.key, label: s.label, working: s.working, total: s.total })),
  }), [status, equity, stats, working, trailing, winRate, total, stationLoad, risk, scan, sos])

  /* the hologram + wall boards are drawn from the closed-trade blotter */
  useEffect(() => {
    let dead = false
    const load = async () => {
      try {
        const d = await endpoints.closedTrades({ limit: 80 })
        const rows = (d.trades || []).slice().sort((a: any, b: any) => (a.closed_at || 0) - (b.closed_at || 0))
        let cum = 0
        const out: number[] = [0]
        for (const r of rows) { cum += Number(r.net_pnl || 0); out.push(cum) }
        if (!dead) setBlotter(out)
      } catch { /* the hologram keeps its placeholder */ }
    }
    load()
    const t = window.setInterval(load, 30_000)
    return () => { dead = true; window.clearInterval(t) }
  }, [total])

  // the newest engine line drives the "what is the swarm doing" ticker
  const activity = useMemo(() => {
    const line = logs?.[0]
    if (!line) return 'waiting for the first engine cycle…'
    return `${line.bot_id || line.level}: ${line.message}`
  }, [logs])

  useEffect(() => {
    if (!selected) return
    const t = window.setTimeout(() => setSelected(null), 60_000)
    return () => window.clearTimeout(t)
  }, [selected])

  const activeStage = status?.workflow?.stage || ''
  const activeKey = STATIONS.find((s) => s.stage === activeStage)?.key || null
  const selectedBot = selected ? bots.find((b) => b.bot_id === selected) : undefined

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      {/* ── the headquarters ───────────────────────────────────────────── */}
      <Panel
        title="Bot Work Zone — live 3D headquarters"
        right={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="glass-row flex items-center gap-0.5 p-0.5">
              {([['hq', 'headquarters', <Boxes size={11} key="b" />],
                ['map', 'swarm map', <Map size={11} key="m" />]] as const).map(([k, label, icon]) => {
                const on = view === k
                return (
                  <button key={k} onClick={() => setView(k as 'hq' | 'map')}
                    className="flex items-center gap-1 rounded-md px-2 py-[3px] text-[0.58rem] uppercase tracking-[0.14em]"
                    style={{
                      cursor: 'pointer',
                      color: on ? 'var(--color-cyan)' : 'var(--sr-dim)',
                      background: on ? 'rgba(34,211,238,0.12)' : 'transparent',
                      border: on ? '1px solid rgba(34,211,238,0.35)' : '1px solid transparent',
                    }}>
                    {icon} {label}
                  </button>
                )
              })}
            </div>
            <DetailToggle value={detail} onChange={setDetail} />
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
        <div className="relative h-[calc(100vh-17rem)] min-h-[34rem] w-full overflow-hidden rounded-xl">
          {view === 'hq' ? (
            <HQ
              bots={bots}
              pulses={pulses}
              snapshot={snapshot}
              detail={detail}
              danger={critic}
              selected={selected}
              focus={focus}
              curve={blotter}
              className="h-full w-full"
              onSelect={setSelected}
              onFocus={setFocus}
              onScan={() => {
                endpoints.runCycle()
                  .then(() => pushToast({
                    kind: 'info', title: 'scan cycle ordered',
                    body: 'the CEO sent every scanner bot back over its 30 assets',
                  }))
                  .catch((e) => pushToast({
                    kind: 'error', title: 'scan cycle refused',
                    body: String(e?.message || e),
                  }))
              }}
            />
          ) : (
            <BotMap3D
              bots={bots}
              links={links}
              pulses={pulses}
              danger={critic}
              onSelect={setSelected}
              className="h-full w-full"
            />
          )}

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

          {/* team load — click a station to fly the camera there */}
          <div className="absolute right-3 top-14 flex w-[13.5rem] flex-col gap-1">
            {risk?.label && <Chip>{risk.label}</Chip>}
            {stationLoad.map((st) => (
              <button
                key={st.key}
                onClick={() => { setView('hq'); setFocus(focus === st.key ? null : st.key) }}
                className="glass-row flex items-center justify-between px-2 py-1 text-left"
                style={{
                  cursor: 'pointer',
                  borderColor: focus === st.key ? st.accent : undefined,
                  opacity: st.total ? 1 : 0.45,
                }}
                title={`${st.label}: ${st.working}/${st.total} active · click to fly there`}
              >
                <span className="flex items-center gap-1.5 text-[0.62rem] uppercase tracking-wider dim">
                  <Users size={10} /> {st.label}
                </span>
                <span className="mono text-[0.68rem]">
                  <span style={{ color: st.working ? st.accent : 'var(--sr-text)' }}>{st.working}</span>
                  <span className="dim">/{st.total}</span>
                </span>
              </button>
            ))}
            <button
              onClick={() => goTo?.('bots')}
              className="glass-row px-2 py-1 text-left text-[0.6rem] uppercase tracking-wider dim"
              style={{ cursor: 'pointer' }}
              title="Open the full roster"
            >
              bot roster →
            </button>
          </div>

          {/* the pipeline rail, stage by stage */}
          <div className="pointer-events-none absolute left-3 top-24 hidden w-[13.5rem] flex-col gap-1 xl:flex">
            <p className="text-[0.58rem] uppercase tracking-[0.18em] dim">workflow rail</p>
            {STATIONS.map((st) => {
              const on = activeKey === st.key
              const stage = wf[st.stage]
              const bad = stage?.status === 'error' || stage?.status === 'blocked'
              const value = stage
                ? (stage.status === 'success' ? 100
                  : stage.status === 'working' ? 62
                    : stage.status === 'error' || stage.status === 'blocked' ? 100 : 8)
                : 0
              return (
                <div key={st.key} className="flex items-center gap-2" title={stage?.detail || st.label}>
                  <span className="h-[6px] w-[6px] rounded-full"
                    style={{ background: on ? st.accent : 'rgba(255,255,255,0.18)',
                      boxShadow: on ? `0 0 10px ${st.accent}` : 'none' }} />
                  <span className="mono w-[5.2rem] truncate text-[0.58rem] uppercase tracking-wider"
                    style={{ color: on ? st.accent : undefined }}>
                    {st.label.split(' ')[0]}
                  </span>
                  <span className="flex-1"><Bar value={value}
                    color={bad ? 'var(--color-bear)' : st.accent} height={3} glow={on} /></span>
                </div>
              )
            })}
            <RenderpeopleCredits className="pointer-events-auto mt-1 self-start" />
          </div>

          {/* bot inspector — floats over the zone, never pushes the layout */}
          {selectedBot && (
            <HQPanel
              bot={selectedBot}
              className="absolute bottom-3 right-3"
              onClose={() => setSelected(null)}
              onFocus={(k) => { setView('hq'); setFocus(k) }}
            />
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
            : `margin ${fmtMoney(equity?.margin_used)} used · ${fmtMoney(equity?.margin_budget)} per trade`}
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

      {/* the mood ticker: who is celebrating, who is down */}
      {bots.some((b) => b.mood === 'excited' || b.mood === 'sad') && (
        <div className="flex flex-wrap items-center gap-2 pb-1">
          <span className="text-[0.58rem] uppercase tracking-[0.18em] dim">floor mood</span>
          {bots.filter((b) => b.mood === 'excited' || b.mood === 'sad').slice(0, 10).map((b) => (
            <button key={b.bot_id} className="chip" style={{ cursor: 'pointer' }}
              onClick={() => { setView('hq'); setSelected(b.bot_id); setFocus(STATION_BY_GROUP[b.group] || null) }}
              title={b.message || b.task}>
              {moodEmoji(b.mood)} {b.name} · {stationOf(STATION_BY_GROUP[b.group] || 'command').label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export default Dashboard
