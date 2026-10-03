import React, { useMemo, useState } from 'react'
import { RefreshCw, Zap } from 'lucide-react'
import { useStore } from '../state/store'
import { endpoints } from '../lib/api'
import { Bar, Chip, Panel, fmtNum, fmtPct, timeAgo } from '../components/Glass'

export const Scan: React.FC = () => {
  const { scan, bots, botMap, pushToast } = useStore()
  const [tab, setTab] = useState<string>('scanner-1')
  const [sortKey, setSortKey] = useState<'symbol' | 'quality' | 'atr_pct' | 'rail_distance_pct'>('quality')
  const [busy, setBusy] = useState(false)

  const scanners = bots.filter((b) => b.group === 'scanner')
  const rows = useMemo(() => {
    const list = [...(scan.by_bot[tab] || [])]
    list.sort((a, b) => {
      if (sortKey === 'symbol') return a.symbol.localeCompare(b.symbol)
      return (Number(b[sortKey] ?? -1)) - (Number(a[sortKey] ?? -1))
    })
    return list
  }, [scan.by_bot, tab, sortKey])

  const bot = botMap[tab]

  const runNow = async () => {
    setBusy(true)
    try {
      const res = await endpoints.runCycle()
      pushToast({ kind: 'info', title: 'Workflow cycle dispatched', body: JSON.stringify(res) })
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Cycle failed', body: String(e?.message || e) })
    } finally { setBusy(false) }
  }

  const totalAssets = Object.values(scan.by_bot).reduce((a, v) => a + v.length, 0)

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Chip color="var(--color-bull)">{totalAssets} assets monitored</Chip>
        <Chip color="var(--color-cyan)">timeframe 5m</Chip>
        <Chip>{scan.by_bot[tab]?.length ?? 0} on {bot?.name || tab}</Chip>
        <Chip color="var(--color-amber)">scan #{scan.cycle}</Chip>
        <span className="text-[0.65rem] dim">updated {timeAgo(scan.updated_at)} · candle closes in
          {' '}{Math.max(0, Math.round(scan.seconds_to_close))}s</span>
        <button disabled={busy} onClick={runNow}
          className="chip ml-auto hover:opacity-80" style={{ cursor: 'pointer' }}>
          <RefreshCw size={11} className={busy ? 'animate-spin' : ''} /> run cycle now
        </button>
      </div>

      {/* scanner bot tabs */}
      <div className="flex flex-wrap gap-2">
        {scanners.map((s) => {
          const list = scan.by_bot[s.bot_id] || []
          const flips = list.filter((r) => r.flip).length
          const active = tab === s.bot_id
          return (
            <button key={s.bot_id} onClick={() => setTab(s.bot_id)}
              className="glass flex min-w-[11rem] flex-1 items-center gap-3 px-3 py-2 text-left transition-all"
              style={{
                borderColor: active ? 'color-mix(in oklab, var(--sr-accent) 55%, transparent)' : undefined,
                boxShadow: active ? 'var(--sr-glow)' : undefined,
              }}>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between">
                  <span className="text-[0.78rem] font-semibold" style={{ color: s.color }}>{s.name}</span>
                  <span className="mono text-[0.6rem] dim">{list.length} assets</span>
                </div>
                <div className="mt-1"><Bar value={(list.length / 30) * 100} height={4} color={s.color} /></div>
                <div className="mt-1 flex items-center justify-between text-[0.6rem] dim">
                  <span>{s.status === 'working' ? s.message.slice(0, 26) : `${s.metrics.tasks_done} sweeps`}</span>
                  <span>{flips ? <span style={{ color: 'var(--color-amber)' }}>{flips} flip</span> : '—'}</span>
                </div>
              </div>
            </button>
          )
        })}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[2.4fr_1fr]">
        <Panel title={`${bot?.name || tab} — market scan list`}
          right={
            <div className="flex items-center gap-1.5">
              {(['quality', 'atr_pct', 'rail_distance_pct', 'symbol'] as const).map((k) => (
                <button key={k} onClick={() => setSortKey(k)} className="chip hover:opacity-80"
                  style={{ cursor: 'pointer', borderColor: sortKey === k ? 'var(--sr-accent)' : undefined }}>
                  {k === 'quality' ? 'trend q' : k.replace('_', ' ')}
                </button>
              ))}
            </div>
          }
          bodyClass="scroll-thin overflow-auto">
          <table className="w-full text-[0.7rem]">
            <thead className="sticky top-0 text-[0.6rem] uppercase tracking-wider dim"
              style={{ background: 'rgba(5,7,12,0.85)' }}>
              <tr>
                <th className="px-3 py-1.5 text-left">Asset</th>
                <th className="text-right">Price</th>
                <th className="text-center">Regime</th>
                <th className="text-right">Trend Q</th>
                <th className="text-right">Rail Δ%</th>
                <th className="text-right">ATR%</th>
                <th className="text-center">1h gate</th>
                <th className="text-center">Flip</th>
                <th className="px-3 text-center">Position</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="glass-row border-t" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                  <td className="px-3 py-1.5 font-semibold">{r.symbol}</td>
                  <td className="mono text-right">{fmtNum(r.price)}</td>
                  <td className="text-center">
                    <span style={{ color: r.trend_side === 1 ? 'var(--color-bull)' : r.trend_side === -1 ? 'var(--color-bear)' : 'var(--sr-dim)' }}>
                      {r.trend}
                    </span>
                  </td>
                  <td className="text-right">
                    <div className="ml-auto flex w-[5rem] items-center gap-1.5">
                      <Bar value={((r.quality ?? 0) * 100)} height={5}
                        color={(r.quality ?? 0) >= 0.6 ? 'var(--color-bull)' : 'var(--color-cyan)'} />
                      <span className="mono text-[0.62rem]">{(r.quality ?? 0).toFixed(2)}</span>
                    </div>
                  </td>
                  <td className="mono text-right">{r.rail_distance_pct !== null ? fmtPct(r.rail_distance_pct) : '—'}</td>
                  <td className="mono text-right">{r.atr_pct !== null ? `${r.atr_pct.toFixed(2)}%` : '—'}</td>
                  <td className="text-center text-[0.62rem]">
                    <span style={{ color: r.htf_bull ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                      {r.htf_bull ? 'bull' : 'bear'}
                    </span>
                  </td>
                  <td className="text-center">
                    {r.flip
                      ? <span className="chip pulse-dot" style={{
                        color: r.flip === 'LONG' ? 'var(--color-bull)' : 'var(--color-bear)',
                        borderColor: 'currentColor',
                      }}><Zap size={9} />{r.flip}{r.tier === 'strong' ? ' ★' : ''}</span>
                      : <span className="dim">—</span>}
                  </td>
                  <td className="px-3 text-center text-[0.62rem]">
                    {r.has_position ? <span style={{ color: 'var(--color-amber)' }}>OPEN</span> : <span className="dim">flat</span>}
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={9} className="p-6 text-center text-[0.7rem] dim">
                  No scan data for this bot yet — the sweep runs on the next 5m candle close.
                </td></tr>
              )}
            </tbody>
          </table>
        </Panel>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel title={`${bot?.name || ''} scanner state`} bodyClass="p-3" className="shrink-0">
            {bot ? (
              <div className="flex flex-col gap-2 text-[0.68rem]">
                <div className="flex items-center justify-between">
                  <span className="dim">status</span>
                  <span style={{ color: bot.color }}>{bot.status}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="dim">sweeps completed</span><span className="mono">{bot.metrics.tasks_done}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="dim">avg sweep time</span>
                  <span className="mono">{(bot.metrics.avg_latency_ms / 1000).toFixed(2)}s</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="dim">api weight (window)</span>
                  <span className="mono">{bot.metrics.api_spent_window}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="dim">rank</span><span className="mono">{bot.rank}</span>
                </div>
                <Bar value={bot.metrics.score} color={bot.color} />
                <div className="text-[0.62rem] dim">{bot.message || bot.task}</div>
              </div>
            ) : <div className="text-[0.7rem] dim">select a scanner</div>}
          </Panel>

          <Panel title="Analyst decisions" right={<Chip>{scan.opportunities.length}</Chip>}
            bodyClass="scroll-thin overflow-auto p-0" className="min-h-0 flex-1">
            <div className="flex flex-col">
              {scan.opportunities.map((o: any, i: number) => (
                <div key={i} className="glass-row border-b px-3 py-2" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                  <div className="flex items-center justify-between text-[0.72rem]">
                    <span className="font-semibold">{o.symbol}
                      <span className="ml-1.5 text-[0.6rem]"
                        style={{ color: o.direction === 'LONG' ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                        {o.direction}
                      </span>
                      {o.tier === 'strong' && <span className="ml-1 text-[0.6rem] accent-text">★</span>}
                    </span>
                    <span className="mono" style={{ color: o.confidence >= 75 ? 'var(--color-bull)' : o.confidence >= 60 ? 'var(--color-amber)' : 'var(--color-bear)' }}>
                      {o.confidence.toFixed(1)}
                    </span>
                  </div>
                  <div className="mt-1 h-1 w-full overflow-hidden rounded-full" style={{ background: 'rgba(255,255,255,0.07)' }}>
                    <div className="h-full" style={{
                      width: `${o.confidence}%`,
                      background: o.approved ? 'var(--color-bull)' : 'var(--color-bear)',
                    }} />
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1 text-[0.58rem] dim">
                    <span>{o.analyst_id}</span>
                    <span>· q {o.trend_quality?.toFixed(2)}</span>
                    <span>· atr {o.atr_pct?.toFixed(2)}%</span>
                    {o.approved
                      ? <span style={{ color: 'var(--color-bull)' }}>· approved</span>
                      : <span style={{ color: 'var(--color-bear)' }}>· rejected</span>}
                  </div>
                </div>
              ))}
              {!scan.opportunities.length && (
                <div className="p-6 text-center text-[0.7rem] dim">
                  No analyst decisions yet — waiting for a confirmed flip.
                </div>
              )}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}
