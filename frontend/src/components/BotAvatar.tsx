import React from 'react'
import type { Bot } from '../lib/types'

/**
 * Procedural hologram avatar — every bot gets a unique, deterministic identity
 * (geometry + rings + sigil derived from its id) instead of a stock photo, so
 * the roster stays visually consistent with the liquid-glass/hacker theme.
 */

function hash(str: string): number {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return Math.abs(h)
}

export const BotAvatar: React.FC<{
  bot: Bot
  size?: number
  status?: string
  mood?: string
  animated?: boolean
}> = ({ bot, size = 64, status, mood, animated = true }) => {
  const h = hash(bot.bot_id)
  const hue = h % 360
  const color = bot.color || `hsl(${hue} 90% 62%)`
  const ringCount = 2 + (h % 3)
  const spokes = 5 + (h % 5)
  const rot = h % 360
  const st = status || bot.status
  const pulse = animated && (st === 'working' || st === 'success' || st === 'celebrating')

  const paths: React.ReactNode[] = []
  for (let i = 0; i < spokes; i++) {
    const a = (i / spokes) * Math.PI * 2 + (rot * Math.PI) / 180
    const x1 = 50 + Math.cos(a) * 14
    const y1 = 50 + Math.sin(a) * 14
    const x2 = 50 + Math.cos(a) * 33
    const y2 = 50 + Math.sin(a) * 33
    paths.push(<line key={`s${i}`} x1={x1} y1={y1} x2={x2} y2={y2} stroke={color}
      strokeWidth="1.1" opacity="0.55" />)
  }

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg viewBox="0 0 100 100" width={size} height={size}>
        <defs>
          <radialGradient id={`g-${bot.bot_id}`} cx="50%" cy="35%">
            <stop offset="0%" stopColor={color} stopOpacity="0.95" />
            <stop offset="60%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0.04" />
          </radialGradient>
          <linearGradient id={`l-${bot.bot_id}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#ffffff" stopOpacity="0.85" />
            <stop offset="100%" stopColor={color} stopOpacity="0.15" />
          </linearGradient>
        </defs>

        {/* outer glassy shell */}
        <circle cx="50" cy="50" r="46" fill="rgba(255,255,255,0.03)" stroke={color}
          strokeOpacity="0.5" strokeWidth="1.2" />
        <circle cx="50" cy="50" r="46" fill={`url(#g-${bot.bot_id})`} opacity="0.55" />

        {/* rotating orbital rings */}
        <g style={{ transformOrigin: '50px 50px', animation: pulse ? 'sr-spin 9s linear infinite' : undefined }}>
          {Array.from({ length: ringCount }).map((_, i) => (
            <ellipse key={i} cx="50" cy="50" rx={40 - i * 6} ry={(40 - i * 6) * (0.35 + i * 0.22)}
              fill="none" stroke={color} strokeOpacity={0.35} strokeWidth="0.9"
              transform={`rotate(${rot + i * 33} 50 50)`} />
          ))}
        </g>

        {paths}
        <circle cx="50" cy="50" r="13" fill={`url(#l-${bot.bot_id})`} stroke={color} strokeOpacity="0.8" />
        <circle cx="50" cy="50" r="4.6" fill={color} style={{ filter: `drop-shadow(0 0 6px ${color})` }} />
      </svg>
      <style>{`@keyframes sr-spin { to { transform: rotate(360deg) } }`}</style>
      <div className="pointer-events-none absolute inset-0 flex items-end justify-center">
        <span className="text-[1.05rem]" style={{ filter: 'drop-shadow(0 0 6px rgba(0,0,0,0.8))' }}>
          {bot.sigil}
        </span>
      </div>
      {mood === 'sad' && <span className="pointer-events-none absolute -top-1 right-0 text-sm">💧</span>}
      {mood === 'excited' && <span className="pointer-events-none absolute -top-1 right-0 text-sm">✨</span>}
      {mood === 'happy' && <span className="pointer-events-none absolute -top-1 right-0 text-sm">🎉</span>}
    </div>
  )
}
