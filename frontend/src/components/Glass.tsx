import React from 'react'

export const Glass: React.FC<{
  className?: string; children: React.ReactNode; style?: React.CSSProperties
  onClick?: () => void
}> = ({ className = '', children, style, onClick }) => (
  <div className={`glass ${className}`} style={style} onClick={onClick}>{children}</div>
)

export const Panel: React.FC<{
  title?: React.ReactNode
  right?: React.ReactNode
  className?: string
  bodyClass?: string
  children: React.ReactNode
  accent?: string
}> = ({ title, right, className = '', bodyClass = '', children, accent }) => (
  <Glass className={`flex min-h-0 flex-col ${className}`}>
    {title && (
      <div className="flex items-center justify-between gap-3 border-b px-4 py-2.5"
        style={{ borderColor: 'var(--sr-border)' }}>
        <div className="flex items-center gap-2 text-[0.72rem] font-semibold uppercase tracking-[0.16em]"
          style={{ color: accent || 'var(--sr-accent)' }}>
          <span className="inline-block h-1.5 w-1.5 rounded-full pulse-dot"
            style={{ background: accent || 'var(--sr-accent)' }} />
          {title}
        </div>
        {right}
      </div>
    )}
    <div className={`min-h-0 flex-1 ${bodyClass}`}>{children}</div>
  </Glass>
)

export const Stat: React.FC<{
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  tone?: 'default' | 'good' | 'bad' | 'warn' | 'accent'
  icon?: React.ReactNode
  locked?: boolean
  className?: string
}> = ({ label, value, sub, tone = 'default', icon, locked, className = '' }) => {
  const color = tone === 'good' ? 'var(--color-bull)'
    : tone === 'bad' ? 'var(--color-bear)'
      : tone === 'warn' ? 'var(--color-amber)'
        : tone === 'accent' ? 'var(--sr-accent)' : 'var(--sr-text)'
  return (
    <Glass className={`p-3.5 ${className}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-[0.64rem] font-semibold uppercase tracking-[0.14em] dim">{label}</div>
        <div className="flex items-center gap-1.5">
          {locked && <span title="Locked at first connect — never changes" className="dim text-[0.7rem]">🔒</span>}
          {icon}
        </div>
      </div>
      <div className="mono mt-1.5 text-[1.35rem] leading-tight tabular" style={{ color }}>
        {value}
      </div>
      {sub && <div className="mt-1 text-[0.68rem] dim">{sub}</div>}
    </Glass>
  )
}

export const Chip: React.FC<{
  children: React.ReactNode; color?: string; className?: string; title?: string
}> = ({ children, color, className = '', title }) => (
  <span className={`chip ${className}`} title={title}
    style={color ? { color, borderColor: `color-mix(in oklab, ${color} 45%, transparent)` } : undefined}>
    {children}
  </span>
)

export const Bar: React.FC<{
  value: number; color?: string; height?: number; className?: string; glow?: boolean
}> = ({ value, color = 'var(--sr-accent)', height = 6, className = '', glow }) => (
  <div className={`w-full overflow-hidden rounded-full ${className}`}
    style={{ height, background: 'rgba(255,255,255,0.07)' }}>
    <div className="h-full rounded-full transition-[width] duration-500"
      style={{
        width: `${Math.max(0, Math.min(100, value))}%`,
        background: color,
        boxShadow: glow ? `0 0 12px ${color}` : undefined,
      }} />
  </div>
)

export const Ring: React.FC<{
  value: number; size?: number; stroke?: number; color?: string; label?: React.ReactNode
}> = ({ value, size = 68, stroke = 7, color = 'var(--sr-accent)', label }) => {
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, value))
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.09)" strokeWidth={stroke} />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
          strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c - (pct / 100) * c}
          style={{ transition: 'stroke-dashoffset 600ms ease', filter: `drop-shadow(0 0 6px ${color})` }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        {label ?? <span className="mono text-[0.8rem]">{pct.toFixed(0)}%</span>}
      </div>
    </div>
  )
}

export const statusColor = (status?: string): string => {
  switch (status) {
    case 'working': return 'var(--color-cyan)'
    case 'success': return 'var(--color-bull)'
    case 'error': return 'var(--color-bear)'
    case 'blocked': return 'var(--color-amber)'
    case 'celebrating': return 'var(--color-bull)'
    case 'sad': return '#7dd3fc'
    case 'halted': return 'var(--color-bear)'
    case 'idle': return '#8aa0b6'
    default: return '#5b6b80'
  }
}

export const moodEmoji = (mood?: string) => {
  switch (mood) {
    case 'happy': return '😄'
    case 'excited': return '🤩'
    case 'sad': return '😔'
    case 'worried': return '😰'
    default: return ''
  }
}

export const fmtMoney = (v: number | undefined | null, digits = 2): string => {
  const n = Number(v ?? 0)
  const sign = n < 0 ? '-' : ''
  return `${sign}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

export const fmtNum = (v: number | undefined | null, digits = 4): string => {
  const n = Number(v ?? 0)
  if (Math.abs(n) >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (Math.abs(n) >= 1) return n.toFixed(Math.min(digits, 3))
  return n.toPrecision(4)
}

export const fmtPct = (v: number | undefined | null, digits = 2): string =>
  `${Number(v ?? 0) >= 0 ? '+' : ''}${Number(v ?? 0).toFixed(digits)}%`

export const timeAgo = (ts?: number): string => {
  if (!ts) return '—'
  const s = Math.max(0, (Date.now() - ts) / 1000)
  if (s < 60) return `${s.toFixed(0)}s ago`
  if (s < 3600) return `${(s / 60).toFixed(0)}m ago`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`
  return `${(s / 86400).toFixed(1)}d ago`
}

export const clock = (ts?: number): string => {
  if (!ts) return '—'
  const d = new Date(ts)
  return d.toLocaleTimeString('en-GB', { hour12: false })
}

export const dateTime = (ts?: number): string => {
  if (!ts) return '—'
  const d = new Date(ts)
  return `${d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })} ${d.toLocaleTimeString('en-GB', { hour12: false })}`
}
