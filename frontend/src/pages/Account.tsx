import React, { useEffect, useMemo, useState } from 'react'
import {
  Activity, ArrowDownRight, ArrowUpRight, Coins, Gauge, Lock, Receipt,
  TrendingUp, Wallet,
} from 'lucide-react'
import { Bar, Chip, Panel, Stat, fmtMoney, fmtNum, fmtPct } from '../components/Glass'
import { LineChart } from '../components/Charts'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'

/**
 * Account — where the six numbers the operator used to keep on the Command Deck
 * now live, together with the rest of the equity sheet.
 *
 * The Command Deck itself is only the 3D headquarters; everything numeric moved
 * into the menu.  The cards are identical, so the numbers still read the same
 * way — and they come from the same `/api/equity` + `/api/stats` payloads.
 */
export const Account: React.FC = () => {
  const { equity, stats, status, openTrades, pushToast } = useStore()
  const [curve, setCurve] = useState<{ x: number; y: number }[]>([])
  const [reconcile, setReconcile] = useState<any>(null)

  useEffect(() => {
    let dead = false
    const load = async () => {
      try {
        const [cv, rc] = await Promise.all([endpoints.equityCurve(600), endpoints.reconcile()])
        if (dead) return
        setCurve((cv.cumulative || []).map((p: any, i: number) => ({ x: i, y: p.cum })))
        setReconcile(rc)
      } catch (e: any) {
        if (!dead) pushToast({ kind: 'error', title: 'Could not load the account sheet', body: String(e?.message || e) })
      }
    }
    load()
    const t = window.setInterval(load, 20_000)
    return () => { dead = true; window.clearInterval(t) }
  }, [pushToast])

  const winRate = stats?.win_rate ?? 0
  const trailing = openTrades.filter((t) => t.trail_active).length
  const growth = equity?.growth_pct ?? 0
  const drawdown = equity?.drawdown_pct ?? 0
  // the day anchor is only set once the engine has seen a full session; before
  // that `daily_pnl` would equal the whole equity, so fall back to the API's
  // explicit `daily` figure
  const daily = (equity?.day_start_equity ?? 0) > 0 ? (equity?.daily_pnl ?? 0) : (equity?.daily ?? 0)
  const feesTotal = equity?.fees_paid_total ?? equity?.fees_paid ?? 0
  const openFees = equity?.open_entry_fees ?? 0
  const fundingNet = equity?.funding_net ?? 0
  const startLocked = equity?.starting_locked

  const rows = useMemo(() => ([
    ['Balance', fmtMoney(equity?.balance), 'cash the exchange reports'],
    ['Available margin', fmtMoney(equity?.available), 'free to open the next trade'],
    ['Margin used', fmtMoney(equity?.margin_used), `budget ${fmtMoney(equity?.margin_budget)} per trade`],
    ['Open slots', `${equity?.open_slots ?? Math.max(0, (status?.max_trades ?? 10) - (equity?.open_positions ?? 0))} of ${status?.max_trades ?? 10}`, 'analysts may execute into these'],
    ['Unrealised', fmtMoney(equity?.unrealized), 'mark-to-market on open positions'],
    ['Peak equity', fmtMoney(equity?.peak_equity), `drawdown ${fmtPct(-Math.abs(drawdown))} from the peak`],
    ['Daily P&L', fmtMoney(daily), (equity?.day_start_equity ?? 0) > 0
      ? `day started at ${fmtMoney(equity?.day_start_equity)}`
      : 'since the first cycle today'],
    ['Equity bridge', fmtMoney(equity?.equity_bridge), 'per-trade P&L + costs, nothing else'],
  ]), [equity, status, drawdown])

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      {/* ── the six numbers ───────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Starting Balance"
          value={fmtMoney(equity?.starting_balance)}
          locked={startLocked}
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
          tone={growth >= 0 ? 'good' : 'bad'}
          sub={`${fmtPct(growth)} since start · free ${fmtMoney(equity?.available)}`}
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
          value={fmtMoney(feesTotal)}
          icon={<Receipt size={13} />}
          tone="bad"
          sub={`incl. ${fmtMoney(openFees)} on open positions · funding ${fmtMoney(fundingNet)} net`}
        />
        <Stat
          label="Win Rate"
          value={`${winRate.toFixed(1)}%`}
          icon={<Gauge size={13} />}
          tone={winRate >= 50 ? 'good' : 'warn'}
          sub={`${stats?.wins ?? 0}W / ${stats?.losses ?? 0}L · ${stats?.total_trades ?? 0} closed · PF ${fmtNum(stats?.profit_factor, 2)}`}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {/* ── the sheet ──────────────────────────────────────────────── */}
        <Panel title="Equity sheet" right={<Chip color="var(--sr-accent)"><Wallet size={10} /> equity manager</Chip>}
          bodyClass="p-3" className="lg:col-span-2">
          <div className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
            {rows.map(([label, value, hint]) => (
              <div key={label} className="flex items-baseline justify-between gap-3 border-b py-1.5"
                style={{ borderColor: 'var(--sr-border)' }}>
                <span className="text-[0.62rem] uppercase tracking-wider dim" title={hint}>{label}</span>
                <span className="mono text-[0.82rem] tabular">{value}</span>
              </div>
            ))}
          </div>
          <p className="pt-2 text-[0.62rem] dim">
            Starting balance is fixed at the first connection and never changes.  Every figure above
            is derived from the journal, so it always satisfies{' '}
            <span className="mono">starting + realised + unrealised − open entry fees = equity</span>.
          </p>
        </Panel>

        {/* ── growth ─────────────────────────────────────────────────── */}
        <Panel title="Growth" right={<Chip>{stats?.total_trades ?? 0} closed</Chip>} bodyClass="p-3">
          <div className="flex items-end justify-between gap-3">
            <div>
              <p className="text-[0.58rem] uppercase tracking-[0.16em] dim">net growth</p>
              <p className="mono text-[1.35rem] tabular"
                style={{ color: growth >= 0 ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                {growth >= 0 ? <ArrowUpRight size={22} className="inline" /> : <ArrowDownRight size={22} className="inline" />}
                {fmtPct(growth)}
              </p>
              <p className="text-[0.62rem] dim">{fmtMoney(equity?.growth_abs)} since the lock</p>
            </div>
            <div className="text-right">
              <p className="text-[0.58rem] uppercase tracking-[0.16em] dim">drawdown</p>
              <p className="mono text-[1.35rem] tabular" style={{ color: 'var(--color-amber)' }}>
                {fmtPct(-Math.abs(drawdown))}
              </p>
              <p className="text-[0.62rem] dim">peak {fmtMoney(equity?.peak_equity)}</p>
            </div>
          </div>
          <div className="pt-2">
            <Bar value={Math.min(100, Math.abs(growth))} color={growth >= 0 ? 'var(--color-bull)' : 'var(--color-bear)'} glow />
            <p className="pt-1 text-[0.6rem] dim">
              {equity?.open_positions ?? 0} of {status?.max_trades ?? 10} position slots in use
              {trailing > 0 ? ` · ${trailing} riding the ROI trail` : ''}
            </p>
          </div>
          <div className="pt-2">
            <p className="text-[0.58rem] uppercase tracking-[0.16em] dim">cumulative net P&L per close</p>
            <div className="pt-1">
              <LineChart data={curve} height={130} baseline={0}
                color={(curve.at(-1)?.y ?? 0) >= 0 ? 'var(--color-bull)' : 'var(--color-bear)'} />
            </div>
          </div>
        </Panel>
      </div>

      {/* ── costs + the ledger honesty check ───────────────────────────── */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <Panel title="Costs — what the balance already paid" bodyClass="p-3" className="lg:col-span-2">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Taker fees" value={fmtMoney(feesTotal)} tone="bad"
              sub="0.05 % per leg, every entry and exit" />
            <Stat label="Entry fees on open" value={fmtMoney(openFees)} tone="warn"
              sub="already paid, still inside open positions" />
            <Stat label="Funding" value={fmtMoney(equity?.funding_paid)}
              sub={`net ${fmtMoney(fundingNet)} credited/debited`} />
            <Stat label="Net after costs" value={fmtMoney(equity?.net_after_costs)}
              tone={(equity?.net_after_costs ?? 0) >= 0 ? 'good' : 'bad'}
              sub="realised P&L minus fees and funding" />
          </div>
          <p className="pt-2 text-[0.62rem] dim">
            Fees and funding are inside every closed-trade figure — the journal books them in the
            trade&apos;s own ledger row, and the P&L chart above is net of them.
          </p>
        </Panel>

        <Panel title="Ledger check" bodyClass="p-3"
          right={<Chip color={reconcile?.balanced ? 'var(--color-bull)' : 'var(--color-amber)'}>
            {reconcile ? (reconcile.balanced ? 'balanced' : 'drift') : '—'}
          </Chip>}>
          {reconcile ? (
            <div className="flex flex-col gap-1 text-[0.68rem]">
              {[
                ['journal net', fmtMoney(reconcile.journal_net)],
                ['open entry fees', fmtMoney(reconcile.open_entry_fees)],
                ['expected from journal', fmtMoney(reconcile.expected_from_journal)],
                ['venue ledger', fmtMoney(reconcile.exchange_net)],
                ['drift', fmtMoney(reconcile.net_drift)],
                ['tolerance', fmtMoney(reconcile.tolerance)],
                ['transport', String(reconcile.transport || '—')],
              ].map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between gap-3">
                  <span className="dim">{k}</span>
                  <span className="mono">{v}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[0.68rem] dim">reading the venue ledger…</p>
          )}
        </Panel>
      </div>
    </div>
  )
}

export default Account
