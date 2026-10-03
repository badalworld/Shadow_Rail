import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Pause, Play, Search, Trash2 } from 'lucide-react'
import { endpoints } from '../lib/api'
import { useStore } from '../state/store'
import { Chip, Panel, clock } from '../components/Glass'

const LEVELS = ['all', 'sos', 'error', 'warn', 'success', 'info', 'debug']

const levelColor = (l: string) => l === 'error' || l === 'sos' ? 'var(--color-bear)'
  : l === 'warn' ? 'var(--color-amber)'
    : l === 'success' ? 'var(--color-bull)'
      : l === 'info' ? 'var(--sr-accent)' : 'var(--sr-dim)'

export const Logs: React.FC = () => {
  const { logs, bots, pushToast } = useStore()
  const [live, setLive] = useState(true)
  const [level, setLevel] = useState('all')
  const [botId, setBotId] = useState('')
  const [search, setSearch] = useState('')
  const [history, setHistory] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  const loadHistory = useCallback(async (offset = 0) => {
    setLoading(true)
    try {
      const res = await endpoints.logs({
        limit: 300, offset, level: level === 'all' ? '' : level,
        bot_id: botId, search,
      })
      setHistory((prev) => (offset ? [...prev, ...res.logs] : res.logs))
    } catch (e: any) {
      pushToast({ kind: 'error', title: 'Log fetch failed', body: String(e?.message || e) })
    } finally { setLoading(false) }
  }, [level, botId, search, pushToast])

  useEffect(() => { loadHistory(0) }, [loadHistory])

  const rows = useMemo(() => {
    const base = live ? logs : history
    return base.filter((l: any) => {
      if (level !== 'all' && l.level !== level) return false
      if (botId && l.bot_id !== botId) return false
      if (search && !String(l.message).toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [live, logs, history, level, botId, search])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    rows.forEach((r: any) => { c[r.level] = (c[r.level] || 0) + 1 })
    return c
  }, [rows])

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setLive((v) => !v)} className="chip hover:opacity-80"
          style={{ cursor: 'pointer', color: live ? 'var(--color-bull)' : 'var(--color-amber)' }}>
          {live ? <><Play size={11} /> live stream</> : <><Pause size={11} /> frozen</>}
        </button>
        <div className="flex items-center gap-1">
          {LEVELS.map((l) => (
            <button key={l} onClick={() => setLevel(l)} className="chip hover:opacity-80"
              style={{ cursor: 'pointer', borderColor: level === l ? 'var(--sr-accent)' : undefined }}>
              <span style={{ color: l === 'all' ? undefined : levelColor(l) }}>{l}</span>
            </button>
          ))}
        </div>
        <select value={botId} onChange={(e) => setBotId(e.target.value)}
          className="rounded-full border bg-transparent px-2.5 py-1 text-[0.68rem] outline-none"
          style={{ borderColor: 'var(--sr-border)', background: 'var(--sr-bg)' }}>
          <option value="">all bots</option>
          {bots.map((b) => <option key={b.bot_id} value={b.bot_id}>{b.name} ({b.bot_id})</option>)}
        </select>
        <div className="relative">
          <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 dim" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="search message"
            className="w-44 rounded-full border bg-transparent py-1 pl-6 pr-2 text-[0.68rem] outline-none"
            style={{ borderColor: 'var(--sr-border)' }} />
        </div>
        <Chip>{rows.length} lines</Chip>
        <Chip color="var(--color-bear)">{counts.error || 0} errors</Chip>
        <Chip color="var(--color-amber)">{counts.warn || 0} warnings</Chip>
        <div className="ml-auto flex gap-2">
          <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
            onClick={() => loadHistory(history.length)} disabled={loading}>
            {loading ? 'loading…' : 'load older'}
          </button>
          <button className="chip hover:opacity-80" style={{ cursor: 'pointer' }}
            onClick={() => { setHistory([]); pushToast({ kind: 'info', title: 'View cleared (server log kept)' }) }}>
            <Trash2 size={11} /> clear view
          </button>
        </div>
      </div>

      <Panel title="Full workflow log — every bot, every action" bodyClass="scroll-thin overflow-auto p-0"
        right={<Chip color={live ? 'var(--color-bull)' : 'var(--color-amber)'}>
          {live ? 'streaming' : 'frozen'}</Chip>}>
        <div ref={boxRef} className="flex flex-col">
          {rows.map((l: any, i: number) => (
            <div key={l.id ?? `${l.ts}-${i}`}
              className="glass-row grid grid-cols-[5.2rem_6.5rem_6.5rem_1fr] items-start gap-2 border-b px-3 py-1.5 text-[0.68rem]"
              style={{ borderColor: 'rgba(255,255,255,0.04)' }}>
              <span className="mono dim">{clock(l.ts)}</span>
              <span className="font-semibold" style={{ color: levelColor(l.level) }}>{l.level}</span>
              <span className="mono truncate" style={{ color: 'var(--color-cyan)' }}>{l.bot_id}</span>
              <span className="min-w-0 break-words" title={JSON.stringify(l.payload || {})}>{l.message}</span>
            </div>
          ))}
          {!rows.length && <div className="p-8 text-center text-[0.75rem] dim">no log lines match the filter</div>}
        </div>
      </Panel>
    </div>
  )
}
