import React, { useEffect, useState } from 'react'
import {
  Activity, ArrowDownRight, ArrowUpRight, Lock, Receipt, RefreshCw, ShieldCheck,
} from 'lucide-react'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'
import { Chip, Panel, Stat, fmtMoney, fmtNum, fmtPct } from '../components/Glass'
import type { Reconcile } from '../lib/types'

/**
 * Open Positions — every live trade the Monitor team is watching, the ROI
 * trail state of each one, and the ledger check that keeps the journal and the
 * exchange honest.  (Moved out of the Command Deck so the 3D work zone stays
 * clean.)
 */
export const Positions: React.FC = () => {
  const { openTrades, equity, status } = useStore()
  const [ledger, setLedger] = useState<Reconcile | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const loadLedger = async () => {
    try { setLedger(await endpoints.reconcile()) } catch { /* keep last verdict */ }
  }
  useEffect(() => {
    loadLedger()
    const t = window.setInterval(loadLedger, 30_000)
    return () => window.clearInterval(t)
  }, [])

  const refresh = async () => {
    setRefreshing(true)
    try { await loadLedger() } finally { setRefreshing(false) }
  }

  const floating = openTrades.reduce((a, t) => a + (t.unrealized ?? 0), 0)
  const trailCount = openTrades.filter((t) => t.trail_active).length
  const locked = openTrades
    .filter((t) => t.trail_active)
    .reduce((a, t) => a + Math.max(0, (t.stop_roi_pct ?? 0)), 0)
  const freeSlots = Math.max(0, (equity?.max_trades ?? 10) - openTrades.length)

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat label="Open Positions" value={`${openTrades.length} / ${equity?.max_trades ?? 10}`}
          icon={<Activity size={13} />} tone="warn" sub={`${freeSlots} slot(s) free`} />
        <Stat label="Unrealised P&L" value={fmtMoney(floating)}
          icon={<ArrowUpRight size={13} />} tone={floating >= 0 ? 'good' : 'bad'}
          sub={`mark-to-market across ${openTrades.length} position(s)`} />
        <Stat label="ROI Trail Armed" value={String(trailCount)} icon={<Lock size={13} />}
          tone={trailCount ? 'good' : 'warn'}
          sub={trailCount ? `Σ locked ROI ${locked.toFixed(1)} pts` : 'arms at +25 % ROI'} />
        <Stat label="Margin Used" value={fmtMoney(equity?.margin_used)} icon={<ShieldCheck size={13} />}
          sub={`budget ${fmtMoney(equity?.margin_budget)} · 8 % per trade`} />
        <Stat label="Realised P&L" value={fmtMoney(equity?.released_pnl)} icon={<Receipt size={13} />}
          tone={(equity?.released_pnl ?? 0) >= 0 ? 'good' : 'bad'} sub="released on close" />
        <Stat label="Engine" value={(status?.paused ? 'paused' : status?.running ? 'running' : 'stopped')}
          icon={<RefreshCw size={13} />} sub={`cycle #${status?.cycle ?? 0} · ${status?.transport ?? '—'}`} />
      </div>

      {/* ── ledger check: starting + released + unrealised − fees = equity ── */}
      {equity && (
        <Panel
          title="Ledger check"
          right={
            <div className="flex items-center gap-2">
              <Chip color={ledger && ledger.exchange_net != null
                ? (ledger.balanced ? 'var(--color-bull)' : 'var(--color-bear)')
                : 'var(--color-amber)'}>
                {ledger && ledger.exchange_net != null
                  ? (ledger.balanced ? 'venue reconciled' : 'drift detected')
                  : 'venue ledger pending'}
              </Chip>
              <Chip>{ledger?.transport || status?.transport || 'sim'}</Chip>
              <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }} onClick={refresh}>
                <RefreshCw size={11} className={refreshing ? 'animate-spin' : ''} /> recheck
              </button>
            </div>
          }
          bodyClass="p-2"
        >
          <div className="grid grid-cols-2 gap-2 px-1 md:grid-cols-3 xl:grid-cols-6">
            <Mini label="Starting" value={fmtMoney(equity.starting_balance)} />
            <Mini label="+ Realised" value={fmtMoney(equity.released_pnl)}
              tone={(equity.released_pnl ?? 0) >= 0 ? 'bull' : 'bear'} />
            <Mini label="+ Unrealised" value={fmtMoney(equity.unrealized)}
              tone={(equity.unrealized ?? 0) >= 0 ? 'bull' : 'bear'} />
            <Mini label="− Open entry fees" value={fmtMoney(-(equity.open_entry_fees ?? 0))} />
            <Mini label="= Equity" value={fmtMoney(equity.equity)} />
            <Mini
              label={ledger && ledger.exchange_net != null ? 'Journal ↔ venue' : 'Equity bridge'}
              value={ledger && ledger.exchange_net != null
                ? `${(ledger.net_drift ?? 0) >= 0 ? '+' : ''}${fmtNum(ledger.net_drift, 4)}`
                : fmtNum(equity.equity_bridge, 4)}
              tone={ledger && ledger.exchange_net != null
                ? (ledger.balanced ? 'bull' : 'bear') : undefined}
            />
          </div>
        </Panel>
      )}

      <Panel
        title={`Live positions — ${openTrades.length} open`}
        right={
          <div className="flex items-center gap-2">
            <Chip color="var(--color-cyan)">🔒 ROI trail: arm +25 %, stop −15 pts</Chip>
          </div>
        }
        bodyClass="scroll-thin overflow-auto p-0"
      >
        {openTrades.length === 0 ? (
          <div className="flex flex-col items-center gap-1 px-4 py-10 text-center">
            <ShieldCheck size={22} style={{ color: 'var(--color-cyan)' }} />
            <p className="text-[0.8rem]">Flat — scanners are hunting the next confirmed flip.</p>
          </div>
        ) : (
          <table className="w-full text-left text-[0.72rem]">
            <thead className="sticky top-0 text-[0.62rem] uppercase tracking-wider dim"
              style={{ background: 'rgba(5,7,12,0.85)' }}>
              <tr>
                <th className="px-3 py-2">Symbol</th>
                <th>Side</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Entry</th>
                <th className="text-right">Mark</th>
                <th className="text-right">uPnL</th>
                <th className="text-right">ROI / peak</th>
                <th className="text-right">Stop (ROI)</th>
                <th className="text-right">Target</th>
                <th className="text-right">Liq</th>
                <th className="px-3 text-right">Trail</th>
              </tr>
            </thead>
            <tbody>
              {openTrades.map((t) => {
                const up = (t.unrealized ?? 0) >= 0
                const liqDist = t.mark && t.liquidation_price
                  ? Math.abs((t.liquidation_price - t.mark) / t.mark) * 100 : 999
                return (
                  <tr key={t.id} className="glass-row border-t" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                    <td className="px-3 py-1.5 mono">{t.symbol}</td>
                    <td>
                      <span className="flex items-center gap-1"
                        style={{ color: t.side === 'LONG' ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                        {t.side === 'LONG' ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                        {t.side}
                      </span>
                    </td>
                    <td className="mono text-right">{fmtNum(t.qty, 6)}</td>
                    <td className="mono text-right">{fmtNum(t.entry_price, 6)}</td>
                    <td className="mono text-right">{fmtNum(t.mark, 6)}</td>
                    <td className="mono text-right" style={{ color: up ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                      {fmtMoney(t.unrealized)} ({fmtPct(t.unrealized_pct)})
                    </td>
                    <td className="mono text-right">
                      <span style={{ color: up ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                        {(t.roi_pct ?? 0).toFixed(1)}%
                      </span>
                      <span className="dim"> / {(t.peak_roi_pct ?? 0).toFixed(1)}%</span>
                    </td>
                    <td className="mono text-right">
                      {fmtNum(t.sl_price, 6)}
                      <span className="dim"> ({(t.stop_roi_pct ?? 0).toFixed(1)}%)</span>
                    </td>
                    <td className="mono text-right dim">{fmtNum(t.tp_price, 6)}</td>
                    <td className="mono text-right"
                      style={{ color: liqDist < 12 ? 'var(--color-bear)' : undefined }}>
                      {fmtNum(t.liquidation_price, 6)}
                    </td>
                    <td className="px-3 text-right">
                      {t.trail_active ? (
                        <Chip color="var(--color-cyan)">
                          🔒 +{(t.stop_roi_pct ?? 0).toFixed(1)}% ROI
                        </Chip>
                      ) : (
                        <span className="dim mono text-[0.62rem]">
                          arms at +{(t.trail_activation_roi_pct ?? 25).toFixed(0)}%
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}

const Mini: React.FC<{ label: string; value: string; tone?: 'bull' | 'bear' }> = ({ label, value, tone }) => (
  <div>
    <p className="text-[0.58rem] uppercase tracking-wider dim">{label}</p>
    <p className="mono text-[0.78rem]" style={{
      color: tone === 'bull' ? 'var(--color-bull)' : tone === 'bear' ? 'var(--color-bear)' : undefined,
    }}>{value}</p>
  </div>
)
