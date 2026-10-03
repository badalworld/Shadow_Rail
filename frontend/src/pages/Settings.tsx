import React, { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import {
  Activity, CheckCircle2, Copy, Globe, KeyRound, RefreshCw, Save, ShieldAlert, Sliders, Waves, XCircle,
} from 'lucide-react'
import { endpoints } from '../lib/api'
import { BOOT, useStore } from '../state/store'
import { Bar, Chip, Panel } from '../components/Glass'

type Tab = 'api' | 'risk' | 'indicator' | 'engine' | 'danger'

const TABS: { key: Tab; label: string; icon: React.ReactNode }[] = [
  { key: 'api', label: 'Binance API & IP', icon: <KeyRound size={14} /> },
  { key: 'risk', label: 'Risk / TP-SL', icon: <ShieldAlert size={14} /> },
  { key: 'indicator', label: 'Indicator', icon: <Waves size={14} /> },
  { key: 'engine', label: 'Engine & Swarm', icon: <Sliders size={14} /> },
  { key: 'danger', label: 'Advanced', icon: <Activity size={14} /> },
]

export const Settings: React.FC = () => {
  const { pushToast, status, refresh } = useStore()
  const [tab, setTab] = useState<Tab>('api')
  const [cfg, setCfg] = useState<any>(BOOT.config ?? null)
  const [ip, setIp] = useState<any>(BOOT.ip ?? null)
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [testing, setTesting] = useState(false)
  const [test, setTest] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const [selftest, setSelftest] = useState<any>(null)

  const load = useCallback(async () => {
    try {
      const [c, i] = await Promise.all([endpoints.config(), endpoints.ipInfo()])
      setCfg(c.config)
      setIp(i)
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Could not load settings', body: String(e?.message || e) })
    }
  }, [pushToast])

  useEffect(() => { load() }, [load])

  const patch = (section: string, key: string, value: any) =>
    setCfg((prev: any) => ({ ...prev, [section]: { ...prev[section], [key]: value } }))

  const save = async () => {
    setSaving(true)
    try {
      const body: any = {
        binance: { ...cfg.binance, api_key: apiKey, api_secret: apiSecret },
        risk: cfg.risk, indicator: cfg.indicator, engine: cfg.engine, ui: cfg.ui,
      }
      delete body.binance.api_key_masked
      delete body.binance.api_secret_masked
      delete body.binance.has_key
      delete body.binance.has_secret
      delete body.binance.api_key_len
      delete body.risk.active_system
      const res = await endpoints.saveConfig(body)
      setApiKey(''); setApiSecret('')
      await load(); refresh()
      const ignored: string[] = res?.unknown || []
      pushToast({
        kind: ignored.length ? 'warn' : 'success',
        title: ignored.length ? 'Saved with warnings' : 'Settings saved',
        body: ignored.length
          ? `The engine ignored ${ignored.length} field(s): ${ignored.slice(0, 4).join(', ')}`
          : 'Engine hot-reloaded with the new configuration',
      })
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Save failed', body: String(e?.message || e) })
    } finally { setSaving(false) }
  }

  const runTest = async () => {
    setTesting(true); setTest(null)
    try {
      const res = await endpoints.testConnection({
        api_key: apiKey || undefined, api_secret: apiSecret || undefined,
        testnet: cfg?.binance?.testnet,
      })
      setTest(res)
      pushToast({
        kind: res.ok ? 'success' : 'error',
        title: res.ok ? 'Connector Bot: link verified' : 'Connection test failed',
        body: res.ok ? `equity ${res.equity} · ${res.latency_ms}ms` : (res.error || '') + (res.hint ? ` — ${res.hint}` : ''),
      })
      if (res.ok) { await load(); refresh() }
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Test failed', body: String(e?.message || e) })
    } finally { setTesting(false) }
  }

  const verifyIp = async () => {
    try {
      const res = await endpoints.verifyIp({
        ip_whitelist: cfg?.binance?.ip_whitelist || ip?.public_ip || '',
      })
      setIp((p: any) => ({ ...p, whitelist_confirmed: res.whitelisted_ok }))
      pushToast({
        kind: res.whitelisted_ok ? 'success' : 'warn',
        title: res.whitelisted_ok ? 'IP whitelist confirmed' : 'IP not confirmed yet',
        body: res.message || `Add ${ip?.public_ip} to your Binance API restrictions`,
      })
      await load()
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Verification failed', body: String(e?.message || e) })
    }
  }

  if (!cfg) {
    return <div className="flex h-full items-center justify-center text-[0.8rem] dim">loading settings…</div>
  }

  const activeSystem = cfg.risk.active_system

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className="glass flex items-center gap-2 px-3 py-2 text-[0.75rem] transition-all"
            style={{
              borderColor: tab === t.key ? 'var(--sr-accent)' : undefined,
              boxShadow: tab === t.key ? 'var(--sr-glow)' : undefined,
              cursor: 'pointer',
            }}>
            <span style={{ color: tab === t.key ? 'var(--sr-accent)' : 'var(--sr-dim)' }}>{t.icon}</span>
            {t.label}
          </button>
        ))}
        <button onClick={save} disabled={saving}
          className="chip ml-auto hover:opacity-80"
          style={{ cursor: 'pointer', borderColor: 'var(--sr-accent)', color: 'var(--sr-accent)' }}>
          {saving ? <RefreshCw size={12} className="animate-spin" /> : <Save size={12} />} save all settings
        </button>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto pr-0.5">
        {/* ══════════════════════ API ══════════════════════ */}
        {tab === 'api' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-[1.4fr_1fr]">
            <Panel title="Binance USDT-M Futures API" bodyClass="p-4">
              <div className="flex flex-col gap-3">
                <div className="glass-solid p-3 text-[0.68rem] leading-relaxed dim">
                  The Connector Bot (<span className="accent-text">ORACLE</span>) verifies this link
                  immediately after saving and re-checks every 5 minutes. Keys are encrypted at rest
                  (Fernet) and never leave this server.
                </div>

                <label className="flex flex-col gap-1">
                  <span className="text-[0.62rem] uppercase tracking-wider dim">API key</span>
                  <input value={apiKey} onChange={(e) => setApiKey(e.target.value)}
                    placeholder={cfg.binance.has_key ? `stored: ${cfg.binance.api_key_masked}` : 'paste API key'}
                    className="mono rounded-xl border bg-transparent px-3 py-2 text-[0.75rem] outline-none"
                    style={{ borderColor: 'var(--sr-border)' }} />
                </label>

                <label className="flex flex-col gap-1">
                  <span className="text-[0.62rem] uppercase tracking-wider dim">API secret</span>
                  <input value={apiSecret} onChange={(e) => setApiSecret(e.target.value)} type="password"
                    placeholder={cfg.binance.has_secret ? `stored: ${cfg.binance.api_secret_masked}` : 'paste API secret'}
                    className="mono rounded-xl border bg-transparent px-3 py-2 text-[0.75rem] outline-none"
                    style={{ borderColor: 'var(--sr-border)' }} />
                </label>

                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1">
                    <span className="text-[0.62rem] uppercase tracking-wider dim">Endpoint</span>
                    <select value={cfg.binance.testnet ? 'testnet' : 'live'}
                      onChange={(e) => patch('binance', 'testnet', e.target.value === 'testnet')}
                      className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem] outline-none"
                      style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                      <option value="live">Live — fapi.binance.com</option>
                      <option value="testnet">Testnet — testnet.binancefuture.com</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[0.62rem] uppercase tracking-wider dim">Trading mode</span>
                    <select value={cfg.binance.mode}
                      onChange={(e) => patch('binance', 'mode', e.target.value)}
                      className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem] outline-none"
                      style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                      <option value="live">live — real orders, real funds</option>
                      <option value="paper">paper — real data, simulated fills</option>
                      <option value="sim">sim — synthetic market (offline demo)</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-[0.62rem] uppercase tracking-wider dim">Data transport</span>
                    <select value={cfg.binance.transport}
                      onChange={(e) => patch('binance', 'transport', e.target.value)}
                      className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem] outline-none"
                      style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                      <option value="auto">auto — Binance, else simulate</option>
                      <option value="binance">binance only — SOS if unreachable</option>
                      <option value="sim">simulation only</option>
                    </select>
                  </label>
                  <label className="flex items-center gap-2 self-end text-[0.72rem]">
                    <input type="checkbox" checked={cfg.engine.simulate_when_offline}
                      onChange={(e) => patch('engine', 'simulate_when_offline', e.target.checked)} />
                    keep workflow alive in simulation when Binance is down
                  </label>
                </div>

                <div className="flex flex-wrap gap-2">
                  <button onClick={runTest} disabled={testing}
                    className="chip hover:opacity-80"
                    style={{ cursor: 'pointer', borderColor: 'var(--sr-accent)', color: 'var(--sr-accent)' }}>
                    <Activity size={12} /> {testing ? 'testing…' : 'test connection'}
                  </button>
                  <button onClick={() => endpoints.probe().then(() => pushToast({ kind: 'info', title: 'Connector Bot probing' }))}
                    className="chip hover:opacity-80" style={{ cursor: 'pointer' }}>
                    <RefreshCw size={12} /> force 5-min health probe
                  </button>
                </div>

                {test && (
                  <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                    className="glass-solid p-3 text-[0.7rem]"
                    style={{ borderColor: test.ok ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                    <div className="flex items-center gap-2 font-semibold"
                      style={{ color: test.ok ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                      {test.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
                      {test.ok ? 'Connection verified — connector bot GREEN' : 'Connection failed'}
                    </div>
                    {test.ok ? (
                      <div className="mt-1.5 grid grid-cols-2 gap-1 md:grid-cols-4">
                        <div>equity <span className="mono">{test.equity}</span></div>
                        <div>available <span className="mono">{test.available}</span></div>
                        <div>latency <span className="mono">{test.latency_ms}ms</span></div>
                        <div>positions <span className="mono">{test.open_positions}</span></div>
                      </div>
                    ) : (
                      <div className="mt-1">
                        <div className="mono">{test.error}</div>
                        {test.hint && <div className="mt-1 dim">💡 {test.hint}</div>}
                      </div>
                    )}
                  </motion.div>
                )}
              </div>
            </Panel>

            <Panel title="IP whitelist" bodyClass="p-4">
              <div className="flex flex-col gap-3">
                <div className="glass-solid p-3">
                  <div className="text-[0.6rem] uppercase tracking-wider dim">this server's public IP</div>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="mono text-[1.05rem] accent-text">{ip?.public_ip || '—'}</span>
                    <button className="chip" style={{ cursor: 'pointer' }}
                      onClick={() => { navigator.clipboard?.writeText(ip?.public_ip || ''); pushToast({ kind: 'info', title: 'IP copied' }) }}>
                      <Copy size={11} /> copy
                    </button>
                  </div>
                  <div className="mt-1 text-[0.62rem] dim">hostname: <span className="mono">{ip?.hostname}</span></div>
                  <label className="mt-3 block">
                    <span className="text-[0.58rem] uppercase tracking-wider dim">
                      IP restricted on the Binance key
                    </span>
                    <input
                      className="mt-1 w-full rounded-lg border bg-transparent px-2 py-1.5 mono text-[0.72rem] outline-none"
                      style={{ borderColor: 'var(--sr-border)' }}
                      placeholder={ip?.public_ip || '203.0.113.10'}
                      value={cfg?.binance?.ip_whitelist ?? ''}
                      onChange={(e) => patch('binance', 'ip_whitelist', e.target.value)}
                      onFocus={(e) => {
                        if (!e.target.value && ip?.public_ip) patch('binance', 'ip_whitelist', ip.public_ip)
                      }}
                    />
                  </label>
                  <div className="mt-1 text-[0.6rem] dim">
                    Saved with your keys. Use the exact string you pasted into Binance —
                    it may be a CIDR range or a comma-separated list.
                  </div>
                  <div className="mt-2">
                    <Chip color={ip?.whitelist_confirmed ? 'var(--color-bull)' : 'var(--color-amber)'}>
                      {ip?.whitelist_confirmed ? 'whitelist confirmed' : 'whitelist not confirmed'}
                    </Chip>
                  </div>
                  <button onClick={verifyIp} className="chip mt-2 hover:opacity-80" style={{ cursor: 'pointer' }}>
                    <Globe size={11} /> verify whitelist with Binance
                  </button>
                </div>
                <div className="glass-solid p-3 text-[0.68rem] leading-relaxed">
                  <div className="mb-1 text-[0.6rem] uppercase tracking-wider dim">how to whitelist</div>
                  <ol className="ml-4 list-decimal space-y-1 dim">
                    {(ip?.instructions || []).map((s: string, i: number) => <li key={i}>{s}</li>)}
                  </ol>
                </div>
                <div className="glass-solid p-3 text-[0.68rem] leading-relaxed">
                  <div className="mb-1 text-[0.62rem] uppercase tracking-wider dim">required permissions</div>
                  <div className="dim">
                    ✅ Enable Futures · ✅ Read + Trade<br />
                    ❌ Withdrawals are never needed<br />
                    ⚠️ IP restriction ON — this engine refuses to run wide-open keys
                  </div>
                </div>
              </div>
            </Panel>
          </div>
        )}

        {/* ══════════════════════ RISK ══════════════════════ */}
        {tab === 'risk' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel title="Take-profit / stop-loss system (choose exactly ONE)" bodyClass="p-4">
              <div className="flex flex-col gap-2">
                {[
                  {
                    id: 'indicator_default', title: 'Indicator default risk map',
                    desc: 'SL 1.5×ATR · TP 3.0×ATR — the Pine "Risk Map" defaults. 2:1 reward/risk, smallest loss per stop. Opposite flip also closes instantly.',
                  },
                  {
                    id: 'shadow_3x', title: 'Shadow 3×ATR stop, no fixed target',
                    desc: 'Wide 3×ATR stop, profits run until the indicator flips. Survives more noise, larger loss per stop-out.',
                  },
                  {
                    id: 'custom', title: 'Custom multipliers',
                    desc: 'Set your own SL/TP ATR multiples below. Stop is still clamped inside liquidation.',
                  },
                ].map((opt) => {
                  const on = cfg.risk.risk_mode === opt.id
                  return (
                    <button key={opt.id} onClick={() => patch('risk', 'risk_mode', opt.id)}
                      className="glass p-3 text-left transition-all"
                      style={{
                        borderColor: on ? 'var(--sr-accent)' : undefined,
                        boxShadow: on ? 'var(--sr-glow)' : undefined,
                        cursor: 'pointer',
                      }}>
                      <div className="flex items-center gap-2">
                        <span className="flex h-4 w-4 items-center justify-center rounded-full border"
                          style={{ borderColor: on ? 'var(--sr-accent)' : 'var(--sr-border)' }}>
                          {on && <span className="h-2 w-2 rounded-full" style={{ background: 'var(--sr-accent)' }} />}
                        </span>
                        <span className="text-[0.8rem] font-semibold">{opt.title}</span>
                        {on && <Chip color="var(--sr-accent)">active</Chip>}
                      </div>
                      <div className="mt-1 text-[0.66rem] leading-relaxed dim">{opt.desc}</div>
                    </button>
                  )
                })}

                {cfg.risk.risk_mode === 'custom' && (
                  <div className="glass-solid grid grid-cols-2 gap-3 p-3">
                    <Num label="SL × ATR" value={cfg.risk.custom_sl_atr_mult}
                      onChange={(v) => patch('risk', 'custom_sl_atr_mult', v)} />
                    <Num label="TP × ATR" value={cfg.risk.custom_tp_atr_mult}
                      onChange={(v) => patch('risk', 'custom_tp_atr_mult', v)} />
                    <label className="col-span-2 flex items-center gap-2 text-[0.72rem]">
                      <input type="checkbox" checked={cfg.risk.custom_tp_enabled}
                        onChange={(e) => patch('risk', 'custom_tp_enabled', e.target.checked)} />
                      enable take-profit (uncheck = exit only on reverse signal / stop)
                    </label>
                  </div>
                )}

                <div className="glass-solid flex flex-col gap-3 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="text-[0.8rem] font-semibold">ROI trailing stop</div>
                      <div className="text-[0.66rem] dim">
                        Once a position earns {Number(cfg.risk.trail_activation_roi_pct ?? 25)}% ROI the
                        stop trails {Number(cfg.risk.trail_distance_roi_pct ?? 15)} ROI-points behind the
                        peak — so it starts at{' '}
                        {Math.max(0, Number(cfg.risk.trail_activation_roi_pct ?? 25)
                          - Number(cfg.risk.trail_distance_roi_pct ?? 15))}% ROI and only ratchets up.
                        It <em>moves</em> the one stop that already exists, never adds a second.
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-[0.72rem]">
                      <input type="checkbox" checked={!!cfg.risk.trail_roi_enabled}
                        onChange={(e) => patch('risk', 'trail_roi_enabled', e.target.checked)} />
                      enabled
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                    <Num label="Activate at ROI %" value={cfg.risk.trail_activation_roi_pct ?? 25}
                      step={1} onChange={(v) => patch('risk', 'trail_activation_roi_pct', v)} />
                    <Num label="Trail distance (ROI pts)" value={cfg.risk.trail_distance_roi_pct ?? 15}
                      step={1} onChange={(v) => patch('risk', 'trail_distance_roi_pct', v)} />
                    <Num label="Min re-place step (ROI pts)" value={cfg.risk.trail_min_step_roi_pct ?? 1}
                      step={0.5} onChange={(v) => patch('risk', 'trail_min_step_roi_pct', v)} />
                  </div>
                  <div className="mono text-[0.66rem] dim">
                    ROI is measured on margin (leverage-adjusted, like Binance): at{' '}
                    {cfg.risk.leverage}× a {((Number(cfg.risk.trail_activation_roi_pct ?? 25)
                      / Math.max(1, Number(cfg.risk.leverage) || 1))).toFixed(2)}% price move arms the trail.
                    The stop can never sit past the liquidation price or below break-even.
                  </div>
                </div>

                <div className="glass-solid p-3 text-[0.68rem]">
                  <div className="mb-1 text-[0.6rem] uppercase tracking-wider dim">active system (live)</div>
                  <div className="accent-text">{activeSystem?.risk_mode?.replace(/_/g, ' ')}</div>
                  <div className="mono mt-1 dim">
                    SL {activeSystem?.sl_atr_mult}×ATR ·{' '}
                    TP {activeSystem?.tp_enabled ? `${activeSystem?.tp_atr_mult}×ATR` : 'reverse signal only'} ·{' '}
                    reverse exit {activeSystem?.reverse_signal_exit ? 'on' : 'off'}
                  </div>
                </div>

                <label className="flex items-center gap-2 text-[0.72rem]">
                  <input type="checkbox" checked={cfg.risk.use_reverse_signal_exit}
                    onChange={(e) => patch('risk', 'use_reverse_signal_exit', e.target.checked)} />
                  close instantly when the indicator flips against the position
                </label>
              </div>
            </Panel>

            <Panel title="Sizing, leverage & guards" bodyClass="p-4">
              <div className="grid grid-cols-2 gap-3">
                <Num label="Margin per trade (% of equity)" value={cfg.risk.size_pct_per_trade} step={0.5}
                  onChange={(v) => patch('risk', 'size_pct_per_trade', v)} />
                <Num label="Leverage (×)" value={cfg.risk.leverage} step={1}
                  onChange={(v) => patch('risk', 'leverage', v)} />
                <label className="flex flex-col gap-1">
                  <span className="text-[0.62rem] uppercase tracking-wider dim">Margin type</span>
                  <select value={cfg.risk.margin_type} onChange={(e) => patch('risk', 'margin_type', e.target.value)}
                    className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem]"
                    style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                    <option value="CROSS">CROSS (recommended by operator)</option>
                    <option value="ISOLATED">ISOLATED</option>
                  </select>
                </label>
                <Num label="Max concurrent trades" value={cfg.risk.max_concurrent_trades} step={1}
                  onChange={(v) => patch('risk', 'max_concurrent_trades', v)} />
                <Num label="Min confidence to execute (%)" value={cfg.risk.min_confidence} step={1}
                  onChange={(v) => patch('risk', 'min_confidence', v)} />
                <Num label="Liquidation safety buffer (%)" value={cfg.risk.liq_safety_buffer_pct} step={1}
                  onChange={(v) => patch('risk', 'liq_safety_buffer_pct', v)} />
                <Num label="Max stop distance (%)" value={cfg.risk.max_stop_distance_pct} step={0.5}
                  onChange={(v) => patch('risk', 'max_stop_distance_pct', v)} />
                <Num label="Daily drawdown stop (%) 0 = off" value={cfg.risk.daily_drawdown_stop_pct} step={1}
                  onChange={(v) => patch('risk', 'daily_drawdown_stop_pct', v)} />
              </div>
              <div className="glass-solid mt-3 p-3 text-[0.68rem] leading-relaxed dim">
                <div className="mb-1 text-[0.62rem] uppercase tracking-wider">enforced by the AEGIS risk bot</div>
                • stop always sits between entry and liquidation (never through it)<br />
                • quantity floored to the exchange step — never rounded up<br />
                • one position per symbol · max {cfg.risk.max_concurrent_trades} concurrent<br />
                • entry refused when the API ceiling is reached (protective closes still allowed)
              </div>
              <div className="mt-3">
                <div className="mb-1 flex justify-between text-[0.62rem] dim">
                  <span>projected exposure with current settings</span>
                  <span className="mono">
                    {(cfg.risk.size_pct_per_trade * cfg.risk.leverage / 100).toFixed(2)}× equity per trade
                  </span>
                </div>
                <Bar value={(cfg.risk.size_pct_per_trade * cfg.risk.leverage / 100) * 10}
                  color="var(--color-amber)" />
                <div className="mt-1 text-[0.6rem] dim">
                  {(cfg.risk.size_pct_per_trade * cfg.risk.leverage * cfg.risk.max_concurrent_trades / 100).toFixed(1)}×
                  equity notional if all {cfg.risk.max_concurrent_trades} slots fill
                </div>
              </div>
            </Panel>
          </div>
        )}

        {/* ══════════════════════ INDICATOR ══════════════════════ */}
        {tab === 'indicator' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel title="Ghost Candle with Shadow Rail — Pine inputs" bodyClass="p-4">
              <div className="grid grid-cols-2 gap-3">
                <Num label="Trend sensitivity (bars)" value={cfg.indicator.swingBars} step={1}
                  onChange={(v) => patch('indicator', 'swingBars', v)} />
                <Num label="Rail spread" value={cfg.indicator.railSpread} step={0.1}
                  onChange={(v) => patch('indicator', 'railSpread', v)} />
                <Num label="Rail drive (0–99)" value={cfg.indicator.railDrive} step={0.1}
                  onChange={(v) => patch('indicator', 'railDrive', v)} />
                <Num label="Ghost smoothing" value={cfg.indicator.ghostBlur} step={1}
                  onChange={(v) => patch('indicator', 'ghostBlur', v)} />
                <Num label="Ghost gap (× ATR)" value={cfg.indicator.ghostOffset} step={0.1}
                  onChange={(v) => patch('indicator', 'ghostOffset', v)} />
                <Num label="Ghost glide" value={cfg.indicator.ghostEase} step={1}
                  onChange={(v) => patch('indicator', 'ghostEase', v)} />
                <label className="flex flex-col gap-1">
                  <span className="text-[0.62rem] uppercase tracking-wider dim">Ghost side</span>
                  <select value={cfg.indicator.ghostPlacement}
                    onChange={(e) => patch('indicator', 'ghostPlacement', e.target.value)}
                    className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem]"
                    style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                    {['Trend', 'Above', 'Below'].map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[0.62rem] uppercase tracking-wider dim">Signal timeframe</span>
                  <select value={cfg.indicator.timeframe}
                    onChange={(e) => patch('indicator', 'timeframe', e.target.value)}
                    className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem]"
                    style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                    {['1m', '3m', '5m', '15m', '30m'].map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                </label>
              </div>
            </Panel>

            <Panel title="Flip filters & higher-timeframe gate" bodyClass="p-4">
              <div className="flex flex-col gap-3">
                <label className="flex items-center gap-2 text-[0.72rem]">
                  <input type="checkbox" checked={cfg.indicator.mtfGate}
                    onChange={(e) => patch('indicator', 'mtfGate', e.target.checked)} />
                  filter flips with the higher timeframe EMA
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1">
                    <span className="text-[0.62rem] uppercase tracking-wider dim">Higher timeframe</span>
                    <select value={cfg.indicator.mtfFrame}
                      onChange={(e) => patch('indicator', 'mtfFrame', e.target.value)}
                      className="rounded-xl border bg-transparent px-3 py-2 text-[0.75rem]"
                      style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
                      {['15m', '30m', '60', '1h', '4h', '240', '1d'].map((o) =>
                        <option key={o} value={o}>{o === '60' ? '1h (60)' : o === '240' ? '4h (240)' : o}</option>)}
                    </select>
                  </label>
                  <Num label="HTF EMA length" value={cfg.indicator.mtfEmaBars} step={1}
                    onChange={(v) => patch('indicator', 'mtfEmaBars', v)} />
                  <Num label="Min trend quality (%)" value={cfg.indicator.minTrendPct} step={1}
                    onChange={(v) => patch('indicator', 'minTrendPct', v)} />
                  <Num label="History warmup (bars)" value={cfg.indicator.kline_warmup} step={100}
                    onChange={(v) => patch('indicator', 'kline_warmup', v)} />
                </div>
                <label className="flex items-center gap-2 text-[0.72rem]">
                  <input type="checkbox" checked={cfg.indicator.require_strong_flip}
                    onChange={(e) => patch('indicator', 'require_strong_flip', e.target.checked)} />
                  only trade STRONG flips (trend quality ≥ 60%)
                </label>
                <div className="glass-solid p-3 text-[0.68rem] leading-relaxed dim">
                  The engine ports the Pine logic 1:1 (state machine, ghost candles, trend quality,
                  ATR risk map). With the HTF gate on, symbols are skipped until the HTF EMA is warm —
                  this prevents a one-sided (short-only) signal stream after a fresh start.
                </div>
                <div className="flex gap-2">
                  <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
                    onClick={async () => setSelftest(await endpoints.indicatorTest())}>
                    <Activity size={12} /> run indicator selftest
                  </button>
                  <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
                    onClick={() => location.reload()}>
                    <RefreshCw size={12} /> reload dashboard
                  </button>
                </div>
                {selftest && (
                  <div className="glass-solid p-3 text-[0.68rem]">
                    <div className="mb-1 font-semibold"
                      style={{ color: selftest.ok ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                      {selftest.ok ? '✓ port healthy' : '✗ check failed'}
                    </div>
                    <div className="mono dim">
                      bars {selftest.bars} · confirmed flips {selftest.flips_confirmed} ·
                      raw {selftest.flips_raw} · rail bars {selftest.rail_bars} ·
                      atr {selftest.last_atr?.toFixed(5)} · quality {selftest.last_clean_ratio?.toFixed(3)}
                    </div>
                  </div>
                )}
              </div>
            </Panel>
          </div>
        )}

        {/* ══════════════════════ ENGINE ══════════════════════ */}
        {tab === 'engine' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel title="Swarm composition" bodyClass="p-4">
              <div className="grid grid-cols-2 gap-3">
                <Num label="Scanner bots" value={cfg.engine.scanner_bots} step={1}
                  onChange={(v) => patch('engine', 'scanner_bots', v)} />
                <Num label="Assets per scanner" value={cfg.engine.assets_per_bot} step={1}
                  onChange={(v) => patch('engine', 'assets_per_bot', v)} />
                <Num label="Analyst slots" value={cfg.engine.analyst_slots} step={1}
                  onChange={(v) => patch('engine', 'analyst_slots', v)} />
                <Num label="Execution bots" value={cfg.engine.execution_bots} step={1}
                  onChange={(v) => patch('engine', 'execution_bots', v)} />
                <Num label="Trade monitors" value={cfg.engine.monitor_bots} step={1}
                  onChange={(v) => patch('engine', 'monitor_bots', v)} />
                <Num label="Universe size" value={cfg.engine.universe_size} step={10}
                  onChange={(v) => patch('engine', 'universe_size', v)} />
                <Num label="Scan cycle (s)" value={cfg.engine.scan_cycle_s} step={30}
                  onChange={(v) => patch('engine', 'scan_cycle_s', v)} />
                <Num label="Monitor tick (s)" value={cfg.engine.monitor_tick_s} step={1}
                  onChange={(v) => patch('engine', 'monitor_tick_s', v)} />
                <Num label="Connector health check (s)" value={cfg.engine.connector_health_interval_s} step={30}
                  onChange={(v) => patch('engine', 'connector_health_interval_s', v)} />
                <Num label="Simulation speed ×" value={cfg.engine.sim_time_accel} step={5}
                  onChange={(v) => patch('engine', 'sim_time_accel', v)} />
              </div>
              <div className="mt-3 text-[0.66rem] dim">
                Current allocation: {status?.scanner_buckets?.join(' · ')} assets per scanner.
              </div>
            </Panel>

            <Panel title="API weight governor (VAULT)" bodyClass="p-4">
              <div className="grid grid-cols-2 gap-3">
                <Num label="IP budget (weight/min)" value={cfg.engine.api_weight_limit_per_min} step={100}
                  onChange={(v) => patch('engine', 'api_weight_limit_per_min', v)} />
                <Num label="Hard ceiling (%)" value={cfg.engine.api_budget_pct} step={1}
                  onChange={(v) => patch('engine', 'api_budget_pct', v)} />
              </div>
              <label className="mt-3 flex items-center gap-2 text-[0.72rem]">
                <input type="checkbox" checked={cfg.engine.allow_critical_above_cap}
                  onChange={(e) => patch('engine', 'allow_critical_above_cap', e.target.checked)} />
                allow critical calls above the ceiling (not recommended)
              </label>
              <div className="glass-solid mt-3 p-3 text-[0.68rem] leading-relaxed dim">
                Every REST call is pre-charged against a sliding 60-second window and attributed to the
                bot that made it. At {cfg.engine.api_budget_pct}% nobody may send anything — new entries
                are refused and the Connector Bot raises an SOS. Protective closes are always allowed
                so a position can never be trapped by a rate limit.
              </div>
              <div className="mt-3">
                <Bar value={status?.api?.cap_used_pct ?? 0}
                  color={(status?.api?.cap_used_pct ?? 0) > 80 ? 'var(--color-bear)' : 'var(--sr-accent)'} glow />
                <div className="mt-1.5 grid grid-cols-3 gap-2 text-[0.62rem] dim">
                  <span>used <span className="mono">{(status?.api?.used_pct ?? 0).toFixed(1)}%</span></span>
                  <span>headroom <span className="mono">{status?.api?.available ?? 0}</span></span>
                  <span>blocked <span className="mono">{status?.api?.blocked_total ?? 0}</span></span>
                </div>
              </div>
            </Panel>
          </div>
        )}

        {/* ══════════════════════ DANGER ══════════════════════ */}
        {tab === 'danger' && (
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
            <Panel title="Operator overrides" bodyClass="p-4" accent="var(--color-amber)">
              <div className="flex flex-col gap-3 text-[0.72rem]">
                <div className="glass-solid p-3">
                  <div className="mb-1 font-semibold">Pause / resume trading</div>
                  <div className="mb-2 dim">Scanners keep observing; no new entries are opened.</div>
                  <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
                    onClick={() => endpoints.enginePause(!status?.paused).then(() =>
                      pushToast({ kind: 'warn', title: status?.paused ? 'Trading resumed' : 'Trading paused' }))}>
                    {status?.paused ? 'resume' : 'pause'} engine
                  </button>
                </div>
                <div className="glass-solid p-3">
                  <div className="mb-1 font-semibold">Re-sync from the exchange</div>
                  <div className="mb-2 dim">Adopt open positions and re-read balances from Binance.</div>
                  <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
                    onClick={() => endpoints.engineStop().then(() => endpoints.engineStart()).then(() =>
                      pushToast({ kind: 'info', title: 'Engine restarted — journal reconciled' }))}>
                    restart engine
                  </button>
                </div>
                <div className="glass-solid p-3 text-[0.68rem]">
                  <div className="mb-1 font-semibold">Confidence model</div>
                  <div className="dim">
                    The analyst team blends a 7-factor heuristic with a logistic model that retrains on
                    your own closed trades once ≥60 are recorded. Weights and coefficients live in
                    <span className="mono"> data/confidence_model.json</span>.
                  </div>
                </div>
              </div>
            </Panel>

            <Panel title="Danger zone" bodyClass="p-4" accent="var(--color-bear)">
              <div className="flex flex-col gap-3 text-[0.72rem]">
                <div className="glass-solid p-3" style={{ borderColor: 'color-mix(in oklab, var(--color-bear) 40%, transparent)' }}>
                  <div className="mb-1 font-semibold" style={{ color: 'var(--color-bear)' }}>Emergency flatten</div>
                  <div className="mb-2 dim">Market-closes every engine position immediately.</div>
                  <button className="chip hover:opacity-80"
                    style={{ cursor: 'pointer', borderColor: 'var(--color-bear)', color: 'var(--color-bear)' }}
                    onClick={() => {
                      if (confirm('This closes ALL open positions at market. Continue?')) {
                        endpoints.emergencyClose().then((r) => {
                          pushToast({ kind: 'warn', title: 'Flatten executed', body: JSON.stringify(r) })
                          refresh()
                        })
                      }
                    }}>
                    <ShieldAlert size={12} /> FLATTEN NOW
                  </button>
                </div>
                <div className="glass-solid p-3">
                  <div className="mb-1 font-semibold">Starting balance lock</div>
                  <div className="mb-2 dim">
                    The main page shows the balance captured on the very first successful Binance
                    connect. Clearing it makes the engine lock the *next* balance instead.
                  </div>
                  <div className="mono mb-2">current lock: ${(cfg && status?.mode ? '' : '')}
                    locked in journal (see main page)</div>
                  <div className="text-[0.64rem] dim">
                    Use with care: it changes the reference point for every P&L figure.
                  </div>
                </div>
              </div>
            </Panel>
          </div>
        )}
      </div>
    </div>
  )
}

const Num: React.FC<{
  label: string; value: number; step?: number; onChange: (v: number) => void
}> = ({ label, value, step = 1, onChange }) => (
  <label className="flex flex-col gap-1">
    <span className="text-[0.62rem] uppercase tracking-wider dim">{label}</span>
    <input type="number" value={value} step={step}
      onChange={(e) => onChange(Number(e.target.value))}
      className="mono rounded-xl border bg-transparent px-3 py-2 text-[0.78rem] outline-none"
      style={{ borderColor: 'var(--sr-border)' }} />
  </label>
)
