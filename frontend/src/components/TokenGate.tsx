import React, { useEffect, useState } from 'react'
import { KeyRound, ShieldCheck } from 'lucide-react'
import { AUTH_EVENT, endpoints } from '../lib/api'
import { setToken } from '../lib/token'

/**
 * Access gate for deployments started with `SHADOW_RAIL_API_TOKEN`.
 *
 * The dashboard is a trading console: if the server hands out 401s (no token,
 * wrong token, token rotated) the UI must stop dead and ask for the token
 * rather than display a room full of empty panels.  When the server runs
 * without a token — the default local/simulator mode — this component is a
 * no-op: the probe succeeds and the app renders immediately.
 */
export const TokenGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [locked, setLocked] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    endpoints.status().catch(() => { if (alive) setLocked(true) })
    const onAuth = () => setLocked(true)
    window.addEventListener(AUTH_EVENT, onAuth)
    return () => { alive = false; window.removeEventListener(AUTH_EVENT, onAuth) }
  }, [])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!value.trim()) return
    setBusy(true)
    setError('')
    setToken(value)
    try {
      await endpoints.status()
      window.location.reload()
    } catch {
      setToken('')
      setError('That token was refused by the server.')
      setBusy(false)
    }
  }

  if (!locked) return <>{children}</>

  return (
    <div className="relative z-10 flex h-screen w-screen items-center justify-center p-6">
      <form onSubmit={submit} className="glass w-full max-w-md p-6">
        <div className="flex items-center gap-3">
          <img src="/logo.svg" alt="" width={44} height={44} className="rounded-xl" />
          <div>
            <div className="text-[1.05rem] font-bold accent-text">Shadow Rail — locked</div>
            <div className="text-[0.72rem] dim">This engine requires an access token</div>
          </div>
        </div>
        <label className="mt-5 block text-[0.62rem] uppercase tracking-wider dim">
          API token
        </label>
        <input
          autoFocus
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="SHADOW_RAIL_API_TOKEN"
          className="mono mt-1 w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-[0.8rem] outline-none focus:border-white/25"
        />
        {error && <div className="mt-2 text-[0.7rem] text-red-300">{error}</div>}
        <button
          type="submit"
          disabled={busy}
          className="chip mt-4 w-full justify-center py-2 hover:opacity-80 disabled:opacity-50"
        >
          <KeyRound size={14} /> {busy ? 'Checking…' : 'Unlock dashboard'}
        </button>
        <div className="mt-3 flex items-start gap-2 text-[0.66rem] dim">
          <ShieldCheck size={13} className="mt-0.5 shrink-0" />
          <span>
            The token is the value of <span className="mono">SHADOW_RAIL_API_TOKEN</span> on
            the machine running the engine. It stays in this browser only.
          </span>
        </div>
      </form>
    </div>
  )
}
