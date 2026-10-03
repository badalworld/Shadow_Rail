import React, { useEffect, useMemo, useState } from 'react'
import { Award, Crosshair, ExternalLink, X } from 'lucide-react'
import { Bar, Chip, moodEmoji, statusColor, timeAgo } from '../Glass'
import { BotAvatar } from '../BotAvatar'
import { endpoints } from '../../lib/api'
import type { Bot } from '../../lib/types'
import { STATIONS, STATION_BY_GROUP, stationOf } from './layout'
import type { StationKey } from './layout'

/**
 * The agent card the operator opens by clicking a body in the headquarters.
 * Live fields come from the websocket-fed store; history comes from
 * `/api/bots/{id}` (its own log tail + the journal's per-bot statistics).
 */
export const HQPanel: React.FC<{
  bot: Bot
  onClose: () => void
  onFocus?: (key: StationKey | null) => void
  onPromote?: (id: string) => void
  onOpenRoster?: () => void
  className?: string
}> = ({ bot, onClose, onFocus, onPromote, onOpenRoster, className = '' }) => {
  const [detail, setDetail] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let dead = false
    setDetail(null)
    endpoints.bot(bot.bot_id)
      .then((d) => { if (!dead) setDetail(d) })
      .catch(() => { if (!dead) setDetail({ logs: [], stats: {} }) })
    return () => { dead = true }
  }, [bot.bot_id])

  const station: StationKey = STATION_BY_GROUP[bot.group] || 'command'
  const st = stationOf(station)
  const stats = detail?.stats || {}
  const closed = stats.trades ?? stats.closed ?? 0
  const wins = stats.wins ?? bot.metrics?.wins ?? 0
  const losses = stats.losses ?? bot.metrics?.losses ?? 0
  const winRate = closed ? (wins / Math.max(1, wins + losses)) * 100 : 0
  const logs = useMemo(() => (detail?.logs || []).slice(0, 8), [detail])

  const promote = async () => {
    setBusy(true)
    try { await endpoints.promote(bot.bot_id); onPromote?.(bot.bot_id) } catch { /* toast handled by store */ }
    setBusy(false)
  }

  return (
    <div className={`glass-solid w-[21rem] max-w-[92vw] p-3 ${className}`}
      style={{ borderColor: `color-mix(in oklab, ${bot.color} 45%, transparent)` }}>
      <div className="flex items-start justify-between gap-2">
        <span className="text-[0.58rem] uppercase tracking-[0.18em] dim">agent card</span>
        <button className="chip" style={{ cursor: 'pointer' }} onClick={onClose} title="Close">
          <X size={11} />
        </button>
      </div>

      <div className="mt-2 flex items-center gap-3">
        <BotAvatar bot={bot} size={54} />
        <div className="min-w-0 flex-1">
          <p className="mono truncate text-[0.86rem]">{bot.name}</p>
          <p className="truncate text-[0.64rem] dim">{bot.role}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <Chip color={statusColor(bot.status)}>
              {moodEmoji(bot.mood)} {bot.status}
            </Chip>
            <Chip color={st.accent}>{bot.rank}</Chip>
          </div>
        </div>
      </div>

      <div className="mt-2">
        <div className="flex items-center justify-between text-[0.6rem] dim">
          <span className="truncate pr-2">{bot.task || bot.message || 'standing by'}</span>
          <span className="mono">{Math.round(bot.progress || 0)}%</span>
        </div>
        <Bar value={bot.progress || 0} color={bot.color} glow />
      </div>

      <div className="mono mt-2 grid grid-cols-3 gap-1 text-[0.6rem]">
        {[
          ['score', Math.round(bot.metrics?.score ?? 0)],
          ['tasks', bot.metrics?.tasks_done ?? 0],
          ['errors', bot.metrics?.errors ?? 0],
          ['api window', bot.metrics?.api_spent_window ?? 0],
          ['wins', wins],
          ['losses', losses],
        ].map(([k, v]) => (
          <div key={String(k)} className="glass-row px-2 py-1">
            <p className="dim uppercase tracking-wider">{k}</p>
            <p className="text-[0.7rem]">{v as any}</p>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1 text-[0.6rem]">
        <Chip>station: {st.label}</Chip>
        {!!closed && <Chip>closed {closed}</Chip>}
        {!!(wins + losses) && <Chip color="var(--color-bull)">win rate {winRate.toFixed(0)}%</Chip>}
        {!!bot.promotions && <Chip color="var(--color-amber)">promotions {bot.promotions}</Chip>}
        <Chip>last active {bot.last_active ? timeAgo(bot.last_active) : '—'}</Chip>
      </div>

      {logs.length > 0 && (
        <div className="scroll-thin mt-2 max-h-[6.5rem] overflow-y-auto">
          {logs.map((l: any, i: number) => (
            <p key={i} className="mono truncate text-[0.58rem] dim" title={l.message}>
              <span className="dim">{timeAgo(l.ts)}</span> · {l.message}
            </p>
          ))}
        </div>
      )}

      <div className="mt-2 flex gap-1.5">
        <button className="chip flex items-center gap-1" style={{ cursor: 'pointer' }}
          onClick={() => onFocus?.(station)}>
          <Crosshair size={11} /> locate
        </button>
        {bot.rank_index < 4 && (
          <button className="chip flex items-center gap-1" style={{ cursor: 'pointer' }}
            disabled={busy} onClick={promote} title="Promote this agent to its next designation">
            <Award size={11} /> {busy ? 'promoting…' : 'promote'}
          </button>
        )}
        {onOpenRoster && (
          <button className="chip ml-auto flex items-center gap-1" style={{ cursor: 'pointer' }}
            onClick={onOpenRoster} title="Open the full roster">
            roster <ExternalLink size={10} />
          </button>
        )}
        <a className={`chip flex items-center gap-1 ${onOpenRoster ? '' : 'ml-auto'}`}
          href={`/api/bots/${bot.bot_id}`} target="_blank" rel="noreferrer">
          json <ExternalLink size={10} />
        </a>
      </div>
    </div>
  )
}

export default HQPanel
