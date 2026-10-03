import React from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, Info, Skull, X, XCircle } from 'lucide-react'
import { useStore } from '../state/store'

const ICONS: Record<string, React.ReactNode> = {
  info: <Info size={15} />,
  success: <CheckCircle2 size={15} />,
  error: <XCircle size={15} />,
  warn: <AlertTriangle size={15} />,
  sos: <Skull size={15} />,
}

const COLORS: Record<string, string> = {
  info: 'var(--color-cyan)',
  success: 'var(--color-bull)',
  error: 'var(--color-bear)',
  warn: 'var(--color-amber)',
  sos: 'var(--color-bear)',
}

export const Toasts: React.FC = () => {
  const { toasts, dismissToast } = useStore()
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[21rem] flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, x: 60, scale: 0.96 }}
            animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 60, scale: 0.96 }}
            transition={{ type: 'spring', stiffness: 320, damping: 28 }}
            className={`glass pointer-events-auto flex items-start gap-2.5 p-3 ${t.kind === 'sos' ? 'sos-flash' : ''}`}
            style={{ borderColor: `color-mix(in oklab, ${COLORS[t.kind]} 55%, transparent)` }}>
            <span style={{ color: COLORS[t.kind] }} className="mt-[2px]">{ICONS[t.kind]}</span>
            <div className="min-w-0 flex-1">
              <div className="text-[0.78rem] font-semibold" style={{ color: COLORS[t.kind] }}>{t.title}</div>
              {t.body && <div className="mt-0.5 break-words text-[0.68rem] dim">{t.body}</div>}
            </div>
            <button onClick={() => dismissToast(t.id)} className="dim hover:opacity-70">
              <X size={13} />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
