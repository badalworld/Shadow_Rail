import React, { useEffect, useMemo, useState } from 'react'
import {
  Activity, ArrowDownRight, ArrowUpRight, Coins, Gauge, Lock, Radio, Receipt,
  ShieldCheck, TrendingUp, Users, Zap,
} from 'lucide-react'
import { Panel, Stat, Chip, Bar, statusColor, moodEmoji, fmtMoney, fmtNum, fmtPct, clock } from '../components/Glass'
import { Donut, LineChart } from '../components/Charts'
import { BotMap3D } from '../components/BotMap3D'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'
import type { PageKey } from '../components/Layout'

const STAGES: { key: string; label: string; bots: string }[] = [
  { key: 'connector', label: 'Connector', bots: 'ORACLE' },
  { key: 'scan', label: 'Scan ×5', bots: 'VEGA · NOVA · ORION · LYRA · ATLAS' },
  { key: 'analyze', label: 'Analyse ×10', bots: 'EINSTEIN … BOHR' },
  { key: 'execute', label: 'Execute ×2', bots: 'BOLT · TITAN' },
  { key: 'verify', label: 'Verify', bots: 'ECHO' },
  { key: 'monitor', label: 'Monitor ×4', bots: 'SENTINEL · WARDEN · WATCHMAN · GUARDIAN' },
  { key: 'close', label: 'Journal', bots: 'LEDGER · BANKER · AEGIS' },
]

const MiniStat: React.FC<{ label: string; value: string; tone?: 'bull' | 'bear' }> = ({ label, value, tone }) => (
  <div>
    <p className="text-[0.58rem] uppercase tracking-wider dim">{label}</p>
    <p className="mono text-[0.78rem]" style={{
      color: tone === 'bull' ? 'var(--color-bull)' : tone === 'bear' ? 'var(--color-bear)' : undefined,
    }}>{value}</p>
  </div>
)

function levelColor(level: string): string {
  switch (level) {
    case 'error': return 'var(--color-bear)'
    case 'warn': return 'var(--color-amber)'
    case 'success': return 'var(--color-bull)'
    case 'sos': return '#ff2b4e'
    default: return 'var(--sr-dim)'
  }
}

export const Dashboard: React.FC<{ goTo?: (p: PageKey) => void }> = ({ goTo }) => {
  const { status, equity, stats, openTrades, bots, links, logs, scan, pulses, sos } = useStore()
  const [selected, setSelected] = useState<string | null>(null)
  const [curve, setCurve] = useState<{ x: number; y: number }[]>([])

  // real equity curve (chronological), refreshed while the page is open
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await endpoints.equityCurve(400)
        if (!alive) return
        const pts = (res.points || res.curve || []).map((p: any) => ({ x: p.ts, y: p.equity }))
        setCurve(pts)
      } catch { /* the socket will retry */ }
    }
    load()
    const t = window.setInterval(load, 20_000)
    return () => { alive = false; window.clearInterval(t) }
  }, [])

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

  return (
    <div className="flex flex-col gap-3">
      {/* ── headline numbers ─────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Starting Balance"
          value={fmtMoney(equity?.starting_balance)}
          locked={equity?.starting_locked}
          icon={<Lock size={13} />}
          tone="accent"
          sub={equity?.starting_at
            ? `locked ${new Date(equity.starting_at).toLocaleString()} · ${equity.starting_source || ''}`
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
          label="Open Positions"
          value={`${equity?.open_positions ?? 0} / ${equity?.max_trades ?? 10}`}
          icon={<Activity size={13} />}
          tone="warn"
          sub={`margin ${fmtMoney(equity?.margin_used)} of ${fmtMoney(equity?.margin_budget)}`}
        />
        <Stat
          label="Released P&L"
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

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[1.55fr_1fr]">
        {/* ── 3D swarm ─────────────────────────────────────────────── */}
        <Panel
          title="Bot Workflow — live 3D pipeline"
          right={
            <div className="flex flex-wrap items-center gap-2">
              <Chip>cycle #{status?.cycle ?? 0}</Chip>
              <Chip color="var(--color-cyan)">{working} bots working</Chip>
              <Chip color="var(--color-amber)">
                candle closes in {Math.max(0, Math.round(scan.seconds_to_close || 0))}s
              </Chip>
            </div>
          }
          bodyClass="p-2"
        >
          <div className="h-[22rem] w-full overflow-hidden rounded-xl md:h-[26rem]">
            <BotMap3D
              bots={bots}
              links={links}
              pulses={pulses}
              danger={critic}
              onSelect={setSelected}
              className="h-full w-full"
            />
          </div>
          <div className="grid grid-cols-2 gap-2 px-1 pt-2 md:grid-cols-4">
            {Object.entries(groups).map(([g, v]) => (
              <button
                key={g}
                onClick={() => goTo?.('bots')}
                className="glass-row flex items-center justify-between px-2 py-1.5 text-left transition-transform hover:scale-[1.02]"
                style={{ cursor: 'pointer' }}
              >
                <span className="flex items-center gap-1.5 text-[0.68rem] uppercase tracking-wider dim">
                  <Users size={11} /> {g}
                </span>
                <span className="mono text-[0.72rem]">
                  <span style={{ color: v.working ? 'var(--color-cyan)' : 'var(--sr-text)' }}>{v.working}</span>
                  <span className="dim">/{v.total}</span>
                </span>
              </button>
            ))}
          </div>
          <p className="px-1 pt-2 text-[0.65rem] dim">
            Pulses travel the rails on every real event: Connector → CEO → Scanners → Analysts →
            Execution → Verify → Monitor → Journal → Equity Manager.
          </p>
        </Panel>

        <div className="flex flex-col gap-3">
          {/* ── workflow rail ─────────────────────────────────────── */}
          <Panel title="Pipeline stages" right={<Chip>{risk?.label || ''}</Chip>}>
            <div className="flex flex-col gap-1.5">
              {STAGES.map((s, i) => {
                const st = wf[s.key] || { status: 'idle', detail: '' }
                const color = statusColor(st.status === 'done' ? 'success' : st.status === 'error' ? 'error' : st.status)
                const active = st.status === 'start'
                const done = st.status === 'done'
                return (
                  <div
                    key={s.key}
                    className="glass-row relative flex items-center gap-2 px-2 py-1.5"
                    style={active ? { borderColor: color, boxShadow: 'var(--sr-glow)' } : undefined}
                  >
                    <span className={`pulse-dot ${active ? 'animate-pulse' : ''}`}
                      style={{ background: color, width: 7, height: 7 }} />
                    <span className="mono text-[0.62rem] dim">{String(i + 1).padStart(2, '0')}</span>
                    <span className="text-[0.78rem]" style={{ color: done || active ? 'var(--sr-text)' : undefined }}>
                      {s.label}
                    </span>
                    <span className="ml-auto max-w-[50%] truncate text-right text-[0.62rem] dim" title={st.detail}>
                      {st.detail || st.status}
                    </span>
                  </div>
                )
              })}
            </div>
          </Panel>

          {/* ── equity ────────────────────────────────────────────── */}
          <Panel
            title="Equity"
            right={
              <span className="mono text-[0.8rem]"
                style={{ color: (equity?.growth_pct ?? 0) >= 0 ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                {fmtPct(equity?.growth_pct)}
              </span>
            }
          >
            <div className="grid grid-cols-3 gap-2">
              <MiniStat label="equity" value={fmtMoney(equity?.equity)} />
              <MiniStat label="start 🔒" value={fmtMoney(equity?.starting_balance)} />
              <MiniStat label="peak" value={fmtMoney(equity?.peak_equity)} />
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2">
              <MiniStat label="drawdown" value={`${(equity?.drawdown_pct ?? 0).toFixed(2)}%`} tone="bear" />
              <MiniStat label="slots free" value={String(equity?.open_slots ?? 0)} />
              <MiniStat label="today" value={fmtMoney(equity?.daily_pnl)}
                tone={(equity?.daily_pnl ?? 0) >= 0 ? 'bull' : 'bear'} />
            </div>
            <div className="mt-3 flex items-center gap-4">
              <Donut
                slices={[
                  { label: 'wins', value: stats?.wins ?? 0, color: 'var(--color-bull)' },
                  { label: 'losses', value: stats?.losses ?? 0, color: 'var(--color-bear)' },
                ]}
                size={104}
                center={
                  <div className="text-center">
                    <p className="mono text-[0.95rem]">{total ? `${winRate.toFixed(0)}%` : '—'}</p>
                    <p className="text-[0.55rem] uppercase tracking-wider dim">{total} trades</p>
                  </div>
                }
              />
              <div className="flex-1 text-[0.7rem]">
                <p className="dim">avg win <span className="mono" style={{ color: 'var(--color-bull)' }}>{fmtMoney(stats?.avg_win)}</span></p>
                <p className="dim">avg loss <span className="mono" style={{ color: 'var(--color-bear)' }}>{fmtMoney(stats?.avg_loss)}</span></p>
                <p className="dim">best <span className="mono">{stats?.best_symbol || '—'}</span></p>
                <p className="dim">worst <span className="mono">{stats?.worst_symbol || '—'}</span></p>
              </div>
            </div>
            <div className="mt-3 h-24">
              {curve.length > 1
                ? <LineChart data={curve} height={96} baseline={equity?.starting_balance} color="var(--color-cyan)" />
                : <p className="pt-6 text-center text-[0.7rem] dim">equity curve builds as trades close</p>}
            </div>
          </Panel>
        </div>
      </div>

      {/* ── open positions + ticker ───────────────────────────────── */}
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[1.55fr_1fr]">
        <Panel
          title="Open positions"
          right={
            <div className="flex items-center gap-2">
              <Chip color={openTrades.length > 0 ? 'var(--color-bull)' : undefined}>
                {openTrades.length > 0 ? 'live' : 'flat'}
              </Chip>
              <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }} onClick={() => goTo?.('trades')}>
                history →
              </button>
            </div>
          }
          bodyClass="p-0"
        >
          {openTrades.length === 0 ? (
            <div className="flex flex-col items-center gap-1 px-4 py-8 text-center">
              <ShieldCheck size={22} style={{ color: 'var(--color-cyan)' }} />
              <p className="text-[0.8rem]">Flat — scanners are hunting the next confirmed flip.</p>
              <p className="text-[0.68rem] dim">
                {risk ? `${risk.size_pct ?? ''}` : ''}one position per symbol · stop always inside liquidation
              </p>
            </div>
          ) : (
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full text-left text-[0.72rem]">
                <thead className="text-[0.62rem] uppercase tracking-wider dim">
                  <tr>
                    <th className="px-3 py-2">Symbol</th>
                    <th>Side</th>
                    <th className="text-right">Entry</th>
                    <th className="text-right">Mark</th>
                    <th className="text-right">uPnL</th>
                    <th className="text-right">SL / TP</th>
                    <th className="text-right">Liq</th>
                    <th className="px-3 text-right">Monitor</th>
                  </tr>
                </thead>
                <tbody>
                  {openTrades.map((t) => {
                    const up = (t.unrealized ?? 0) >= 0
                    const liqDist = t.mark && t.liquidation_price
                      ? Math.abs((t.liquidation_price - t.mark) / t.mark) * 100 : 999
                    return (
                      <tr key={t.id} className="glass-row">
                        <td className="px-3 py-1.5 mono">{t.symbol}</td>
                        <td>
                          <span className="flex items-center gap-1"
                            style={{ color: t.side === 'LONG' ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                            {t.side === 'LONG' ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                            {t.side}
                          </span>
                        </td>
                        <td className="mono text-right">{fmtNum(t.entry_price, 6)}</td>
                        <td className="mono text-right">{fmtNum(t.mark, 6)}</td>
                        <td className="mono text-right" style={{ color: up ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                          {fmtMoney(t.unrealized)} ({fmtPct(t.unrealized_pct)})
                        </td>
                        <td className="mono text-right dim">{fmtNum(t.sl_price, 6)} / {fmtNum(t.tp_price, 6)}</td>
                        <td className="mono text-right"
                          style={{ color: liqDist < 12 ? 'var(--color-bear)' : undefined }}>
                          {fmtNum(t.liquidation_price, 6)}
                        </td>
                        <td className="px-3 text-right mono dim">{t.monitor_id || t.monitor_bot_id || '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel title="Workflow log" right={<Chip color="var(--color-cyan)">{logs.length} lines</Chip>}>
          <div className="scroll-thin flex max-h-[16rem] flex-col gap-1 overflow-y-auto pr-1">
            {logs.length === 0 && <p className="text-[0.72rem] dim">no log lines yet</p>}
            {[...logs].slice(-30).reverse().map((l, i) => (
              <div key={`${l.ts}-${i}`} className="flex items-start gap-2 text-[0.68rem]">
                <span className="mono shrink-0 dim">{clock(l.ts)}</span>
                <span className="chip shrink-0"
                  style={{ borderColor: levelColor(l.level), color: levelColor(l.level), fontSize: '0.55rem', padding: '0 0.3rem' }}>
                  {(l.bot_id || l.level).slice(0, 15)}
                </span>
                <span className="min-w-0 flex-1 truncate" title={l.message}>{l.message}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {selected && (
        <Panel
          title={`Bot inspector — ${selected}`}
          right={
            <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
              onClick={() => setSelected(null)}>close</button>
          }
        >
          {(() => {
            const b = bots.find((x) => x.bot_id === selected)
            if (!b) {
              return (
                <p className="text-[0.75rem] dim">
                  This node is a team rail (a whole group), not a single agent — open the Bot Roster
                  to inspect its members.
                </p>
              )
            }
            return (
              <div className="flex flex-wrap items-center gap-4">
                <span className="text-3xl">{moodEmoji(b.mood)}</span>
                <div>
                  <p className="mono text-[0.9rem]">{b.name}</p>
                  <p className="text-[0.7rem] dim">{b.role} · {b.group} · {b.rank}</p>
                </div>
                <div className="min-w-[12rem] flex-1">
                  <Bar value={b.progress} color={b.color} glow />
                  <p className="pt-1 text-[0.68rem] dim">{b.task || b.message || b.status}</p>
                </div>
                <div className="flex gap-4">
                  <MiniStat label="score" value={String(Math.round(b.metrics?.score ?? 0))} />
                  <MiniStat label="tasks" value={String(b.metrics?.tasks_done ?? 0)} />
                  <MiniStat label="wins" value={String(b.metrics?.wins ?? 0)} tone="bull" />
                  <MiniStat label="errors" value={String(b.metrics?.errors ?? 0)} tone="bear" />
                  <MiniStat label="api/window" value={String(b.metrics?.api_spent_window ?? 0)} />
                </div>
                <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }} onClick={() => goTo?.('bots')}>
                  open dossier →
                </button>
              </div>
            )
          })()}
        </Panel>
      )}

      {/* ── celebration tape ─────────────────────────────────────── */}
      <Panel title="Latest closes" right={<Zap size={13} />}>
        {(() => {
          const closes = (logs || []).filter((l) => /closed \(|reconciled/i.test(l.message)).slice(-6).reverse()
          if (!closes.length) {
            return <p className="text-[0.72rem] dim">no closed trades yet — the first win is celebrated here 🎉</p>
          }
          return (
            <div className="flex flex-wrap gap-2">
              {closes.map((l, i) => {
                const win = /net \+\$/.test(l.message)
                return (
                  <span key={i} className="chip" style={{
                    borderColor: win ? 'var(--color-bull)' : 'var(--color-bear)',
                    color: win ? 'var(--color-bull)' : 'var(--color-bear)',
                  }}>
                    {win ? '🎉' : '💧'} {l.message.slice(0, 76)}
                  </span>
                )
              })}
            </div>
          )
        })()}
      </Panel>
    </div>
  )
}

export default Dashboard
