import React, { useMemo } from 'react'

/** Dependency-free SVG charts — tiny, fast, and they inherit the SOS palette. */

export const LineChart: React.FC<{
  data: { x: number; y: number }[]
  height?: number
  color?: string
  fill?: boolean
  baseline?: number
  className?: string
}> = ({ data, height = 160, color = 'var(--sr-accent)', fill = true, baseline, className = '' }) => {
  const w = 800
  const h = height
  const { path, area, min, max } = useMemo(() => {
    if (!data.length) return { path: '', area: '', min: 0, max: 0 }
    const ys = data.map((d) => d.y).concat(baseline !== undefined ? [baseline] : [])
    const min = Math.min(...ys)
    const max = Math.max(...ys)
    const span = max - min || 1
    const px = (i: number) => (i / Math.max(1, data.length - 1)) * w
    const py = (v: number) => h - ((v - min) / span) * (h - 10) - 5
    const pts = data.map((d, i) => `${px(i).toFixed(2)},${py(d.y).toFixed(2)}`)
    const line = `M ${pts.join(' L ')}`
    const areaPath = `${line} L ${w},${h} L 0,${h} Z`
    return { path: line, area: areaPath, min, max }
  }, [data, h, baseline])

  if (!data.length) return <div className="flex h-full items-center justify-center text-xs dim">no data yet</div>
  const zero = baseline !== undefined
    ? h - ((baseline - min) / ((max - min) || 1)) * (h - 10) - 5
    : null

  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className={`h-full w-full ${className}`}>
      <defs>
        <linearGradient id="sr-line-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1="0" x2={w} y1={h * f} y2={h * f} stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
      ))}
      {zero !== null && (
        <line x1="0" x2={w} y1={zero} y2={zero} stroke="rgba(255,255,255,0.22)"
          strokeWidth="1" strokeDasharray="4 4" />
      )}
      {fill && <path d={area} fill="url(#sr-line-fill)" />}
      <path d={path} fill="none" stroke={color} strokeWidth="2"
        style={{ filter: `drop-shadow(0 0 5px ${color})` }} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

export const Bars: React.FC<{
  data: { label: string; value: number }[]
  height?: number
  className?: string
}> = ({ data, height = 130, className = '' }) => {
  if (!data.length) return <div className="flex h-full items-center justify-center text-xs dim">no closed trades yet</div>
  const max = Math.max(...data.map((d) => Math.abs(d.value)), 1)
  return (
    <div className={`flex items-end gap-1.5 ${className}`} style={{ height }}>
      {data.map((d, i) => {
        const pct = (Math.abs(d.value) / max) * 100
        const up = d.value >= 0
        return (
          <div key={i} className="group relative flex-1" title={`${d.label}: ${d.value.toFixed(2)}`}>
            <div className="flex h-full flex-col justify-end">
              <div className="rounded-t-sm transition-all"
                style={{
                  height: `${Math.max(2, pct)}%`,
                  background: up ? 'var(--color-bull)' : 'var(--color-bear)',
                  boxShadow: `0 0 10px ${up ? 'var(--color-bull)' : 'var(--color-bear)'}66`,
                }} />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export const Donut: React.FC<{
  slices: { value: number; color: string; label: string }[]
  size?: number
  center?: React.ReactNode
}> = ({ slices, size = 140, center }) => {
  const total = slices.reduce((a, s) => a + s.value, 0)
  const r = size / 2 - 10
  const c = 2 * Math.PI * r
  let offset = 0
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="12" />
        {total > 0 && slices.map((s, i) => {
          const len = (s.value / total) * c
          const el = (
            <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.color}
              strokeWidth="12" strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset}
              style={{ filter: `drop-shadow(0 0 5px ${s.color}88)`, transition: 'stroke-dasharray 600ms' }} />
          )
          offset += len
          return el
        })}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{center}</div>
    </div>
  )
}
