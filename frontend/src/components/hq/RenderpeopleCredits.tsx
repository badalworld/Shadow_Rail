import React, { useState } from 'react'
import { Info, X } from 'lucide-react'

/**
 * Human reference credit.
 *
 * The agents in the headquarters are modelled on the Renderpeople catalogue
 * taxonomy (renderpeople.com/3d-people): office people filtered by activity —
 * sitting, typing, standing, walking, pointing, talking — in business and
 * smart-casual clothing, across body types, ages and ethnicities.
 *
 * Those scans are licensed, paid assets, so none of their geometry ships with
 * this build: the cast is generated procedurally with the same vocabulary.
 * A licensed studio can drop real scans in and the room uses them as-is —
 * see `SCAN_SLOT` in ./layout.ts and the README section "Renderpeople
 * integration".
 */
export const RenderpeopleCredits: React.FC<{ className?: string }> = ({ className = '' }) => {
  const [open, setOpen] = useState(false)
  return (
    <div className={`relative ${className}`}>
      <button className="chip flex items-center gap-1" style={{ cursor: 'pointer' }}
        onClick={() => setOpen((o) => !o)} title="About the people in this room">
        <Info size={11} /> human reference
      </button>
      {open && (
        <div className="glass-solid absolute bottom-6 right-0 z-20 w-[20rem] p-3 text-[0.66rem] leading-relaxed">
          <div className="flex items-center justify-between">
            <span className="text-[0.58rem] uppercase tracking-[0.18em] dim">credits</span>
            <button className="chip" style={{ cursor: 'pointer' }} onClick={() => setOpen(false)}>
              <X size={11} />
            </button>
          </div>
          <p className="pt-1">
            The 29 agents are procedural bodies modelled on the{' '}
            <a className="underline" href="https://renderpeople.com/3d-people"
              target="_blank" rel="noreferrer">Renderpeople</a> office-people
            catalogue: sitting, typing, standing and walking poses, business and
            smart-casual clothing, and the full range of builds, ages and ethnicities.
          </p>
          <p className="pt-1 dim">
            Renderpeople scans are licensed assets, so no geometry of theirs is
            redistributed here. Drop licensed GLBs into{' '}
            <span className="mono">public/models/people/</span> and set{' '}
            <span className="mono">VITE_HQ_SCANS=on</span> to render them in place of the
            procedural cast.
          </p>
        </div>
      )}
    </div>
  )
}

export default RenderpeopleCredits
