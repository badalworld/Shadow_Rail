import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import {
  ContactShadows, Environment, Html, Lightformer, OrbitControls, Sparkles, useGLTF,
} from '@react-three/drei'
import * as THREE from 'three'
import type { Bot } from '../../lib/types'
import {
  SCAN_SLOT, STATIONS, STATION_BY_GROUP, buildLook, seatsFor, stationOf,
} from './layout'
import type { Detail, StationKey } from './layout'
import { Human } from './Human'
import {
  BigScreen, CeilingLights, Chair, Console, Dais, Desk, Gate, HoloCurve,
  Podium, Plant, Room, Truss, Vault, VideoWall, ZoneMark,
} from './furniture'

/* ═══════════════════════════════════════════════════════════════════════════
   The AI Trading Bot Headquarters — a real trading floor.

   The room is 48 × 30 m with a 10 m ceiling.  Every agent from the roster has
   a seat, a desk and a body; the workflow runs along the floor from the
   west-side scanner bay, through the analyst wing, into the execution pods and
   the verification gate, past the monitor wall, and into the vault where the
   ledger closes the books.  The CEO stands on the dais facing the big board.

   Humans are procedural (see ./Human.tsx) but modelled on the Renderpeople
   catalogue taxonomy — see ./layout.ts for the licensed-scan drop-in slot.
   ═══════════════════════════════════════════════════════════════════════════ */

/* ─────────────────── is there really a canvas to draw on? ──────────────── */

const WEBGL_OK: boolean = (() => {
  try {
    if (typeof document === 'undefined' || typeof document.createElement !== 'function') return false
    const c = document.createElement('canvas')
    const gl = c.getContext('webgl2') || c.getContext('webgl')
    // hand the probe context straight back — the real canvas needs its own
    if (gl) (gl.getExtension('WEBGL_lose_context') as { loseContext?: () => void } | null)?.loseContext?.()
    return !!gl
  } catch {
    return false
  }
})()

/* ──────────────────────────────── props ────────────────────────────────── */

export interface HQSnapshot {
  cycle?: number
  stage?: string
  working?: number
  open?: number
  max?: number
  equity?: number
  starting?: number
  released?: number
  unrealized?: number
  fees?: number
  winRate?: number
  trades?: number
  marginUsed?: number
  marginBudget?: number
  trailArmed?: number
  drawdown?: number
  peak?: number
  riskLabel?: string
  groups?: { key: string; label: string; working: number; total: number }[]
  scanSeconds?: number
  sosReasons?: string[]
}

export interface HQProps {
  bots: Bot[]
  pulses: { id: number; from: string; to: string; stage: string }[]
  snapshot?: HQSnapshot
  detail?: Detail
  danger?: boolean
  selected?: string | null
  focus?: StationKey | null
  curve?: number[]
  className?: string
  onSelect?: (botId: string | null) => void
  onFocus?: (key: StationKey | null) => void
  onDetail?: (d: Detail) => void
  onScan?: () => void
}

/* ─────────────────────────── live wall boards ──────────────────────────── */

const MONO = '"JetBrains Mono", ui-monospace, SFMono-Regular, monospace'

const drawBoard = (
  canvas: HTMLCanvasElement, accent: string, title: string,
  rows: [string, string][], bars: number[], danger: boolean,
) => {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const W = canvas.width
  const H = canvas.height
  const hue = danger ? '#ef4444' : accent
  const g = ctx.createLinearGradient(0, 0, W, H)
  g.addColorStop(0, '#050810')
  g.addColorStop(1, '#08131c')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, W, H)
  // scanline texture
  ctx.fillStyle = 'rgba(255,255,255,0.022)'
  for (let y = 0; y < H; y += 4) ctx.fillRect(0, y, W, 1)
  // frame
  ctx.strokeStyle = hue
  ctx.globalAlpha = 0.55
  ctx.lineWidth = 3
  ctx.strokeRect(6, 6, W - 12, H - 12)
  ctx.globalAlpha = 1
  // title + rule
  ctx.fillStyle = hue
  ctx.font = `600 ${Math.round(H * 0.085)}px ${MONO}`
  ctx.fillText(title.toUpperCase(), 24, H * 0.13)
  ctx.globalAlpha = 0.35
  ctx.fillRect(24, H * 0.17, W - 48, 2)
  ctx.globalAlpha = 1
  // rows
  const top = H * 0.24
  const lh = (H * 0.72 - (bars.length ? H * 0.22 : 0)) / Math.max(1, rows.length)
  rows.forEach(([k, v], i) => {
    const y = top + lh * (i + 0.72)
    ctx.fillStyle = 'rgba(203,224,236,0.62)'
    ctx.font = `500 ${Math.round(H * 0.068)}px ${MONO}`
    ctx.fillText(k.toUpperCase(), 24, y)
    ctx.fillStyle = '#eafcff'
    ctx.font = `600 ${Math.round(H * 0.078)}px ${MONO}`
    ctx.fillText(v, W * 0.42, y)
  })
  // bars
  if (bars.length) {
    const n = bars.length
    const bw = (W - 48) / n
    const lo = Math.min(...bars)
    const hi = Math.max(...bars)
    const span = hi - lo || 1
    bars.forEach((b, i) => {
      const h = 12 + ((b - lo) / span) * H * 0.17
      ctx.fillStyle = i === n - 1 ? hue : 'rgba(34,211,238,0.55)'
      ctx.fillRect(24 + i * bw, H - 16 - h, Math.max(2, bw - 3), h)
    })
  }
}

const Board: React.FC<{
  w: number; h: number; px?: number; accent: string
  title: string; rows: [string, string][]; bars?: number[]; danger?: boolean
}> = ({ w, h, px = 1024, accent, title, rows, bars = [], danger = false }) => {
  const ratio = h / w
  const tex = useMemo(() => {
    const canvas = document.createElement('canvas')
    canvas.width = px
    canvas.height = Math.max(64, Math.round(px * ratio))
    drawBoard(canvas, accent, title, rows, bars, danger)
    const t = new THREE.CanvasTexture(canvas)
    t.anisotropy = 4
    t.needsUpdate = true
    return t
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [px, ratio, accent, title, JSON.stringify(rows), bars.join(','), danger])
  useEffect(() => () => tex.dispose(), [tex])
  return (
    <mesh>
      <planeGeometry args={[w, h]} />
      <meshBasicMaterial map={tex} toneMapped={false} />
    </mesh>
  )
}

/* ─────────────────────────────── camera rig ────────────────────────────── */

const OVERVIEW = { pos: [0, 13.8, 33.5] as [number, number, number], target: [0, 2.7, -2] as [number, number, number] }

/**
 * ACES tone mapping renders our dark palette very dark.  Lift the exposure so
 * the floor, the desks and the agents are readable at a glance.
 */
const Exposure: React.FC<{ value: number }> = ({ value }) => {
  const gl = useThree((st) => st.gl)
  useEffect(() => {
    const prev = gl.toneMappingExposure
    gl.toneMappingExposure = value
    return () => { gl.toneMappingExposure = prev }
  }, [gl, value])
  return null
}

const CameraRig: React.FC<{ focus: StationKey | null }> = ({ focus }) => {
  const camera = useThree((s) => s.camera)
  const controls = useThree((s) => s.controls) as any
  const goal = useRef({
    pos: new THREE.Vector3(...OVERVIEW.pos),
    target: new THREE.Vector3(...OVERVIEW.target),
  })
  const flying = useRef(true)

  useEffect(() => {
    const preset = focus ? stationOf(focus).cam : OVERVIEW
    goal.current.pos.set(preset.pos[0], preset.pos[1], preset.pos[2])
    goal.current.target.set(preset.target[0], preset.target[1], preset.target[2])
    flying.current = true
    if (controls) controls.enabled = false
  }, [focus, controls])

  useFrame((_, dt) => {
    if (!controls || !flying.current) return
    const k = 1 - Math.pow(0.0015, Math.min(dt, 0.05))
    camera.position.lerp(goal.current.pos, k)
    controls.target.lerp(goal.current.target, k)
    controls.update()
    if (camera.position.distanceTo(goal.current.pos) < 0.12) {
      flying.current = false
      controls.enabled = true
    }
  })
  return null
}

/* ───────────────────────────── pulse rails ─────────────────────────────── */

const arc = (a: THREE.Vector3, b: THREE.Vector3, lift = 0.2) => {
  const mid = a.clone().add(b).multiplyScalar(0.5)
  mid.y += 0.4 + a.distanceTo(b) * lift
  return new THREE.CatmullRomCurve3([a.clone(), mid, b.clone()])
}

const Rail: React.FC<{ a: THREE.Vector3; b: THREE.Vector3; color: string; live: boolean }> =
  ({ a, b, color, live }) => {
    const curve = useMemo(() => arc(a, b), [a, b])
    const geo = useMemo(() => new THREE.TubeGeometry(curve, 64, 0.035, 6, false), [curve])
    useEffect(() => () => geo.dispose(), [geo])
    const mat = useRef<THREE.MeshBasicMaterial>(null!)
    useFrame(({ clock }) => {
      if (!mat.current) return
      mat.current.opacity = live
        ? 0.5 + Math.abs(Math.sin(clock.getElapsedTime() * 3)) * 0.4
        : 0.1
    })
    return (
      <mesh geometry={geo}>
        <meshBasicMaterial ref={mat} color={color} transparent opacity={0.12} />
      </mesh>
    )
  }

const PulseDot: React.FC<{ a: THREE.Vector3; b: THREE.Vector3; color: string }> =
  ({ a, b, color }) => {
    const curve = useMemo(() => arc(a, b, 0.24), [a, b])
    const head = useRef<THREE.Mesh>(null!)
    const tail = useRef<THREE.Mesh>(null!)
    const tail2 = useRef<THREE.Mesh>(null!)
    const t = useRef(0)
    useFrame((_, dt) => {
      t.current = Math.min(1, t.current + dt / 1.5)
      const p = t.current
      const at = (x: number) => curve.getPointAt(Math.max(0, Math.min(1, x)))
      if (head.current) head.current.position.copy(at(p))
      if (tail.current) tail.current.position.copy(at(p - 0.045))
      if (tail2.current) tail2.current.position.copy(at(p - 0.09))
    })
    return (
      <group>
        <mesh ref={head}>
          <sphereGeometry args={[0.17, 12, 12]} />
          <meshBasicMaterial color={color} toneMapped={false} />
        </mesh>
        <mesh ref={tail}>
          <sphereGeometry args={[0.11, 10, 10]} />
          <meshBasicMaterial color={color} transparent opacity={0.55} toneMapped={false} />
        </mesh>
        <mesh ref={tail2}>
          <sphereGeometry args={[0.07, 8, 8]} />
          <meshBasicMaterial color={color} transparent opacity={0.28} toneMapped={false} />
        </mesh>
      </group>
    )
  }

/* ───────────────────── licensed Renderpeople scans ─────────────────────── */

/** `VITE_HQ_SCANS=on` renders public/models/people/<bot_id>.glb in place of the
 *  procedural body.  The files are licensed and never downloaded — see
 *  public/models/people/README.md.  Any missing or broken scan silently falls
 *  back to the procedural cast, so the room always renders. */
const SCANS_ON = String((import.meta.env && import.meta.env.VITE_HQ_SCANS) || '').toLowerCase() === 'on'

class ScanBoundary extends React.Component<
  { fallback: React.ReactNode; children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch() { /* a broken scan must never take the floor down */ }
  render() { return this.state.failed ? this.props.fallback : this.props.children }
}

const ScanAgent: React.FC<{
  url: string
  bot: Bot
  scale: number
  label: boolean
  selected: boolean
  onSelect?: (id: string) => void
  onHover?: (id: string | null) => void
}> = ({ url, bot, scale, label, selected, onSelect, onHover }) => {
  const { scene } = useGLTF(url)
  const obj = useMemo(() => {
    const clone = scene.clone(true)
    clone.traverse((o: any) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true } })
    return clone
  }, [scene])
  const accent = bot.color || '#22d3ee'
  return (
    <group scale={scale}
      onPointerOver={(e) => { e.stopPropagation(); onHover?.(bot.bot_id) }}
      onPointerOut={() => onHover?.(null)}
      onPointerDown={(e) => { e.stopPropagation(); onSelect?.(bot.bot_id) }}>
      <primitive object={obj} />
      {/* invisible capsule keeps the agent clickable even without a body mesh */}
      <mesh position={[0, 0.9, 0]} visible={false}>
        <capsuleGeometry args={[0.32, 1.05, 4, 8]} />
      </mesh>
      {label && (
        <Html position={[0, 2.08, 0]} center distanceFactor={30} zIndexRange={[6, 0]}>
          <div className="pointer-events-none select-none whitespace-nowrap rounded px-1.5 py-[1px]"
            style={{
              background: 'rgba(4,8,14,0.68)', border: `1px solid ${accent}66`,
              fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.1em',
              color: selected ? accent : 'rgba(233,244,255,0.86)',
              boxShadow: selected ? `0 0 12px ${accent}66` : 'none',
            }}>
            {bot.name}
          </div>
        </Html>
      )}
    </group>
  )
}

/* ──────────────────────────── the 3D scene ─────────────────────────────── */

const STAGE_STATION: Record<string, StationKey> = {
  connector: 'command', scan: 'scan', analyze: 'analyze', execute: 'execute',
  verify: 'verify', monitor: 'monitor', close: 'finance', idle: 'command',
}

const Scene: React.FC<HQProps & {
  hover: string | null
  setHover: (id: string | null) => void
  focus: StationKey | null
  handoffs: Record<string, number>
}> = ({
  bots, pulses, snapshot, detail = 'balanced', danger = false, selected = null,
  onSelect, onFocus, onScan, curve = [], focus, hover, setHover, handoffs,
}) => {
  const rich = detail === 'cinematic'
  const mid = detail !== 'performance'
  const accentOf = (c: string) => (danger ? '#ef4444' : c)
  const seats = useMemo(() => seatsFor(bots), [bots])
  const looks = useMemo(() => {
    const m: Record<string, ReturnType<typeof buildLook>> = {}
    for (const b of bots) m[b.bot_id] = buildLook(b)
    return m
  }, [bots])
  const byId = useMemo(() => {
    const m: Record<string, Bot> = {}
    for (const b of bots) m[b.bot_id] = b
    return m
  }, [bots])

  /* station centres = mean seat position (the patroller does not count) */
  const centres = useMemo(() => {
    const acc: Record<string, { x: number; z: number; n: number }> = {}
    for (const s of seats) {
      if (!s.station) continue
      const c = (acc[s.station] ||= { x: 0, z: 0, n: 0 })
      c.x += s.position[0]; c.z += s.position[2]; c.n += 1
    }
    const out: Partial<Record<StationKey, THREE.Vector3>> = {}
    for (const k of Object.keys(acc) as StationKey[]) {
      const c = acc[k]
      out[k] = new THREE.Vector3(c.x / c.n, 1.2, c.z / c.n)
    }
    return out
  }, [seats])

  const botStation = useMemo(() => {
    const m: Record<string, StationKey> = {}
    for (const b of bots) {
      const k = STATION_BY_GROUP[b.group]
      if (k) m[b.bot_id] = k
    }
    return m
  }, [bots])

  const groupCounts = useMemo(() => {
    const g: Record<string, { working: number; total: number }> = {}
    for (const b of bots) {
      const e = (g[b.group] ||= { working: 0, total: 0 })
      e.total += 1
      if (b.status === 'working' || b.status === 'success') e.working += 1
    }
    return g
  }, [bots])

  const activeStage = snapshot?.stage || ''
  const activeStation = STAGE_STATION[activeStage] || null
  const cycleAccent = accentOf(STATIONS.find((s) => s.key === activeStation)?.accent || '#22d3ee')

  /* the seven wall boards: overview / pipeline / risk / equity, cycled by click */
  const views = useMemo(() => {
    const s = snapshot || {}
    const money = (v?: number) => (v === undefined ? '—' : v.toLocaleString(undefined, { maximumFractionDigits: 2 }))
    const groups = (s.groups || []).map((g) => [`${g.label}`, `${g.working}/${g.total}`] as [string, string])
    return [
      {
        key: 'overview',
        title: `shadow rail · cycle ${s.cycle ?? 0}`,
        rows: [
          ['stage', (activeStage || 'idle').toUpperCase()],
          ['agents working', `${s.working ?? 0} / ${bots.length}`],
          ['open positions', `${s.open ?? 0} / ${s.max ?? 10}`],
          ['equity', `$${money(s.equity)}`],
          ['realised p&l', `$${money(s.released)}`],
          ['fees paid', `$${money(s.fees)}`],
          ['bar closes in', `${Math.max(0, Math.round(s.scanSeconds || 0))}s`],
        ] as [string, string][],
      },
      {
        key: 'pipeline',
        title: 'workflow rail',
        rows: groups.length ? groups : STATIONS.map((st) => [st.label, '—'] as [string, string]),
      },
      {
        key: 'risk',
        title: 'risk governor',
        rows: [
          ['mode', (s.riskLabel || 'indicator default').toUpperCase()],
          ['margin used', `$${money(s.marginUsed)}`],
          ['budget / trade', `$${money(s.marginBudget)}`],
          ['free to deploy', `${s.open !== undefined && s.max !== undefined ? Math.max(0, s.max - s.open) : '—'} slots`],
          ['roi trail armed', `${s.trailArmed ?? 0}`],
          ['drawdown', `${(s.drawdown ?? 0).toFixed(2)}%`],
          ['peak equity', `$${money(s.peak)}`],
        ] as [string, string][],
      },
      {
        key: 'equity',
        title: 'equity manager',
        rows: [
          ['starting balance', `$${money(s.starting)}`],
          ['equity now', `$${money(s.equity)}`],
          ['unrealised', `$${money(s.unrealized)}`],
          ['realised p&l', `$${money(s.released)}`],
          ['fees paid', `$${money(s.fees)}`],
          ['win rate', `${(s.winRate ?? 0).toFixed(1)}%`],
          ['closed trades', `${s.trades ?? 0}`],
        ] as [string, string][],
      },
    ]
  }, [snapshot, activeStage, bots.length])
  const [view, setView] = useState(0)
  const board = views[danger ? 0 : view % views.length]

  /* rails between consecutive stations + any pulse that is in flight */
  const pipeline: StationKey[] = ['command', 'scan', 'analyze', 'execute', 'verify', 'monitor', 'finance']
  const railPairs = useMemo(() => {
    const pairs: { a: THREE.Vector3; b: THREE.Vector3; color: string; key: string }[] = []
    for (let i = 0; i < pipeline.length; i++) {
      const a = centres[pipeline[i]]
      const b = centres[pipeline[(i + 1) % pipeline.length]]
      if (!a || !b) continue
      pairs.push({ a, b, color: stationOf(pipeline[(i + 1) % pipeline.length]).accent, key: `${pipeline[i]}-${pipeline[(i + 1) % pipeline.length]}` })
    }
    return pairs
  }, [centres])

  const livePulses = useMemo(() => {
    const out: { id: number; a: THREE.Vector3; b: THREE.Vector3; color: string }[] = []
    for (const p of pulses.slice(-8)) {
      const from = botStation[p.from] || activeStation
      const to = botStation[p.to] || activeStation
      if (!from || !to) continue
      const a = centres[from]
      const b = centres[to]
      if (!a || !b) continue
      out.push({ id: p.id, a, b, color: stationOf(to).accent })
    }
    return out
  }, [pulses, botStation, centres, activeStation])
  const liveKeys = new Set(livePulses.map((p) => `${p.a.x.toFixed(1)}|${p.b.x.toFixed(1)}`))

  const curvePoints = useMemo(() => curve.slice(-64), [curve])

  return (
    <>
      <fog attach="fog" args={[danger ? '#1c0509' : '#0a1220', 46, 145]} />
      <color attach="background" args={[danger ? '#12060a' : '#0a1322']} />
      <Exposure value={danger ? 1.02 : 1.16} />
      <group>

      {/* ── light rig ─────────────────────────────────────────────── */}
      <ambientLight intensity={danger ? 0.5 : mid ? 0.78 : 0.6} color={danger ? '#ffd5d5' : '#dceaff'} />
      <hemisphereLight intensity={danger ? 0.55 : mid ? 0.95 : 0.7} color="#bcdcff" groundColor="#3d4a5e" />
      <directionalLight
        position={[18, 26, 30]} intensity={danger ? 1.6 : mid ? 2.0 : 1.6} color="#ffffff"
        castShadow={rich} shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-26} shadow-camera-right={26}
        shadow-camera-top={20} shadow-camera-bottom={-20}
      />
      {/* fill from the far side: the room must never fall to silhouette */}
      <directionalLight position={[-22, 17, -16]} intensity={danger ? 0.45 : 0.8} color="#9fd8ff" />
      {mid && (
        <Environment resolution={rich ? 128 : 64} frames={1}>
          <Lightformer form="rect" intensity={rich ? 3.4 : 2.4} color="#9fd8ff"
            position={[0, 9, 14]} rotation={[Math.PI / 2, 0, 0]} scale={[18, 8, 1]} />
          <Lightformer form="rect" intensity={rich ? 2.6 : 1.8} color="#22d3ee"
            position={[-14, 6, -12]} rotation={[0, Math.PI / 3, 0]} scale={[14, 7, 1]} />
          <Lightformer form="rect" intensity={rich ? 2.2 : 1.5} color="#f472b6"
            position={[14, 5, -10]} rotation={[0, -Math.PI / 3, 0]} scale={[12, 6, 1]} />
          <Lightformer form="circle" intensity={rich ? 2.0 : 1.5} color="#34d399"
            position={[0, 4, -16]} scale={[9, 9, 1]} />
          <Lightformer form="ring" intensity={rich ? 1.6 : 1.2} color="#ffffff"
            position={[0, 10, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[16, 16, 1]} />
          {/* overhead softbox — gives every metal surface something bright to mirror */}
          <Lightformer form="rect" intensity={2.6} color="#ffffff"
            position={[0, 12, 0]} rotation={[Math.PI / 2, 0, 0]} scale={[34, 20, 1]} />
        </Environment>
      )}

      <Room quality={detail || 'balanced'} night={!mid} />
      <Truss />
      <CeilingLights night={!mid} lights={mid} />
      {/* one soft light per station, tinted in that station's own accent */}
      {mid && STATIONS.map((st) => {
        const c = centres[st.key]
        if (!c) return null
        return (
          <pointLight key={`z-${st.key}`} position={[c.x, 3.8, c.z]}
            color={accentOf(st.accent)} intensity={danger ? 9 : 17} distance={19} decay={2} />
        )
      })}
      <Dais accent={accentOf('#22d3ee')} active={activeStation === 'command'} />

      {/* ── furniture built from the seating plan ─────────────────── */}
      {seats.map((s) => {
        const f = s.furniture
        const accent = accentOf(stationOf(s.station || 'command').accent)
        const fwd = new THREE.Vector3(Math.sin(s.yaw), 0, Math.cos(s.yaw))
        if (s.mode === 'walk' || !f) return null
        if (f.kind === 'desk') {
          const p = new THREE.Vector3(s.position[0], s.position[1], s.position[2]).addScaledVector(fwd, 0.74)
          return (
            <Desk key={s.id} position={[p.x, s.position[1], p.z]} yaw={s.yaw + Math.PI}
              accent={accent} chairs={false} onScreen={() => onSelect?.(s.id)} />
          )
        }
        if (f.kind === 'console') {
          const p = new THREE.Vector3(s.position[0], s.position[1], s.position[2]).addScaledVector(fwd, 1.0)
          return (
            <Console key={s.id} position={[p.x, s.position[1], p.z]} yaw={s.yaw + Math.PI}
              accent={accent} screens={2} chairs={false} />
          )
        }
        return null
      })}
      {seats.filter((s) => s.mode === 'sit').map((s) => (
        <Chair key={`c-${s.id}`} position={s.position} yaw={s.yaw} />
      ))}

      {/* CEO podium + the verification gate + the vault */}
      {(() => {
        const ceo = seats.find((s) => s.furniture?.kind === 'podium')
        if (!ceo) return null
        const fwd = new THREE.Vector3(Math.sin(ceo.yaw), 0, Math.cos(ceo.yaw))
        const p = new THREE.Vector3(ceo.position[0], ceo.position[1], ceo.position[2]).addScaledVector(fwd, 1.15)
        return (
          <Podium position={[p.x, ceo.position[1], p.z]} accent={accentOf('#22d3ee')}
            onClick={() => onScan?.()} />
        )
      })()}
      <Gate position={[-0.9, 0, -2.4]} accent={accentOf('#a78bfa')}
        active={activeStation === 'verify'} onClick={() => onFocus?.('verify')} />
      <Vault position={[-22.7, 2.3, 9]} accent={accentOf('#34d399')} />
      <HoloCurve points={curvePoints} position={[-21.4, 3.5, 9]} accent={accentOf('#34d399')} />
      {mid && <>
        <Plant position={[-21.6, 0, -12.5]} />
        <Plant position={[21.6, 0, 12.6]} scale={1.15} />
        <Plant position={[-21.8, 0, 13.2]} scale={0.85} />
        <Plant position={[12.4, 0, 12.8]} />
      </>}

      {/* ── screens ───────────────────────────────────────────────── */}
      <BigScreen position={[4.5, 6.3, -14.6]} accent={accentOf('#22d3ee')}
        onClick={() => setView((v) => (v + 1) % views.length)}>
        <Board w={14.6} h={4.5} px={1024} accent={accentOf('#22d3ee')}
          title={board.title} rows={board.rows} bars={curvePoints} />
      </BigScreen>
      <VideoWall position={[22.7, 0, -4.2]} onSelect={() => setView((v) => (v + 1) % views.length)}
        accents={[accentOf('#f472b6'), accentOf('#38bdf8'), accentOf('#fbbf24'), accentOf('#34d399')]} />

      {/* small board over the scanner bay + at the execution pods */}
      <group position={[-22.6, 4.4, -2]} rotation={[0, Math.PI / 2, 0]}>
        <Board w={7.2} h={2.2} px={640} accent={accentOf('#00e5a8')} title="scanner bay"
          rows={[
            ['assets', `${(snapshot?.groups?.find((g) => g.key === 'scanner')?.total ?? 5) * 30}`],
            ['synced', `${snapshot?.scanSeconds !== undefined ? '1m live' : '—'}`],
            ['bar closes', `${Math.max(0, Math.round(snapshot?.scanSeconds || 0))}s`],
          ]} bars={curvePoints} />
      </group>
      <group position={[-4.7, 3.5, 3.6]} rotation={[0, -Math.PI / 2, 0]}>
        <Board w={5.4} h={1.8} px={512} accent={accentOf('#fbbf24')} title="execution pods"
          rows={[
            ['open', `${snapshot?.open ?? 0}/${snapshot?.max ?? 10}`],
            ['trail armed', `${snapshot?.trailArmed ?? 0}`],
            ['margin used', `$${(snapshot?.marginUsed ?? 0).toFixed(0)}`],
          ]} />
      </group>

      {/* ── floor zones: click to fly to a station ────────────────── */}
      {STATIONS.map((st) => {
        const c = centres[st.key]
        if (!c) return null
        const radius = st.key === 'command' ? 5.2 : st.key === 'analyze' ? 4.6
          : st.key === 'scan' ? 3.2 : st.key === 'monitor' ? 3.4
            : st.key === 'finance' ? 3.6 : 2.8
        return (
          <ZoneMark key={st.key} position={[c.x, 0, c.z]} radius={radius}
            accent={accentOf(st.accent)} label={st.label}
            active={activeStation === st.key || focus === st.key}
            onClick={() => onFocus?.(focus === st.key ? null : st.key)} />
        )
      })}

      {/* ── workflow rails + live pulses ───────────────────────────── */}
      {railPairs.map((r) => (
        <Rail key={r.key} a={r.a} b={r.b} color={r.color}
          live={liveKeys.has(`${r.a.x.toFixed(1)}|${r.b.x.toFixed(1)}`)} />
      ))}
      {livePulses.map((p) => (
        <PulseDot key={p.id} a={p.a} b={p.b} color={accentOf(p.color)} />
      ))}

      {/* ── the crew ──────────────────────────────────────────────── */}
      {seats.map((s) => {
        const bot = byId[s.id]
        if (!bot) return null
        const status = bot.status
        const mood = bot.mood
        const mode = (status === 'celebrating' || mood === 'excited') ? 'celebrate' as const
          : (status === 'sad' || mood === 'sad') ? 'sad' as const
            : s.mode
        const station = s.station || 'command'
        const working = status === 'working' || status === 'success'
          || (!!activeStation && activeStation === station)
        const label = rich || selected === bot.bot_id || hover === bot.bot_id
          || status === 'celebrating' || status === 'sad' || working
        const body = (
          <Human
            bot={bot} look={looks[bot.bot_id]} position={[0, 0, 0]} yaw={0} mode={mode}
            working={working} handoff={handoffs[bot.bot_id] || 0} detail={detail}
            shadows={rich} selected={selected === bot.bot_id}
            hovered={hover === bot.bot_id} label={label}
            patrol={s.mode === 'walk' ? mid : false}
            onSelect={(id) => { onSelect?.(id); onFocus?.(botStation[id] || station) }}
            onHover={setHover}
          />
        )
        return (
          <React.Fragment key={`h-${s.id}`}>
            <group position={s.position} rotation={[0, s.yaw, 0]}>
              {SCANS_ON ? (
                <ScanBoundary fallback={body}>
                  <React.Suspense fallback={body}>
                    <ScanAgent
                      url={`${SCAN_SLOT}/${bot.bot_id}.glb`} bot={bot}
                      scale={looks[bot.bot_id].height / 1.78} label={label}
                      selected={selected === bot.bot_id}
                      onSelect={(id) => { onSelect?.(id); onFocus?.(botStation[id] || station) }}
                      onHover={setHover}
                    />
                  </React.Suspense>
                </ScanBoundary>
              ) : body}
            </group>
            {(status === 'celebrating' || mood === 'excited') && mid && (
              <Sparkles count={rich ? 26 : 14} scale={[1.4, 1.6, 1.4]}
                position={[s.position[0], s.position[1] + 2.1, s.position[2]]}
                size={3} speed={0.5} color={bot.color || '#22d3ee'} />
            )}
          </React.Fragment>
        )
      })}

      {rich && (
        <ContactShadows position={[0, 0.02, 0]} opacity={0.5} scale={80} blur={2.2}
          far={14} resolution={1024} color="#000000" />
      )}

      {/* ── danger overlay: SOS beacon ────────────────────────────── */}
      {danger && <SosBeacon />}

        <CameraRig focus={focus} />
        <OrbitControls
          makeDefault target={[0, 2.4, 2]} enableDamping dampingFactor={0.08}
          minDistance={4} maxDistance={62} maxPolarAngle={Math.PI / 2.08}
          enabled={false}
        />
      </group>
    </>
  )
}

const SosBeacon: React.FC = () => {
  const light = useRef<THREE.PointLight>(null!)
  const cone = useRef<THREE.Mesh>(null!)
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime()
    const pulse = 0.5 + Math.abs(Math.sin(t * 3.4))
    if (light.current) light.current.intensity = 4 + pulse * 26
    if (cone.current) {
      cone.current.rotation.y = t * 1.1
      const m = cone.current.material as THREE.MeshBasicMaterial
      m.opacity = 0.06 + pulse * 0.16
    }
  })
  return (
    <group position={[0, 6, 2]}>
      <pointLight ref={light} color="#ef4444" distance={46} intensity={16} />
      <mesh position={[0, 2.6, 0]}>
        <sphereGeometry args={[0.42, 16, 16]} />
        <meshBasicMaterial color="#ef4444" toneMapped={false} />
      </mesh>
      <mesh ref={cone} position={[0, -2.4, 0]} rotation={[Math.PI, 0, 0]}>
        <coneGeometry args={[7.5, 8.4, 30, 1, true]} />
        <meshBasicMaterial color="#ef4444" transparent opacity={0.14}
          side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
    </group>
  )
}

/* ────────────────────────────── the wrapper ────────────────────────────── */

const PLACEHOLDER = (
  <div className="flex h-full w-full items-center justify-center">
    <p className="mono text-[0.7rem] dim">
      the 3D headquarters needs WebGL — opening the command deck in a browser will render it
    </p>
  </div>
)

/** If the renderer itself fails, say so instead of leaving a black hole. */
class HQBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(err: unknown) { console.error('[hq] the 3D floor failed to render:', err) }
  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-1">
        <p className="mono text-[0.7rem]" style={{ color: 'var(--color-amber)' }}>
          the 3D floor hit a renderer error
        </p>
        <p className="mono text-[0.62rem] dim">
          the swarm map view and every number on this page are unaffected
        </p>
      </div>
    )
  }
}

export const HQ: React.FC<HQProps> = (props) => {
  const {
    detail = 'balanced', danger = false, focus = null, onFocus, className = '',
    bots, pulses, snapshot, selected, curve, onSelect, onScan,
  } = props
  const [hover, setHover] = useState<string | null>(null)
  const [handoffs, setHandoffs] = useState<Record<string, number>>({})
  const seen = useRef<Set<number>>(new Set())

  /* every rail pulse leaves a gesture on the two agents it touches */
  useEffect(() => {
    for (const p of pulses) {
      if (seen.current.has(p.id)) continue
      seen.current.add(p.id)
      setHandoffs((prev) => {
        const next = { ...prev }
        next[p.from] = (next[p.from] || 0) + 1
        next[p.to] = (next[p.to] || 0) + 1
        return next
      })
    }
    if (seen.current.size > 600) seen.current = new Set(pulses.map((p) => p.id))
  }, [pulses])

  /* clicking empty space deselects */
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!selected) return
    const t = window.setTimeout(() => onSelect?.(null), 60_000)
    return () => window.clearTimeout(t)
  }, [selected, onSelect])

  const alive = WEBGL_OK
  const stations = STATIONS

  return (
    <div ref={rootRef} className={`relative overflow-hidden ${className}`}>
      {alive ? (
        <HQBoundary>
        <Canvas
          shadows={detail === 'cinematic'}
          dpr={detail === 'cinematic' ? [1, 1.75] : detail === 'balanced' ? [1, 1.5] : [0.75, 1]}
          camera={{ position: OVERVIEW.pos, fov: 45, near: 0.4, far: 400 }}
          gl={{ antialias: detail !== 'performance', powerPreference: 'high-performance' }}
          onPointerMissed={() => { onSelect?.(null) }}
        >
          <React.Suspense fallback={null}>
            <Scene
              {...props}
              bots={bots} pulses={pulses} snapshot={snapshot} selected={selected}
              curve={curve} danger={danger} detail={detail}
              focus={focus} hover={hover} setHover={setHover} handoffs={handoffs}
              onSelect={onSelect} onFocus={onFocus} onScan={onScan}
            />
          </React.Suspense>
        </Canvas>
        </HQBoundary>
      ) : PLACEHOLDER}

      {/* ── station rail (bottom-left) ─────────────────────────────── */}
      <div className="pointer-events-none absolute bottom-3 left-3 flex max-w-[70%] flex-wrap gap-1.5">
        <button
          className="pointer-events-auto chip"
          style={{
            cursor: 'pointer',
            borderColor: focus === null ? 'var(--color-cyan)' : undefined,
            color: focus === null ? 'var(--color-cyan)' : undefined,
          }}
          onClick={() => onFocus?.(null)}
          title="Fly the camera back to the whole floor"
        >
          overview
        </button>
        {stations.map((st) => (
          <button
            key={st.key}
            className="pointer-events-auto chip"
            style={{
              cursor: 'pointer',
              borderColor: focus === st.key ? st.accent : undefined,
              color: focus === st.key ? st.accent : undefined,
            }}
            onClick={() => onFocus?.(focus === st.key ? null : st.key)}
            title={`${st.label} · ${st.group}`}
          >
            {st.label}
          </button>
        ))}
      </div>

      {/* ── legend (bottom-right) ──────────────────────────────────── */}
      {!selected && (
        <p className="pointer-events-none absolute bottom-4 right-3 mono text-[0.58rem] dim">
          drag to orbit · scroll to zoom · click an agent for its card
        </p>
      )}
    </div>
  )
}

export default HQ
