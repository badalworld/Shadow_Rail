import React, { useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../state/store'
import { fmtMoney } from './Glass'

/**
 * Win  → every bot celebrates: confetti burst + banner.
 * Loss → the swarm goes quiet: rain drops + a subdued banner.
 */
export const CelebrationOverlay: React.FC = () => {
  const { celebration, bots } = useStore()

  const confetti = useMemo(() => Array.from({ length: 46 }).map((_, i) => ({
    id: i,
    dx: `${(Math.random() - 0.5) * 720}px`,
    dy: `${-Math.random() * 460 - 60}px`,
    delay: `${Math.random() * 0.35}s`,
    color: ['#00e5a8', '#22d3ee', '#a78bfa', '#fbbf24', '#ffffff'][i % 5],
    size: 5 + Math.random() * 7,
  })), [])

  const drops = useMemo(() => Array.from({ length: 26 }).map((_, i) => ({
    id: i,
    left: `${Math.random() * 100}%`,
    delay: `${Math.random() * 0.9}s`,
    size: 6 + Math.random() * 8,
  })), [])

  const celebrating = bots.filter((b) => b.mood === 'happy' || b.mood === 'excited').length

  return (
    <AnimatePresence>
      {celebration && (
        <motion.div
          key={celebration.id}
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          className="pointer-events-none fixed inset-0 z-40 overflow-hidden">
          {celebration.win ? (
            <>
              <div className="absolute inset-0"
                style={{ background: 'radial-gradient(circle at 50% 40%, rgba(0,229,168,0.16), transparent 60%)' }} />
              {confetti.map((c) => (
                <span key={c.id} className="particle absolute left-1/2 top-1/2"
                  style={{
                    width: c.size, height: c.size * 1.6, background: c.color, borderRadius: 2,
                    animationDelay: c.delay, ['--dx' as any]: c.dx, ['--dy' as any]: c.dy,
                    boxShadow: `0 0 10px ${c.color}`,
                  }} />
              ))}
            </>
          ) : (
            <>
              <div className="absolute inset-0"
                style={{ background: 'linear-gradient(180deg, rgba(56,189,248,0.10), transparent 55%)' }} />
              {drops.map((d) => (
                <span key={d.id} className="drop absolute top-0"
                  style={{ left: d.left, fontSize: d.size, animationDelay: d.delay }}>💧</span>
              ))}
            </>
          )}

          <motion.div
            initial={{ y: -60, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -40, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 22 }}
            className="glass absolute left-1/2 top-6 -translate-x-1/2 px-5 py-3 text-center"
            style={{
              borderColor: celebration.win
                ? 'color-mix(in oklab, var(--color-bull) 60%, transparent)'
                : 'color-mix(in oklab, #38bdf8 55%, transparent)',
            }}>
            <div className="text-[0.95rem] font-bold tracking-wide"
              style={{ color: celebration.win ? 'var(--color-bull)' : '#7dd3fc' }}>
              {celebration.win ? '🎉 TRADE WON — THE SWARM CELEBRATES' : '💧 TRADE LOST — THE SWARM MOURNS'}
            </div>
            <div className="mono mt-0.5 text-[0.8rem]">
              {celebration.symbol} · {celebration.reason?.replace(/_/g, ' ')} ·{' '}
              <span style={{ color: celebration.net >= 0 ? 'var(--color-bull)' : 'var(--color-bear)' }}>
                {fmtMoney(celebration.net)}
              </span>
            </div>
            {celebration.win && celebrating > 0 && (
              <div className="mt-1 text-[0.65rem] dim">{celebrating} bots celebrating · promotions queued</div>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
