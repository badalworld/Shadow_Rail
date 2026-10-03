import React, { useMemo, useState } from 'react'
import { Award, Crown, Filter } from 'lucide-react'
import { WORKFLOW_STAGES, useStore } from '../state/store'
import { endpoints } from '../lib/api'
import { Bar, Chip, Panel, fmtNum, moodEmoji, statusColor, timeAgo } from '../components/Glass'
import { BotAvatar } from '../components/BotAvatar'
import type { Bot } from '../lib/types'

const RANK_ORDER = ['Recruit', 'Operative', 'Specialist', 'Elite', 'Legend']

export const Bots: React.FC = () => {
  const { bots, botMap, api, pushToast, status } = useStore()
  const [group, setGroup] = useState('all')
  const [selected, setSelected] = useState<string | null>(null)
  const [detail, setDetail] = useState<any>(null)

  const groups = useMemo(() => ['all', ...Array.from(new Set(bots.map((b) => b.group)))], [bots])
  const shown = useMemo(() => (group === 'all' ? bots : bots.filter((b) => b.group === group)), [bots, group])
  const bot = selected ? botMap[selected] : null

  const openBot = async (id: string) => {
    setSelected(id)
    try { setDetail(await endpoints.bot(id)) } catch { setDetail(null) }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/* ── the pipeline the swarm is currently walking ─────────────────── */}
      <Panel
        title="Pipeline stages"
        right={
          <div className="flex flex-wrap items-center gap-2">
            <Chip color="var(--color-cyan)">{(status?.workflow?.stage || 'idle').toString()}</Chip>
            <Chip>cycle #{status?.cycle ?? 0}</Chip>
          </div>
        }
        bodyClass="p-2"
      >
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
          {WORKFLOW_STAGES.map((s, i) => {
            const st = (status?.workflow?.stages || {})[s.key] || { status: 'idle', detail: '' }
            const active = st.status === 'start'
            const done = st.status === 'done'
            const color = statusColor(st.status === 'done' ? 'success'
              : st.status === 'error' ? 'error' : st.status)
            return (
              <div key={s.key} className="glass-row px-2 py-1.5"
                style={active ? { borderColor: color, boxShadow: 'var(--sr-glow)' } : undefined}>
                <div className="flex items-center gap-1.5">
                  <span className={`pulse-dot ${active ? 'animate-pulse' : ''}`}
                    style={{ background: color, width: 7, height: 7 }} />
                  <span className="mono text-[0.58rem] dim">{String(i + 1).padStart(2, '0')}</span>
                  <span className="truncate text-[0.72rem]">{s.label}</span>
                </div>
                <p className="truncate text-[0.6rem] dim" title={st.detail}>
                  {done ? 'done' : st.detail || st.status}
                </p>
              </div>
            )
          })}
        </div>
      </Panel>

      <div className="flex flex-wrap items-center gap-2">
        <Chip color="var(--color-bull)">{bots.length} agents online</Chip>
        <Chip color="var(--color-cyan)">{bots.filter((b) => b.status === 'working').length} working</Chip>
        <Chip color="var(--color-amber)">{bots.filter((b) => b.status === 'blocked' || b.status === 'error').length} attention</Chip>
        <Chip>{bots.reduce((a, b) => a + b.promotions, 0)} promotions earned</Chip>
        <div className="ml-auto flex items-center gap-1.5">
          <Filter size={12} className="dim" />
          {groups.map((g) => (
            <button key={g} onClick={() => setGroup(g)} className="chip hover:opacity-80"
              style={{ cursor: 'pointer', borderColor: group === g ? 'var(--sr-accent)' : undefined }}>
              {g}
            </button>
          ))}
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 xl:grid-cols-[2.6fr_1fr]">
        <Panel title="Bot roster — every agent, live" bodyClass="scroll-thin overflow-auto p-3">
          <div className="grid grid-cols-1 gap-2.5 md:grid-cols-2 2xl:grid-cols-3">
            {shown.map((b) => (
              <button key={b.bot_id} onClick={() => openBot(b.bot_id)}
                className="glass flex items-start gap-3 p-3 text-left transition-transform hover:scale-[1.015]"
                style={{
                  borderColor: selected === b.bot_id ? 'var(--sr-accent)' : undefined,
                  boxShadow: selected === b.bot_id ? 'var(--sr-glow)' : undefined,
                }}>
                <BotAvatar bot={b} size={58} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-[0.82rem] font-semibold" style={{ color: b.color }}>
                      {b.name}
                    </span>
                    {b.rank_index >= 3 && <Crown size={11} style={{ color: 'var(--color-amber)' }} />}
                    <span className="ml-auto mono text-[0.6rem] dim">
                      {moodEmoji(b.mood)} {b.rank}
                    </span>
                  </div>
                  <div className="truncate text-[0.62rem] dim">{b.role}</div>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <span className="inline-block h-1.5 w-1.5 rounded-full"
                      style={{ background: statusColor(b.status) }} />
                    <span className="text-[0.62rem]" style={{ color: statusColor(b.status) }}>{b.status}</span>
                    <span className="truncate text-[0.6rem] dim">· {b.task}</span>
                  </div>
                  <div className="mt-1.5"><Bar value={b.progress * 100} height={4} color={statusColor(b.status)} /></div>
                  <div className="mt-1.5 grid grid-cols-4 gap-1 text-[0.56rem] dim">
                    <span>tasks <span className="mono">{b.metrics.tasks_done}</span></span>
                    <span>score <span className="mono">{b.metrics.score.toFixed(0)}</span></span>
                    <span>W/L <span className="mono">{b.metrics.wins}/{b.metrics.losses}</span></span>
                    <span>api <span className="mono">{b.metrics.api_spent_window}</span></span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title={bot ? `${bot.name} · ${bot.role}` : 'Agent dossier'}
          right={bot && <Chip color={statusColor(bot.status)}>{bot.rank}</Chip>}
          bodyClass="scroll-thin overflow-auto p-3">
          {!bot ? (
            <div className="flex h-full items-center justify-center text-[0.72rem] dim">
              Select an agent to open its dossier — metrics, promotions and its own log.
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <BotAvatar bot={bot} size={86} />
                <div className="min-w-0">
                  <div className="text-[1rem] font-bold" style={{ color: bot.color }}>{bot.name}</div>
                  <div className="text-[0.66rem] dim">{bot.bot_id}</div>
                  <div className="mt-1 flex items-center gap-1.5">
                    <Chip color={statusColor(bot.status)}>{bot.status}</Chip>
                    <Chip color="var(--color-amber)">{bot.rank}</Chip>
                    {bot.promotions > 0 && <Chip color="var(--color-bull)">{bot.promotions}× promoted</Chip>}
                  </div>
                </div>
              </div>

              <div>
                <div className="mb-1 flex justify-between text-[0.62rem] dim">
                  <span>field rank progress</span><span className="mono">{bot.metrics.score.toFixed(1)} / 100</span>
                </div>
                <Bar value={bot.metrics.score} color={bot.color} glow />
                <div className="mt-1 flex justify-between text-[0.55rem] dim">
                  {RANK_ORDER.map((r, i) => (
                    <span key={r} style={{ opacity: i <= bot.rank_index ? 1 : 0.4 }}>{r}</span>
                  ))}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                {[
                  ['tasks done', bot.metrics.tasks_done],
                  ['tasks failed', bot.metrics.tasks_failed],
                  ['errors', bot.metrics.errors],
                  ['wins', bot.metrics.wins],
                  ['losses', bot.metrics.losses],
                  ['avg latency', `${bot.metrics.avg_latency_ms.toFixed(0)} ms`],
                  ['api window', bot.metrics.api_spent_window],
                  ['api blocked', bot.metrics.api_blocked],
                ].map(([k, v]) => (
                  <div key={k as string} className="glass-solid px-2.5 py-1.5">
                    <div className="text-[0.55rem] uppercase tracking-wider dim">{k}</div>
                    <div className="mono text-[0.8rem]">{v as any}</div>
                  </div>
                ))}
              </div>

              <div className="glass-solid p-2.5">
                <div className="text-[0.58rem] uppercase tracking-wider dim">current work</div>
                <div className="text-[0.7rem]">{bot.task}</div>
                <div className="mt-0.5 text-[0.65rem] dim">{bot.message}</div>
                <div className="mt-1"><Bar value={bot.progress * 100} color={statusColor(bot.status)} /></div>
              </div>

              {bot.assigned.length > 0 && (
                <div className="glass-solid p-2.5">
                  <div className="mb-1 text-[0.58rem] uppercase tracking-wider dim">
                    assigned ({bot.assigned.length})
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {bot.assigned.slice(0, 30).map((a) => (
                      <span key={a} className="chip" style={{ fontSize: '0.55rem' }}>{a}</span>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <div className="mb-1.5 flex items-center justify-between text-[0.58rem] uppercase tracking-wider dim">
                  <span>agent log</span>
                  <button className="chip hover:opacity-80"
                    onClick={() => endpoints.promote(bot.bot_id).then(() => {
                      pushToast({ kind: 'success', title: `${bot.name} promoted` })
                      openBot(bot.bot_id)
                    })}>
                    <Award size={10} /> promote
                  </button>
                </div>
                <div className="flex max-h-56 flex-col gap-1 overflow-auto scroll-thin">
                  {(detail?.logs || []).map((l: any) => (
                    <div key={l.id} className="glass-solid px-2 py-1 text-[0.62rem]">
                      <span className="mono dim">{new Date(l.ts).toLocaleTimeString('en-GB', { hour12: false })}</span>
                      <span className="ml-1.5" style={{
                        color: l.level === 'error' ? 'var(--color-bear)'
                          : l.level === 'success' ? 'var(--color-bull)' : 'var(--text)',
                      }}>{l.message.slice(0, 150)}</span>
                    </div>
                  ))}
                  {!detail?.logs?.length && <div className="text-[0.65rem] dim">no activity recorded yet</div>}
                </div>
              </div>

              <div className="text-[0.6rem] dim">
                api window spend {fmtNum(api?.per_bot?.[bot.bot_id]?.spent_window ?? 0, 0)} ·
                total {fmtNum(api?.per_bot?.[bot.bot_id]?.spent_total ?? 0, 0)} ·
                blocked {api?.per_bot?.[bot.bot_id]?.blocked ?? 0} ·
                last active {timeAgo(bot.last_active * 1000)}
              </div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}
