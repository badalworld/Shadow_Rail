import React, { useEffect, useMemo, useState } from 'react'
import { Boxes, Map } from 'lucide-react'
import { BotMap3D } from '../components/BotMap3D'
import { HQ } from '../components/hq/HQ'
import type { HQSnapshot } from '../components/hq/HQ'
import { HQPanel } from '../components/hq/HQPanel'
import { DetailToggle, readDetail } from '../components/hq/DetailToggle'
import { RenderpeopleCredits } from '../components/hq/RenderpeopleCredits'
import { STATIONS } from '../components/hq/layout'
import type { Detail, StationKey } from '../components/hq/layout'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'
import type { PageKey } from '../components/Layout'

/**
 * The Command Deck is the 3D office — nothing else.
 *
 * The floor takes the whole page: the headquarters (or the compact swarm map),
 * the agents moving through the workflow, the live boards and the rail pulses.
 * Every number — starting balance, equity, open positions, realised P&L, fees,
 * win rate — lives on the Account page in the menu, and the rest of the app is in
 * the menu with it.
 */
export const Dashboard: React.FC<{ goTo?: (p: PageKey) => void }> = ({ goTo }) => {
  const { status, equity, stats, openTrades, bots, links, logs, scan, pulses, sos, pushToast } = useStore()
  const [selected, setSelected] = useState<string | null>(null)
  const [focus, setFocus] = useState<StationKey | null>(null)
  const [detail, setDetail] = useState<Detail>(() => readDetail())
  const [view, setView] = useState<'hq' | 'map'>('hq')
  const [blotter, setBlotter] = useState<number[]>([])

  const groups = useMemo(() => {
    const g: Record<string, { total: number; working: number }> = {}
    for (const b of bots) {
      g[b.group] = g[b.group] || { total: 0, working: 0 }
      g[b.group].total++
      if (b.status === 'working' || b.status === 'success' || b.status === 'celebrating') g[b.group].working++
    }
    return g
  }, [bots])

  const stationLoad = useMemo(
    () => STATIONS.map((st) => {
      const g = groups[st.group] || { total: 0, working: 0 }
      return { key: st.key, label: st.label, accent: st.accent, working: g.working, total: g.total }
    }),
    [groups])

  const winRate = stats?.win_rate ?? 0
  const working = bots.filter((b) => b.status === 'working').length
  const critic = sos.active && sos.level === 'critical'
  const trailing = openTrades.filter((t) => t.trail_active).length

  /* the podium/hologram curve is the closed-trade blotter, not a chart on the page */
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
  }, [stats?.total_trades])

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
    trades: stats?.total_trades,
    marginUsed: equity?.margin_used,
    marginBudget: equity?.margin_budget,
    trailArmed: trailing,
    drawdown: equity?.drawdown_pct,
    peak: equity?.peak_equity,
    riskLabel: status?.risk?.label,
    scanSeconds: scan.seconds_to_close,
    sosReasons: sos.reasons,
    groups: stationLoad.map((s) => ({ key: s.key, label: s.label, working: s.working, total: s.total })),
  }), [status, equity, stats, working, trailing, winRate, stationLoad, scan, sos])

  useEffect(() => {
    if (!selected) return
    const t = window.setTimeout(() => setSelected(null), 60_000)
    return () => window.clearTimeout(t)
  }, [selected])

  const selectedBot = selected ? bots.find((b) => b.bot_id === selected) : undefined

  return (
    <div className="relative h-full min-h-0">
      <div className="scroll-thin flex h-full min-h-0 flex-col overflow-hidden">
        {/* ── the floor controls: only what the room itself needs ────── */}
        <div className="mb-2 flex flex-wrap items-center gap-2">
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
        </div>

        {/* ── the office ────────────────────────────────────────────── */}
        <div className="relative min-h-0 flex-1 overflow-hidden rounded-xl">
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

          {/* the agent card, only when a body is clicked */}
          {selectedBot && (
            <HQPanel
              bot={selectedBot}
              className="absolute bottom-3 right-3"
              onClose={() => setSelected(null)}
              onFocus={(k) => { setView('hq'); setFocus(k) }}
              onOpenRoster={() => goTo?.('bots')}
            />
          )}

          {/* the human-reference credit stays with the room it belongs to */}
          <RenderpeopleCredits className="absolute right-3 top-3" />
        </div>
      </div>
    </div>
  )
}

export default Dashboard
