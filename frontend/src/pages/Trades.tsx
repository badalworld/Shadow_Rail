import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, Filter, Search } from 'lucide-react'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'
import { Chip, Panel, Stat, clock, dateTime, fmtMoney, fmtNum, fmtPct } from '../components/Glass'
import { Bars, Donut, LineChart } from '../components/Charts'
import type { Stats, Trade } from '../lib/types'

export const Trades: React.FC = () => {
  const { stats, equity, pushToast } = useStore()
  const [rows, setRows] = useState<Trade[]>([])
  const [total, setTotal] = useState(0)
  const [limit, setLimit] = useState(100)
  const [symbol, setSymbol] = useState('')
  const [result, setResult] = useState('')
  const [reason, setReason] = useState('')
  const [detail, setDetail] = useState<{ trade: Trade; events: any[] } | null>(null)
  const [curve, setCurve] = useState<any>({ points: [], cumulative: [], by_day: [] })
  const [s, setS] = useState<Stats | null>(stats)

  const load = useCallback(async () => {
    try {
      const [res, cv, st] = await Promise.all([
        endpoints.closedTrades({ limit, symbol, result, reason }),
        endpoints.equityCurve(1200),
        endpoints.stats(),
      ])
      setRows(res.trades || [])
      setTotal(res.total || 0)
      setCurve(cv)
      setS(st.stats)
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Could not load trades', body: String(e?.message || e) })
    }
  }, [limit, symbol, result, reason, pushToast])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    const t = setInterval(load, 15000)
    return () => clearInterval(t)
  }, [load])

  const cumulative = useMemo(() => {
    const pts = (curve.cumulative || []).map((p: any, i: number) => ({ x: i, y: p.cum }))
    return pts.length ? pts : [{ x: 0, y: 0 }, { x: 1, y: 0 }]
  }, [curve.cumulative])

  const daily = useMemo(() =>
    (curve.by_day || []).map((d: any) => ({ label: d.day, value: d.net })), [curve.by_day])

  const exportCsv = () => {
    const head = ['id', 'symbol', 'side', 'qty', 'entry', 'exit', 'net_pnl', 'fees', 'funding',
      'reason', 'r', 'opened', 'closed', 'confidence', 'analyst', 'scanner', 'exec']
    const lines = [head.join(',')].concat(rows.map((r) => [
      r.id, r.symbol, r.side, r.qty, r.entry_price, r.exit_price, r.net_pnl, r.fee_paid,
      r.funding_paid, r.close_reason, r.r_multiple, new Date(r.opened_at).toISOString(),
      r.closed_at ? new Date(r.closed_at).toISOString() : '', r.signal_confidence,
      r.analyst_id, r.scanner_id, r.exec_bot_id,
    ].join(',')))
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `shadow-rail-trades-${Date.now()}.csv`
    a.click()
    pushToast({ kind: 'success', title: `Exported ${rows.length} trades` })
  }

  const winRate = s?.win_rate ?? 0

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Total Executed" value={s?.total_trades ?? 0} sub={`${s?.wins ?? 0}W / ${s?.losses ?? 0}L`} />
        <Stat label="Win Rate" value={`${winRate.toFixed(1)}%`}
          tone={winRate >= 50 ? 'good' : 'warn'} sub={`PF ${(s?.profit_factor ?? 0).toFixed(2)}`} />
        <Stat label="Net P&L" value={fmtMoney(s?.net_pnl)} tone={(s?.net_pnl ?? 0) >= 0 ? 'good' : 'bad'}
          sub={`avg win ${fmtMoney(s?.avg_win)} / loss ${fmtMoney(s?.avg_loss)}`} />
        <Stat label="Fees Paid" value={fmtMoney(s?.fees_paid)} tone="warn"
          sub={`${((s?.fees_paid ?? 0) / Math.max(1, s?.total_trades ?? 1)).toFixed(2)} per trade avg`} />
        <Stat label="Funding" value={fmtMoney(s?.funding_net)} tone={(s?.funding_net ?? 0) >= 0 ? 'good' : 'bad'}
          sub={(s?.funding_net ?? 0) >= 0 ? 'net received' : 'net paid'} />
        <Stat label="Best / Worst" value={<span>{fmtNum(s?.best_trade, 2)} / {fmtNum(s?.worst_trade, 2)}</span>}
          sub={`${s?.best_symbol || '—'} · ${s?.worst_symbol || '—'}`} />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[1.6fr_1fr_1fr]">
        <Panel title="Cumulative net P&L" right={<Chip>{curve.cumulative?.length ?? 0} trades</Chip>} bodyClass="p-3">
          <div className="h-[9.5rem]">
            <LineChart data={cumulative} baseline={0}
              color={(s?.net_pnl ?? 0) >= 0 ? 'var(--color-bull)' : 'var(--color-bear)'} />
          </div>
        </Panel>
        <Panel title="Daily P&L (30d)" right={<Chip color="var(--color-cyan)">{daily.length} days</Chip>} bodyClass="p-3">
          <Bars data={daily} height={140} />
        </Panel>
        <Panel title="Outcome split" bodyClass="p-3 flex items-center justify-center">
          <Donut size={148} slices={[
            { value: s?.wins ?? 0, color: 'var(--color-bull)', label: 'wins' },
            { value: s?.losses ?? 0, color: 'var(--color-bear)', label: 'losses' },
          ]} center={
            <div className="text-center">
              <div className="mono text-[1.05rem]">{winRate.toFixed(0)}%</div>
              <div className="text-[0.58rem] uppercase tracking-wider dim">win rate</div>
            </div>
          } />
        </Panel>
      </div>

      <Panel
        title={`Closed positions — ${rows.length} of ${total}`}
        right={
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 dim" />
              <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="symbol" className="w-28 rounded-full border bg-transparent py-1 pl-6 pr-2 text-[0.68rem] outline-none"
                style={{ borderColor: 'var(--sr-border)' }} />
            </div>
            <select value={result} onChange={(e) => setResult(e.target.value)}
              className="rounded-full border bg-transparent px-2 py-1 text-[0.68rem] outline-none"
              style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
              <option value="">all results</option>
              <option value="win">wins</option>
              <option value="loss">losses</option>
            </select>
            <select value={reason} onChange={(e) => setReason(e.target.value)}
              className="rounded-full border bg-transparent px-2 py-1 text-[0.68rem] outline-none"
              style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
              <option value="">all exits</option>
              <option value="tp">take profit</option>
              <option value="sl">stop loss</option>
              <option value="reverse_signal">reverse signal</option>
              <option value="emergency">emergency</option>
            </select>
            <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
              className="rounded-full border bg-transparent px-2 py-1 text-[0.68rem] outline-none"
              style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
              {[50, 100, 250, 500].map((n) => <option key={n} value={n}>{n} rows</option>)}
            </select>
            <button onClick={exportCsv} className="chip hover:opacity-80" style={{ cursor: 'pointer' }}>
              <Download size={11} /> csv
            </button>
          </div>
        }
        bodyClass="scroll-thin overflow-auto">
        <table className="w-full text-[0.7rem]">
          <thead className="sticky top-0 text-[0.6rem] uppercase tracking-wider dim"
            style={{ background: 'rgba(5,7,12,0.85)' }}>
            <tr>
              <th className="px-3 py-1.5 text-left">#</th>
              <th className="text-left">Symbol</th>
              <th className="text-left">Side</th>
              <th className="text-right">Qty</th>
              <th className="text-right">Entry → Exit</th>
              <th className="text-right">Gross</th>
              <th className="text-right">Fees</th>
              <th className="text-right">Funding</th>
              <th className="text-right">Net</th>
              <th className="text-right">R</th>
              <th className="text-center">Exit reason</th>
              <th className="text-left">Opened</th>
              <th className="text-left">Closed</th>
              <th className="text-center">Bots</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const up = (r.net_pnl ?? 0) >= 0
              return (
                <tr key={r.id} onClick={() => endpoints.trade(r.id).then(setDetail)}
                  className="glass-row cursor-pointer border-t" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                  <td className="px-3 py-1.5 dim">{r.id}</td>
                  <td className="font-semibold">{r.symbol}</td>
                  <td style={{ color: r.side === 'LONG' ? 'var(--color-bull)' : 'var(--color-bear)' }}>{r.side}</td>
                  <td className="mono text-right">{fmtNum(r.qty)}</td>
                  <td className="mono text-right">{fmtNum(r.entry_price)} → {fmtNum(r.exit_price)}</td>
                  <td className="mono text-right">{fmtMoney(r.gross_pnl)}</td>
                  <td className="mono text-right dim">{fmtMoney(r.fee_paid)}</td>
                  <td className="mono text-right dim">{fmtMoney(r.funding_paid)}</td>
                  <td className="mono text-right font-semibold"
                    style={{ color: up ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                    {fmtMoney(r.net_pnl)}
                  </td>
                  <td className="mono text-right">{(r.r_multiple ?? 0).toFixed(2)}</td>
                  <td className="text-center">
                    <span className="chip" style={{
                      color: r.close_reason === 'tp' ? 'var(--color-bull)'
                        : r.close_reason === 'sl' ? 'var(--color-bear)'
                          : r.close_reason === 'reverse_signal' ? 'var(--color-cyan)' : 'var(--color-amber)',
                    }}>{r.close_reason?.replace(/_/g, ' ')}</span>
                  </td>
                  <td className="text-[0.62rem] dim">{dateTime(r.opened_at)}</td>
                  <td className="text-[0.62rem] dim">{dateTime(r.closed_at)}</td>
                  <td className="text-center text-[0.58rem] dim">
                    {[r.scanner_id, r.analyst_id, r.exec_bot_id].filter(Boolean).join(' · ')}
                  </td>
                </tr>
              )
            })}
            {!rows.length && (
              <tr><td colSpan={14} className="p-8 text-center text-[0.75rem] dim">
                <Filter size={16} className="mx-auto mb-2" />
                No closed trades yet. The Trade Manager writes here the moment a position closes.
              </td></tr>
            )}
          </tbody>
        </table>
      </Panel>

      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setDetail(null)}>
          <div className="glass max-h-[80vh] w-full max-w-3xl overflow-auto p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-[0.95rem] font-semibold">
                {detail.trade.symbol} {detail.trade.side} · trade #{detail.trade.id}
              </div>
              <button className="chip" onClick={() => setDetail(null)}>close</button>
            </div>
            <div className="grid grid-cols-2 gap-4 text-[0.72rem] md:grid-cols-4">
              {[
                ['entry', fmtNum(detail.trade.entry_price)],
                ['exit', fmtNum(detail.trade.exit_price)],
                ['qty', fmtNum(detail.trade.qty)],
                ['leverage', `${detail.trade.leverage}x`],
                ['net P&L', fmtMoney(detail.trade.net_pnl)],
                ['fees', fmtMoney(detail.trade.fee_paid)],
                ['funding', fmtMoney(detail.trade.funding_paid)],
                ['R multiple', (detail.trade.r_multiple ?? 0).toFixed(2)],
                ['confidence', `${(detail.trade.signal_confidence ?? 0).toFixed(1)} (${detail.trade.signal_tier})`],
                ['opened', dateTime(detail.trade.opened_at)],
                ['closed', dateTime(detail.trade.closed_at)],
                ['mode', detail.trade.mode],
              ].map(([k, v]) => (
                <div key={k as string}>
                  <div className="text-[0.6rem] uppercase tracking-wider dim">{k}</div>
                  <div className="mono">{v as string}</div>
                </div>
              ))}
            </div>
            <div className="mt-4">
              <div className="mb-1.5 text-[0.65rem] uppercase tracking-wider dim">lifecycle events</div>
              <div className="flex flex-col gap-1">
                {detail.events.map((e: any) => (
                  <div key={e.id} className="glass-solid px-2.5 py-1.5 text-[0.66rem]">
                    <span className="mono dim">{clock(e.created_at)}</span>
                    <span className="ml-2 accent-text">{e.kind}</span>
                    <span className="ml-2 dim">{String(e.detail || '').slice(0, 220)}</span>
                  </div>
                ))}
                {!detail.events.length && <div className="text-[0.68rem] dim">no events recorded</div>}
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2 text-[0.65rem] dim">
              <Chip>scanner {detail.trade.scanner_id || '—'}</Chip>
              <Chip>analyst {detail.trade.analyst_id || '—'}</Chip>
              <Chip>execution {detail.trade.exec_bot_id || '—'}</Chip>
              <Chip>monitor {detail.trade.monitor_bot_id || '—'}</Chip>
              <Chip>equity at close {fmtMoney(equity?.equity)}</Chip>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
