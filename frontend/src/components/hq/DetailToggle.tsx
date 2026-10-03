import React, { useEffect, useState } from 'react'
import { Gauge, Monitor, Sparkles } from 'lucide-react'
import type { Detail } from './layout'

/**
 * Rendering quality for the 3D headquarters.
 *
 *   cinema      — reflections, shadows, every agent fully modelled
 *   balanced    — the default: live screens, contact shadows, mid models
 *   performance — no reflections/shadows, tiny draw calls, fastest preview
 *
 * The choice is remembered per browser so a slower machine never has to ask twice.
 */
const KEY = 'shadow-rail.hq.detail'

export const readDetail = (): Detail => {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'cinematic' || v === 'balanced' || v === 'performance') return v
  } catch { /* private mode */ }
  return 'balanced'
}

export const DETAILS: { key: Detail; label: string; icon: React.ReactNode; hint: string }[] = [
  { key: 'cinematic', label: 'cinema', icon: <Sparkles size={11} />, hint: 'reflections, shadows, full-detail agents' },
  { key: 'balanced', label: 'balanced', icon: <Monitor size={11} />, hint: 'live screens and shadows, mid-detail agents' },
  { key: 'performance', label: 'speed', icon: <Gauge size={11} />, hint: 'no reflections, minimal geometry — best on laptops' },
]

export const DetailToggle: React.FC<{
  value: Detail
  onChange: (d: Detail) => void
  className?: string
}> = ({ value, onChange, className = '' }) => {
  useEffect(() => {
    try { localStorage.setItem(KEY, value) } catch { /* private mode */ }
  }, [value])

  return (
    <div className={`glass-row flex items-center gap-0.5 p-0.5 ${className}`}>
      {DETAILS.map((d) => {
        const on = d.key === value
        return (
          <button
            key={d.key}
            title={d.hint}
            onClick={() => onChange(d.key)}
            className="flex items-center gap-1 rounded-md px-2 py-[3px] text-[0.58rem] uppercase tracking-[0.14em] transition-colors"
            style={{
              cursor: 'pointer',
              color: on ? 'var(--color-cyan)' : 'var(--sr-dim)',
              background: on ? 'rgba(34,211,238,0.12)' : 'transparent',
              border: on ? '1px solid rgba(34,211,238,0.35)' : '1px solid transparent',
            }}
          >
            {d.icon} {d.label}
          </button>
        )
      })}
    </div>
  )
}

export default DetailToggle
