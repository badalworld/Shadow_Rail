import React, { useMemo, useRef, useState, useEffect } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Html, OrbitControls } from '@react-three/drei'
import * as THREE from 'three'
import type { Bot, Link } from '../lib/types'
import { statusColor } from './Glass'

/* ═══════════════════════════════════════════════════════════════════════════
   The swarm map: every bot is a glowing node, every workflow link is a curved
   rail. Pulses travel along the rails as the engine works, and the whole
   constellation tints red when the Connector Bot raises SOS.
   ═══════════════════════════════════════════════════════════════════════════ */

const GROUP_Y: Record<string, number> = {
  core: 6.4,
  scanner: 1.6,
  analyst: -2.6,
  execution: -6.0,
  verify: -6.0,
  monitor: 6.4,
  finance: 1.6,
}

interface Node3D {
  bot: Bot
  pos: THREE.Vector3
}

function layout(bots: Bot[]): Record<string, Node3D> {
  const nodes: Record<string, Node3D> = {}
  const groups: Record<string, Bot[]> = {}
  bots.forEach((b) => { (groups[b.group] ||= []).push(b) })

  const place = (list: Bot[], y: number, xSpan: number, zOffset = 0, stagger = 0) => {
    const n = Math.max(1, list.length)
    list.forEach((b, i) => {
      const t = n === 1 ? 0.5 : i / (n - 1)
      nodes[b.bot_id] = {
        bot: b,
        pos: new THREE.Vector3(
          (t - 0.5) * xSpan,
          y + Math.sin(i * 1.7) * 0.35,
          zOffset + (stagger ? (i % 2 === 0 ? stagger : -stagger) : 0),
        ),
      }
    })
  }

  // core trio on the crown, scanners in an arc, analysts a wide row, etc.
  const core = (groups.core || [])
  place(core.filter((b) => b.bot_id === 'ceo-bot'), GROUP_Y.core + 2.6, 0)
  place(core.filter((b) => b.bot_id !== 'ceo-bot'), GROUP_Y.core + 0.4, 12, -2.5)

  const right = (groups.monitor || [])
  place(right, GROUP_Y.monitor - 1.2, 11, 9, 1.2)

  const scanners = groups.scanner || []
  scanners.forEach((b, i) => {
    const t = scanners.length === 1 ? 0.5 : i / (scanners.length - 1)
    const x = (t - 0.5) * 20
    nodes[b.bot_id] = {
      bot: b,
      pos: new THREE.Vector3(x, GROUP_Y.scanner - Math.cos((t - 0.5) * Math.PI) * 1.6, -1.5),
    }
  })

  place(groups.analyst || [], GROUP_Y.analyst, 26)
  place(groups.execution || [], GROUP_Y.execution, 9, -1)
  place(groups.verify || [], GROUP_Y.verify + 2.2, 4, 4)
  place(groups.finance || [], GROUP_Y.finance - 1.0, 12, -10, 1.5)
  return nodes
}

function teamCentroid(nodes: Record<string, Node3D>, group: string): THREE.Vector3 {
  const list = Object.values(nodes).filter((n) => n.bot.group === group)
  if (!list.length) return new THREE.Vector3()
  const v = new THREE.Vector3()
  list.forEach((n) => v.add(n.pos))
  return v.divideScalar(list.length)
}

function resolveNode(nodes: Record<string, Node3D>, id: string): THREE.Vector3 | null {
  if (nodes[id]) return nodes[id].pos
  if (id.endsWith('-team')) {
    const group = id.replace('-team', '')
    if (group === 'scanner') return teamCentroid(nodes, 'scanner')
    return teamCentroid(nodes, group)
  }
  return null
}

const NodeMesh: React.FC<{
  node: Node3D
  danger: boolean
  selected: boolean
  onSelect: (id: string) => void
  reduced: boolean
}> = ({ node, danger, selected, onSelect, reduced }) => {
  const ref = useRef<THREE.Mesh>(null)
  const halo = useRef<THREE.Mesh>(null)
  const { bot, pos } = node
  const color = danger ? '#ff2d55' : statusColor(bot.status) === '#8aa0b6' ? bot.color : statusColor(bot.status)
  const busy = bot.status === 'working'
  const seed = bot.bot_id.length

  useFrame(({ clock }) => {
    const t = clock.getElapsedTime()
    if (ref.current) {
      ref.current.rotation.y = t * (busy ? 0.9 : 0.18) + seed
      ref.current.rotation.x = Math.sin(t * 0.4 + seed) * 0.25
      const bob = reduced ? 0 : Math.sin(t * 0.9 + seed) * 0.18
      ref.current.position.set(pos.x, pos.y + bob, pos.z)
    }
    if (halo.current) {
      const s = busy ? 1 + Math.sin(t * 3.2) * 0.12 : 1 + Math.sin(t * 0.9) * 0.03
      halo.current.scale.setScalar(s)
      halo.current.position.set(pos.x, pos.y, pos.z)
    }
  })

  const size = bot.bot_id === 'ceo-bot' ? 0.72 : bot.group === 'scanner' ? 0.42 : 0.5

  return (
    <group>
      <mesh ref={halo} position={pos}>
        <sphereGeometry args={[size * 1.85, 20, 20]} />
        <meshBasicMaterial color={color} transparent opacity={busy ? 0.14 : 0.06} />
      </mesh>
      <mesh ref={ref} position={pos} onClick={(e) => { e.stopPropagation(); onSelect(bot.bot_id) }}>
        <icosahedronGeometry args={[size, 1]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={busy ? 1.5 : selected ? 1.0 : 0.45}
          metalness={0.65}
          roughness={0.22}
          flatShading
        />
        {selected && (
          <mesh>
            <torusGeometry args={[size * 1.5, 0.02, 8, 48]} />
            <meshBasicMaterial color="#ffffff" />
          </mesh>
        )}
      </mesh>
      <Html position={[pos.x, pos.y - size - 0.55, pos.z]} center distanceFactor={22} zIndexRange={[10, 0]}>
        <div
          onClick={() => onSelect(bot.bot_id)}
          className="cursor-pointer select-none whitespace-nowrap rounded-full px-2 py-[2px] text-center"
          style={{
            background: 'rgba(4,8,14,0.72)',
            border: `1px solid ${color}66`,
            color: 'var(--sr-text)',
            fontFamily: 'var(--font-mono)',
            fontSize: 9.5,
            letterSpacing: '0.06em',
          }}
        >
          <span style={{ color }}>{bot.name}</span>
          <span className="dim"> · {bot.rank}</span>
          {bot.mood === 'happy' && ' 🎉'}
          {bot.mood === 'sad' && ' 💧'}
          {bot.mood === 'excited' && ' ✨'}
        </div>
      </Html>
    </group>
  )
}

const Rail: React.FC<{ from: THREE.Vector3; to: THREE.Vector3; color: string; active: boolean }> =
  ({ from, to, color, active }) => {
    const mid = useMemo(() => {
      const m = from.clone().add(to).multiplyScalar(0.5)
      m.y += from.distanceTo(to) * 0.16 + 0.8
      return m
    }, [from, to])
    const curve = useMemo(() => new THREE.QuadraticBezierCurve3(from, mid, to), [from, mid, to])
    const geo = useMemo(() => new THREE.TubeGeometry(curve, 42, active ? 0.032 : 0.018, 6, false),
      [curve, active])
    return (
      <mesh geometry={geo}>
        <meshBasicMaterial color={color} transparent opacity={active ? 0.85 : 0.26} />
      </mesh>
    )
  }

const Pulse: React.FC<{ from: THREE.Vector3; to: THREE.Vector3; color: string }> = ({ from, to, color }) => {
  const ref = useRef<THREE.Mesh>(null)
  const t0 = useRef(0)
  const mid = useMemo(() => {
    const m = from.clone().add(to).multiplyScalar(0.5)
    m.y += from.distanceTo(to) * 0.16 + 0.8
    return m
  }, [from, to])
  const curve = useMemo(() => new THREE.QuadraticBezierCurve3(from, mid, to), [from, mid, to])
  useFrame(({ clock }) => {
    if (!ref.current) return
    if (!t0.current) t0.current = clock.getElapsedTime()
    const p = Math.min(1, (clock.getElapsedTime() - t0.current) / 1.15)
    const v = curve.getPoint(p)
    ref.current.position.copy(v)
    const s = 0.16 * (1 - p * 0.4)
    ref.current.scale.setScalar(s)
    ;(ref.current.material as THREE.MeshBasicMaterial).opacity = 1 - p * 0.7
  })
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[1, 12, 12]} />
      <meshBasicMaterial color={color} transparent />
    </mesh>
  )
}

const Scene: React.FC<{
  bots: Bot[]
  links: Link[]
  pulses: { id: number; from: string; to: string; stage: string }[]
  danger: boolean
  onSelect: (id: string) => void
  autoRotate: boolean
  reduced: boolean
}> = ({ bots, links, pulses, danger, onSelect, autoRotate, reduced }) => {
  const nodes = useMemo(() => layout(bots), [bots])
  const activePairs = useMemo(() => pulses.map((p) => ({ ...p })), [pulses])
  const { camera } = useThree()
  useEffect(() => {
    camera.position.set(0, 9, 34)
  }, [camera])

  return (
    <>
      <ambientLight intensity={0.55} />
      <pointLight position={[12, 16, 12]} intensity={95} color={danger ? '#ff2d55' : '#22d3ee'} />
      <pointLight position={[-14, -8, -10]} intensity={70} color={danger ? '#ff6b3d' : '#00e5a8'} />
      <fog attach="fog" args={[danger ? '#14030a' : '#05070c', 26, 68]} />

      {links.map((l, i) => {
        const a = resolveNode(nodes, l.from)
        const b = resolveNode(nodes, l.to)
        if (!a || !b) return null
        const active = activePairs.some((p) => p.from === l.from && p.to === l.to)
        return <Rail key={`${l.from}-${l.to}-${i}`} from={a} to={b}
          color={danger ? '#ff2d55' : active ? 'var(--color-cyan)' : 'var(--color-cyan)'}
          active={active} />
      })}

      {activePairs.map((p) => {
        const a = resolveNode(nodes, p.from)
        const b = resolveNode(nodes, p.to)
        if (!a || !b) return null
        return <Pulse key={p.id} from={a.clone()} to={b.clone()}
          color={danger ? '#ff8fa3' : '#7ef7d1'} />
      })}

      {Object.values(nodes).map((n) => (
        <NodeMesh key={n.bot.bot_id} node={n} danger={danger} reduced={reduced}
          selected={false} onSelect={onSelect} />
      ))}

      <gridHelper args={[70, 34, danger ? '#5a1020' : '#12e5ac22', '#ffffff10']} position={[0, -8.4, 0]} />
      <OrbitControls enablePan enableZoom enableRotate autoRotate={autoRotate} autoRotateSpeed={0.42}
        minDistance={12} maxDistance={78} maxPolarAngle={Math.PI * 0.86} />
    </>
  )
}

export const BotMap3D: React.FC<{
  bots: Bot[]
  links: Link[]
  pulses: { id: number; from: string; to: string; stage: string }[]
  danger: boolean
  onSelect: (id: string) => void
  className?: string
}> = ({ bots, links, pulses, danger, onSelect, className = '' }) => {
  const [autoRotate, setAutoRotate] = useState(true)
  const reduced = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  return (
    <div className={`relative ${className}`}>
      <Canvas dpr={[1, 1.75]} camera={{ position: [0, 9, 34], fov: 48 }}
        gl={{ antialias: true, alpha: true }}>
        <Scene bots={bots} links={links} pulses={pulses} danger={danger}
          onSelect={onSelect} autoRotate={autoRotate} reduced={reduced} />
      </Canvas>
      <div className="absolute right-3 top-3 flex gap-2">
        <button onClick={() => setAutoRotate((v) => !v)}
          className="chip hover:opacity-90" style={{ cursor: 'pointer' }}>
          {autoRotate ? '⟳ auto-orbit on' : '⟳ auto-orbit off'}
        </button>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-2 text-[0.6rem]">
        {['core', 'scanner', 'analyst', 'execution', 'verify', 'monitor', 'finance'].map((g) => (
          <span key={g} className="chip dim">{g}</span>
        ))}
      </div>
    </div>
  )
}
