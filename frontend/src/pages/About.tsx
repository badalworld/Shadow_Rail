import React, { useEffect, useState } from 'react'
import { Github, Mail, Send, Zap } from 'lucide-react'
import { endpoints } from '../lib/api'
import { BOOT } from '../state/store'
import { Chip, Panel } from '../components/Glass'

export const About: React.FC = () => {
  const [data, setData] = useState<any>(BOOT.about ?? null)

  useEffect(() => {
    if (BOOT.about) return            // already hydrated at first paint
    endpoints.about().then(setData).catch(() => {})
  }, [])

  const dev = data?.developer || {}
  const project = data?.project || {}

  return (
    <div className="scroll-thin flex h-full min-h-0 flex-col gap-3 overflow-y-auto pr-0.5">
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[1.15fr_1fr]">
        <Panel title="Developer" bodyClass="p-5">
          <div className="flex items-start gap-4">
            <img src="/logo.svg" alt="Shadow Rail" width={92} height={92} className="rounded-2xl" />
            <div className="min-w-0">
              <div className="text-[1.25rem] font-bold accent-text glow-text">
                {dev.name || 'badalworld'}
              </div>
              <div className="text-[0.78rem] dim">{dev.role}</div>
              <div className="mt-2 text-[0.72rem] italic dim">"{dev.tagline}"</div>
              <div className="mt-3 flex flex-wrap gap-2">
                {dev.github && (
                  <a href={dev.github} target="_blank" rel="noreferrer" className="chip hover:opacity-80">
                    <Github size={12} /> repository
                  </a>
                )}
                {dev.email && (
                  <a href={`mailto:${dev.email}`} className="chip hover:opacity-80">
                    <Mail size={12} /> {dev.email}
                  </a>
                )}
                {dev.telegram && (
                  <a href={dev.telegram} target="_blank" rel="noreferrer" className="chip hover:opacity-80">
                    <Send size={12} /> telegram
                  </a>
                )}
              </div>
            </div>
          </div>

          <div className="glass-solid mt-4 p-3 text-[0.7rem] leading-relaxed dim">
            Shadow Rail is a full-stack automatic trading engine built around a single idea: the
            indicator decides <em>direction</em>, the swarm decides <em>quality</em>, and the risk
            layer decides <em>size</em> — with a 95%-of-API-budget ceiling, a liquidation-aware stop,
            and exactly one take-profit/stop-loss system active at any moment.
          </div>

          <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
            {[
              ['bots', project.bots],
              ['timeframe', project.timeframe],
              ['htf filter', project.htf_filter],
              ['api ceiling', project.api_budget_pct != null ? `${project.api_budget_pct}%` : '—'],
              ['mode', project.mode],
              ['transport', project.transport],
            ].map(([k, v]) => (
              <div key={k as string} className="glass-solid px-3 py-2">
                <div className="text-[0.55rem] uppercase tracking-wider dim">{k}</div>
                <div className="mono text-[0.8rem]">{String(v ?? '—')}</div>
              </div>
            ))}
          </div>
        </Panel>

        <div className="flex flex-col gap-3">
          <Panel title="The indicator" bodyClass="p-4">
            <div className="text-[0.76rem] font-semibold">Ghost Candle with Shadow Rail (GCSR)</div>
            <div className="text-[0.68rem] dim">© ChartTrader-X · TradingView · MPL-2.0</div>
            <div className="mt-2 text-[0.7rem] leading-relaxed dim">
              The Shadow Rail state machine tracks a self-adjusting rail that chases price with
              asymmetric damping (slow upward, fast downward), while trend quality compares net
              directional movement against total wander. Flips are confirmed only when the higher
              timeframe agrees, and the ATR risk map defines entry, stop and target.
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <a className="chip hover:opacity-80"
                href="https://www.tradingview.com/script/AY5Gz97v-Ghost-Candle-with-Shadow-Rail-Px/"
                target="_blank" rel="noreferrer">
                <Zap size={12} /> open the script on TradingView
              </a>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {['state machine port', 'htf gate', 'trend quality', 'atr risk map',
                'strong-flip tier', 'no-repaint htf'].map((t) => <Chip key={t}>{t}</Chip>)}
            </div>
          </Panel>

          <Panel title="Workflow commitments" bodyClass="p-4">
            <ul className="flex flex-col gap-1.5 text-[0.7rem] dim">
              <li>• Connector Bot verifies the Binance link every 5 minutes; a failure raises SOS and
                turns the whole dashboard red.</li>
              <li>• 5 scanner bots sweep 30 volatility-ranked assets each, every 5-minute candle.</li>
              <li>• 10 analyst bots score opportunities; nothing executes below the confidence floor.</li>
              <li>• Execution sets the exchange-side stop <em>and</em> the target in one system — never both.</li>
              <li>• The Info Bot verifies position, quantity, stop and target before the monitors take over.</li>
              <li>• Trade Manager keeps history, P&L, win-rate, fees and funding; Equity Manager releases
                8% of equity as margin at 10× cross.</li>
              <li>• Wins make the swarm celebrate; losses make it mourn; sustained good work earns promotions.</li>
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  )
}
