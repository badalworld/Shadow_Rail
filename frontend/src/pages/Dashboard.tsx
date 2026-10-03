import React, { useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { Activity, ArrowDownRight, ArrowUpRight, Brain, Cpu, Eye, Gauge, Radio, ShieldCheck, Wallet } from 'lucide-react'
import { useStore } from '../state/store'
import { endpoints } from '../lib/api'
import { Bar, Chip, Panel, Ring, Stat, clock, fmtMoney, fmtNum, fmtPct, timeAgo, statusColor } from '../components/Glass'
import { BotMap3D } from '../components/BotMap3D'
import { LineChart, MiniMeter } from '../components/Charts'
import type { Bot } from '../lib/types'

const STAGES: { key: string; label: string; icon: React.ReactNode }[] = [
  { key: 'connector', label: 'Connector', icon: <Radio size={13} /> },
  { key: 'scan', label: 'Scan ×5', icon: <Activity size={13} /> },
  { key: 'analyze', label: 'Analyse ×10', icon: <Brain size={13} /> },
  { key: 'execute', label: 'Execute', icon: <Cpu size={13} /> },
  { key: 'verify', label: 'Verify', icon: <ShieldCheck size={13} /> },
  { key: 'monitor', label: 'Monitor', icon: <Eye size={13} /> },
  { key: 'close', label: 'Journal', icon: <Wallet size={13} /> },
]

export const Dashboard: React.FC<{ goTo: (p: any) => void }> = ({ goTo }) => {
  const { status, bots, links, equity, stats, openTrades, logs, pulses, sos, scan, celebration } = useStore()
  const [selected, setSelected] = useState<string | null>(null)
  const [curve, setCurve] = useState<{ x: number; y: number }[]>([])
  const critical = sos.active && sos.level === 'critical'

  // real equity history (sampled by the Equity Manager), refreshed periodically
  useEffect(() => {
    let alive = true
    const pull = async () => {
      try {
        const res = await endpoints.equityCurve(400)
        if (!alive) return
        const pts = (res.points || []).map((p: any, i: number) => ({ x: i, y: p.equity }))
        const base = equity?.starting_balance ?? 0
        setCurve(pts.length > 1 ? pts : [{ x: 0, y: base }, { x: 1, y: equity?.equity ?? base }])
      } catch { /* keep the previous curve */ }
    }
    pull()
    const t = setInterval(pull, 20000)
    return () => { alive = false; clearInterval(t) }
  }, [equity?.starting_balance, equity?.equity])

  const equityPoints = curve.length ? curve : [{ x: 0, y: equity?.equity ?? 0 }]

  const groups = useMemo(() => {
    const g: Record<string, Bot[]> = {}
    bots.forEach((b) => { (g[b.group] ||= []).push(b) })
    return g
  }, [bots])

  const selectedBot = selected ? bots.find((b) => b.bot_id === selected) : null
  const workflow = status?.workflow
  const stageState = (k: string) => workflow?.stages?.[k]?.status || 'idle'

  const working = bots.filter((b) => b.status === 'working').length

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      {/* ── main numbers (exactly what the operator asked to see first) ── */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Starting Balance" value={fmtMoney(equity?.starting_balance)} locked
          sub={equity?.starting_at
            ? `locked ${timeAgo(equity.starting_at)} · ${equity.starting_source || 'binance'}`
            : 'locks on first connect'} />
        <Stat label="Current Equity" value={fmtMoney(equity?.equity)} tone="accent"
          sub={`${fmtPct(equity?.growth_pct)} vs start · avail ${fmtMoney(equity?.available)}`} />
        <Stat label="Open Positions" value={`${equity?.open_positions ?? 0} / ${status?.max_trades ?? 10}`}
          tone={(equity?.open_positions ?? 0) >= (status?.max_trades ?? 10) ? 'warn' : 'default'}
          sub={`margin used ${fmtMoney(equity?.margin_used)} · ${fmtMoney(equity?.margin_budget)}/trade`} />
        <Stat label="Released P&L" value={fmtMoney(equity?.released_pnl)}
          tone={(equity?.released_pnl ?? 0) >= 0 ? 'good' : 'bad'}
          sub={`unrealised ${fmtMoney(equity?.unrealized)}`} />
        <Stat label="Fees Paid" value={fmtMoney(equity?.fees_paid)} tone="warn"
          sub={`funding ${fmtMoney(-(equity?.funding_paid ?? 0))} net`} />
        <Stat label="Win Rate" value={`${(stats?.win_rate ?? 0).toFixed(1)}%`}
          sub={`${stats?.wins ?? 0}W / ${stats?.losses ?? 0}L · ${stats?.total_trades ?? 0} closed`} />
        <Stat label="Net After Costs" value={fmtMoney(equity?.net_after_costs)}
          tone={(equity?.net_after_costs ?? 0) >= 0 ? 'good' : 'bad'}
          sub={`PF ${(stats?.profit_factor ?? 0).toFixed(2)} · peak ${fmtMoney(equity?.peak_equity)}`} />
      </div>

      {/* ── workflow rail ── */}
      <Panel title="Bot Workflow — live pipeline" right={
        <div className="flex items-center gap-2 text-[0.65rem] dim">
          <span>cycle #{workflow?.cycle ?? 0}</span>
          <span>·</span>
          <span>{working} bots working</span>
          <span>·</span>
          <span>next candle in {Math.max(0, Math.round(scan.seconds_to_close))}s</span>
        </div>
      } bodyClass="p-3">
        <div className="flex flex-wrap items-stretch gap-2">
          {STAGES.map((s, i) => {
            const st = stageState(s.key)
            const color = st === 'done' ? 'var(--color-bull)'
              : st === 'start' ? 'var(--color-cyan)'
                : st === 'error' ? 'var(--color-bear)' : '#5b6b80'
            return (
              <React.Fragment key={s.key}>
                <motion.div
                  initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.03 }}
                  className="glass relative min-w-[8.5rem] flex-1 px-3 py-2"
                  style={{
                    borderColor: `color-mix(in oklab, ${color} 40%, transparent)`,
                    boxShadow: st === 'start' ? `0 0 22px ${color}44` : undefined,
                  }}>
                  <div className="flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-wider"
                    style={{ color }}>
                    {s.icon}{s.label}
                  </div>
                  <div className="mt-1 truncate text-[0.68rem] dim">
                    {workflow?.stages?.[s.key]?.detail || 'waiting'}
                  </div>
                  {st === 'start' && <div className="flow-line mt-1.5 h-[2px] w-full rounded-full" />}
                </motion.div>
                {i < STAGES.length - 1 && (
                  <div className="hidden items-center lg:flex">
                    <div className="h-[1px] w-4"
                      style={{ background: st === 'done' ? 'var(--color-bull)' : 'var(--sr-border)' }} />
                  </div>
                )}
              </React.Fragment>
            )
          })}
        </div>
      </Panel>

      {/* ── 3D swarm + side panels ── */}
      <div className="grid min-h-0 grid-cols-1 gap-3 xl:grid-cols-[2.1fr_1fr]">
        <Panel
          title="3D Bot Swarm — click a node for details"
          right={<div className="flex gap-1.5">
            <Chip color="var(--color-cyan)">{links.length} rails</Chip>
            <Chip color="var(--color-bull)">{bots.length} bots</Chip>
            <Chip color={critical ? 'var(--color-bear)' : 'var(--color-bull)'}>
              {critical ? 'sos' : 'nominal'}
            </Chip>
          </div>}
          bodyClass="relative"
          className="min-h-[26rem]"
          accent={critical ? 'var(--color-bear)' : undefined}>
          <div className="absolute inset-0">
            <BotMap3D bots={bots} links={links} pulses={pulses} danger={critical}
              onSelect={setSelected} className="h-full w-full" />
          </div>
          <div className="pointer-events-none absolute bottom-3 right-3 max-w-[16rem] text-[0.6rem] leading-relaxed dim">
            Pulses show live data flow: Connector → CEO → Scanners → Analysts → Execution →
            Verify → Monitors → Journal → Equity.
          </div>
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel title="Swarm groups" bodyClass="p-3" className="shrink-0">
            <div className="grid grid-cols-2 gap-2.5">
              {Object.entries(groups).map(([g, list]) => {
                const busy = list.filter((b) => b.status === 'working').length
                const pct = (busy / list.length) * 100
                return (
                  <div key={g} className="glass-solid px-2.5 py-2">
                    <div className="flex items-center justify-between text-[0.62rem] uppercase tracking-wider dim">
                      <span>{g}</span><span className="mono">{busy}/{list.length}</span>
                    </div>
                    <div className="mt-1.5"><MiniMeter value={pct}
                      color={busy ? 'var(--color-cyan)' : '#3f5468'} /></div>
                    <div className="mt-1 truncate text-[0.6rem] dim">
                      {list.slice(0, 2).map((b) => b.name).join(' · ')}{list.length > 2 ? ' …' : ''}
                    </div>
                  </div>
                )
              })}
            </div>
          </Panel>

          <Panel title={selectedBot ? `${selectedBot.name} · ${selectedBot.role}` : 'Bot inspector'}
            bodyClass="p-3" className="min-h-[12rem] flex-1"
            right={selectedBot && <Chip color={statusColor(selectedBot.status)}>{selectedBot.status}</Chip>}>
            {selectedBot ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2 text-[0.7rem]">
                  <span className="chip" style={{ color: statusColor(selectedBot.status) }}>
                    {selectedBot.rank}
                  </span>
                  <span className="dim">score {selectedBot.metrics.score.toFixed(1)}</span>
                  <span className="dim">· {selectedBot.metrics.tasks_done} tasks</span>
                </div>
                <div className="text-[0.72rem]">{selectedBot.task}</div>
                <div className="text-[0.68rem] dim">{selectedBot.message}</div>
                <Bar value={selectedBot.progress * 100} color={statusColor(selectedBot.status)} />
                <div className="grid grid-cols-2 gap-2 text-[0.62rem] dim">
                  <div>api window: <span className="mono">{selectedBot.metrics.api_spent_window}</span></div>
                  <div>latency: <span className="mono">{selectedBot.metrics.avg_latency_ms.toFixed(0)}ms</span></div>
                  <div>wins: <span className="mono">{selectedBot.metrics.wins}</span></div>
                  <div>errors: <span className="mono">{selectedBot.metrics.errors}</span></div>
                </div>
                <div className="mt-1 text-[0.62rem] dim">
                  {selectedBot.assigned.length
                    ? <>watching: <span className="mono">{selectedBot.assigned.slice(0, 8).join(', ')}
                      {selectedBot.assigned.length > 8 ? ` +${selectedBot.assigned.length - 8}` : ''}</span></>
                    : 'no assets assigned'}
                </div>
                <button className="chip mt-1 justify-center hover:opacity-80"
                  onClick={() => goTo('bots')}>open roster →</button>
              </div>
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                <Gauge size={26} className="dim" />
                <div className="text-[0.7rem] dim">Click any node in the 3D swarm<br />to inspect that bot live.</div>
              </div>
            )}
          </Panel>
        </div>
      </div>

      {/* ── live positions + equity + logs ── */}
      <div className="grid min-h-[17rem] grid-cols-1 gap-3 xl:grid-cols-[1.5fr_1fr_1fr]">
        <Panel title={`Open positions ${openTrades.length}/${status?.max_trades ?? 10}`}
          right={<Chip color="var(--color-cyan)">{openTrades.length ? 'live' : 'flat'}</Chip>}
          bodyClass="scroll-thin overflow-auto">
          {openTrades.length === 0 ? (
            <div className="flex h-full items-center justify-center text-[0.72rem] dim">
              Flat — scanners are hunting the next confirmed flip
            </div>
          ) : (
            <table className="w-full text-[0.7rem]">
              <thead className="sticky top-0 text-[0.6rem] uppercase tracking-wider dim"
                style={{ background: 'rgba(5,7,12,0.8)' }}>
                <tr>
                  <th className="px-3 py-1.5 text-left">Symbol</th>
                  <th className="text-left">Side</th>
                  <th className="text-right">Entry</th>
                  <th className="text-right">Mark</th>
                  <th className="text-right">uPnL</th>
                  <th className="text-right">SL / TP</th>
                  <th className="text-right">Liq</th>
                  <th className="px-3 text-right">Bot</th>
                </tr>
              </thead>
              <tbody>
                {openTrades.map((t) => {
                  const up = (t.unrealized ?? 0) >= 0
                  return (
                    <tr key={t.id} className="glass-row border-t" style={{ borderColor: 'var(--sr-border)' }}>
                      <td className="px-3 py-1.5 font-semibold">{t.symbol}</td>
                      <td>
                        <span className="inline-flex items-center gap-1"
                          style={{ color: t.side === 'LONG' ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                          {t.side === 'LONG' ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                          {t.side}
                        </span>
                      </td>
                      <td className="mono text-right">{fmtNum(t.entry_price)}</td>
                      <td className="mono text-right">{fmtNum(t.mark ?? t.entry_price)}</td>
                      <td className="mono text-right" style={{ color: up ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                        {fmtMoney(t.unrealized)} ({fmtPct(t.unrealized_pct)})
                      </td>
                      <td className="mono text-right dim">
                        {fmtNum(t.sl_price)} / {t.tp_price ? fmtNum(t.tp_price) : 'flip'}
                      </td>
                      <td className="mono text-right dim">{fmtNum(t.liquidation_live ?? t.liquidation_price)}</td>
                      <td className="px-3 text-right text-[0.62rem] dim">{t.monitor_id || t.monitor_bot_id || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Equity" right={<Chip>{fmtPct(equity?.growth_pct)}</Chip>} bodyClass="p-3">
          <div className="flex h-[6.5rem] items-center">
            <LineChart data={equityPoints} baseline={equity?.starting_balance}
              color={(equity?.equity ?? 0) >= (equity?.starting_balance ?? 0)
                ? 'var(--color-bull)' : 'var(--color-bear)'} />
          </div>
          <div className="mt-2 grid grid-cols-2 gap-2 text-[0.65rem]">
            <div className="dim">equity <span className="mono accent-text">{fmtMoney(equity?.equity)}</span></div>
            <div className="dim">start <span className="mono">{fmtMoney(equity?.starting_balance)}</span></div>
            <div className="dim">drawdown <span className="mono">{fmtPct(-(equity?.drawdown_pct ?? 0))}</span></div>
            <div className="dim">slots <span className="mono">{equity?.open_slots ?? 0} free</span></div>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Ring value={stats?.win_rate ?? 0} size={62} label={
              <span className="mono text-[0.68rem]">{(stats?.win_rate ?? 0).toFixed(0)}%</span>
            } />
            <div className="text-[0.62rem] leading-relaxed dim">
              <div><span className="mono">{stats?.wins ?? 0}</span> wins ·
                <span className="mono"> {stats?.losses ?? 0}</span> losses</div>
              <div>avg win <span className="mono">{fmtMoney(stats?.avg_win)}</span></div>
              <div>avg loss <span className="mono">{fmtMoney(stats?.avg_loss)}</span></div>
              <div>best <span className="mono accent-text">{stats?.best_symbol || '—'}</span></div>
            </div>
          </div>
        </Panel>

        <Panel title="Workflow log" right={<Chip>{logs.length}</Chip>}
          bodyClass="scroll-thin overflow-auto p-0">
          <div className="flex flex-col">
            {logs.slice(0, 60).map((l, i) => (
              <div key={l.id ?? i}
                className="glass-row flex items-start gap-2 border-b px-3 py-1.5 text-[0.66rem]"
                style={{ borderColor: 'rgba(255,255,255,0.04)' }}>
                <span className="mono shrink-0 dim">{clock(l.ts)}</span>
                <span className="shrink-0 font-semibold" style={{
                  color: l.level === 'error' || l.level === 'sos' ? 'var(--color-bear)'
                    : l.level === 'warn' ? 'var(--color-amber)'
                      : l.level === 'success' ? 'var(--color-bull)' : 'var(--sr-accent)',
                }}>{l.bot_id}</span>
                <span className="min-w-0 flex-1 truncate" title={l.message}>{l.message}</span>
              </div>
            ))}
            {!logs.length && <div className="p-6 text-center text-[0.7rem] dim">no log lines yet</div>}
          </div>
        </Panel>
      </div>
    </div>
  )
}
