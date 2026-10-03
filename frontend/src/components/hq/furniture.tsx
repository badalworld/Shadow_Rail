import React, { useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html, MeshReflectorMaterial } from '@react-three/drei'
import * as THREE from 'three'
import { ROOM } from './layout'

/**
 * The headquarters itself: polished stone floor, glass walls, the command dais,
 * curved trading consoles, desks with live monitors, the verification gate, the
 * vault with the equity hologram, and the big screen the whole room faces.
 *
 * Everything is built from a small material vocabulary (dark brushed metal,
 * laminate, glass, emissive panels) so it reads like a real trading floor and
 * still renders in a few hundred draw calls.
 */

const METAL = { color: '#2a3140', metalness: 0.85, roughness: 0.35 }
const DARK = { color: '#12161f', metalness: 0.6, roughness: 0.45 }
const LAMINATE = { color: '#1b2230', metalness: 0.25, roughness: 0.55 }

/* ─────────────────────────────── room shell ────────────────────────────── */

export const Room: React.FC<{ quality: 'cinematic' | 'balanced' | 'performance'; night: boolean }> =
  ({ quality, night }) => {
    const { halfX, halfZ } = ROOM
    const wall = night ? '#0b1018' : '#101725'
    const parapet = 3.6                      // side walls stay low so the floor is
    const floor = quality === 'cinematic'    // always visible from outside
      ? (
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[halfX * 2, halfZ * 2]} />
          <MeshReflectorMaterial
            resolution={512} mixBlur={1.1} mixStrength={2.4} blur={[320, 70]}
            mirror={0.42} depthScale={1.1} minDepthThreshold={0.4}
            maxDepthThreshold={1.35} color="#0a0f18" metalness={0.62} roughness={0.82} />
        </mesh>
      )
      : (
        <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
          <planeGeometry args={[halfX * 2, halfZ * 2]} />
          <meshStandardMaterial color="#0b111b" metalness={0.45} roughness={0.55} />
        </mesh>
      )
    return (
      <group>
        {floor}
        {/* the back wall carries the big board + the video wall; the sides are
            parapets with an LED rail, so the hall reads as closed without a lid
            that would hide the floor from an outside camera */}
        <mesh position={[0, 5, -halfZ]} receiveShadow>
          <boxGeometry args={[halfX * 2, 10, 0.3]} />
          <meshStandardMaterial color={wall} metalness={0.35} roughness={0.72} />
        </mesh>
        {[halfX, -halfX].map((x) => (
          <group key={x}>
            <mesh position={[x, parapet / 2, 0]} receiveShadow>
              <boxGeometry args={[0.3, parapet, halfZ * 2]} />
              <meshStandardMaterial color={wall} metalness={0.35} roughness={0.72} />
            </mesh>
            <mesh position={[x, parapet + 0.04, 0]}>
              <boxGeometry args={[0.14, 0.07, halfZ * 2 - 0.4]} />
              <meshStandardMaterial color="#22d3ee" emissive="#22d3ee" emissiveIntensity={0.6} />
            </mesh>
          </group>
        ))}
        {/* floor glow strips front and back */}
        {[-halfZ + 0.2, halfZ - 0.2].map((z) => (
          <mesh key={z} position={[0, 0.06, z]}>
            <boxGeometry args={[halfX * 2 - 1, 0.06, 0.08]} />
            <meshStandardMaterial color="#22d3ee" emissive="#22d3ee" emissiveIntensity={0.5} />
          </mesh>
        ))}
      </group>
    )
  }

/** Hanging truss over the hall: beams carry the light bars. */
export const Truss: React.FC = () => {
  const y = ROOM.height - 0.24
  const cross: number[] = [-2, -5.5, -9, -12.5]
  const long: number[] = [-3, 0, 3]
  return (
    <group position={[0, y, 0]}>
      {cross.map((z) => (
        <mesh key={`c${z}`} position={[0, 0, z]}>
          <boxGeometry args={[ROOM.halfX * 2 - 0.6, 0.16, 0.16]} />
          <meshStandardMaterial color="#151b26" metalness={0.7} roughness={0.4} />
        </mesh>
      ))}
      {long.map((x) => (
        <mesh key={`l${x}`} position={[x, 0.02, 0]}>
          <boxGeometry args={[0.12, 0.12, ROOM.halfZ * 2 - 0.6]} />
          <meshStandardMaterial color="#151b26" metalness={0.7} roughness={0.4} />
        </mesh>
      ))}
    </group>
  )
}

/** Hanging light bars, suspended from the truss. */
export const CeilingLights: React.FC<{ night: boolean }> = ({ night }) => {
  const bars = useMemo(() => {
    const out: [number, number][] = []
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) out.push([i * 13, j * 8])
    return out
  }, [])
  const intensity = night ? 0.5 : 1.0
  return (
    <group position={[0, ROOM.height - 0.5, 0]}>
      {bars.map(([x, z]) => (
        <group key={`${x}-${z}`} position={[x, 0, z]}>
          <mesh position={[0, 0.32, 0]}>
            <boxGeometry args={[0.05, 0.44, 0.05]} />
            <meshStandardMaterial color="#1b2330" metalness={0.7} roughness={0.4} />
          </mesh>
          <mesh castShadow={false}>
            <boxGeometry args={[5.4, 0.12, 0.5]} />
            <meshStandardMaterial color="#0e131c" metalness={0.6} roughness={0.5} />
          </mesh>
          <mesh position={[0, -0.085, 0]}>
            <boxGeometry args={[5.0, 0.04, 0.36]} />
            <meshStandardMaterial color="#dff6ff" emissive="#cfefff"
              emissiveIntensity={intensity} toneMapped={false} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

/* ──────────────────────────────── screens ──────────────────────────────── */

const Screen: React.FC<{
  size?: [number, number]
  color?: string
  intensity?: number
  pulse?: boolean
}> = ({ size = [0.62, 0.36], color = '#22d3ee', intensity = 0.9, pulse = false }) => {
  const ref = useRef<THREE.MeshStandardMaterial>(null!)
  useFrame(({ clock }) => {
    if (!pulse || !ref.current) return
    const t = clock.getElapsedTime()
    ref.current.emissiveIntensity = intensity * (0.75 + Math.abs(Math.sin(t * 1.3)) * 0.45)
  })
  return (
    <group>
      <mesh>
        <boxGeometry args={[size[0] + 0.04, size[1] + 0.04, 0.028]} />
        <meshStandardMaterial color="#080b11" metalness={0.7} roughness={0.35} />
      </mesh>
      <mesh position={[0, 0, 0.018]}>
        <planeGeometry args={size} />
        <meshStandardMaterial ref={ref} color={color} emissive={color}
          emissiveIntensity={intensity} toneMapped={false} />
      </mesh>
    </group>
  )
}

/* ──────────────────────────────── desks ────────────────────────────────── */

export const Chair: React.FC<{ position: [number, number, number]; yaw: number }> =
  ({ position, yaw }) => (
    <group position={position} rotation={[0, yaw, 0]}>
      <mesh position={[0, 0.46, 0]}>
        <boxGeometry args={[0.5, 0.09, 0.5]} />
        <meshStandardMaterial color="#161a22" roughness={0.7} />
      </mesh>
      <mesh position={[0, 0.74, -0.23]} rotation={[0.12, 0, 0]}>
        <boxGeometry args={[0.46, 0.52, 0.08]} />
        <meshStandardMaterial color="#161a22" roughness={0.7} />
      </mesh>
      <mesh position={[0, 0.24, 0]}>
        <cylinderGeometry args={[0.045, 0.055, 0.44, 12]} />
        <meshStandardMaterial {...METAL} />
      </mesh>
      <mesh position={[0, 0.04, 0]}>
        <cylinderGeometry args={[0.3, 0.3, 0.03, 16]} />
        <meshStandardMaterial {...DARK} />
      </mesh>
    </group>
  )

/** A workstation: desk, monitor, keyboard, mug, and the chair. */
export const Desk: React.FC<{
  position: [number, number, number]
  yaw: number
  accent: string
  chairs?: boolean
  onScreen?: () => void
}> = ({ position, yaw, accent, chairs = true, onScreen }) => (
  <group position={position} rotation={[0, yaw, 0]}>
    {/* top + frame */}
    <mesh position={[0, 0.74, 0]} castShadow receiveShadow>
      <boxGeometry args={[1.75, 0.06, 0.85]} />
      <meshStandardMaterial {...LAMINATE} />
    </mesh>
    {[-0.8, 0.8].map((x) => (
      <mesh key={x} position={[x, 0.37, 0]}>
        <boxGeometry args={[0.06, 0.74, 0.78]} />
        <meshStandardMaterial {...DARK} />
      </mesh>
    ))}
    {/* monitor on an arm */}
    <mesh position={[0, 0.86, -0.28]}>
      <cylinderGeometry args={[0.035, 0.05, 0.22, 10]} />
      <meshStandardMaterial {...METAL} />
    </mesh>
    <group position={[0, 1.12, -0.26]} onPointerDown={onScreen}>
      <Screen size={[0.78, 0.44]} color={accent} intensity={0.85} pulse />
    </group>
    {/* keyboard + mouse + mug */}
    <mesh position={[-0.06, 0.785, 0.18]} rotation={[-0.06, 0, 0]}>
      <boxGeometry args={[0.42, 0.018, 0.15]} />
      <meshStandardMaterial color="#0d1117" roughness={0.5} />
    </mesh>
    <mesh position={[0.28, 0.79, 0.2]}>
      <boxGeometry args={[0.07, 0.025, 0.11]} />
      <meshStandardMaterial color="#0d1117" roughness={0.5} />
    </mesh>
    <mesh position={[0.72, 0.82, 0.22]}>
      <cylinderGeometry args={[0.045, 0.038, 0.1, 14]} />
      <meshStandardMaterial color="#e8ecf2" roughness={0.4} />
    </mesh>
    {chairs && <Chair position={[0, 0, 0.95]} yaw={Math.PI} />}
  </group>
)

/** The curved command consoles (connector + api-guard) and the trading pods. */
export const Console: React.FC<{
  position: [number, number, number]
  yaw: number
  accent: string
  screens?: number
  chairs?: boolean
  pedestal?: number
}> = ({ position, yaw, accent, screens = 1, chairs = true, pedestal = 0 }) => (
  <group position={position} rotation={[0, yaw, 0]}>
    <mesh position={[0, 0.74, 0]} castShadow receiveShadow>
      <boxGeometry args={[1.7, 0.07, 1.0]} />
      <meshStandardMaterial color="#151b26" metalness={0.5} roughness={0.4} />
    </mesh>
    <mesh position={[0, 0.36, -0.34]}>
      <boxGeometry args={[1.4, 0.72, 0.3]} />
      <meshStandardMaterial {...DARK} />
    </mesh>
    {/* glow strip under the console lip */}
    <mesh position={[0, 0.7, 0.49]}>
      <boxGeometry args={[1.5, 0.03, 0.03]} />
      <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.8} />
    </mesh>
    {Array.from({ length: screens }).map((_, i) => {
      const spread = screens === 1 ? 0 : (i - (screens - 1) / 2) * 0.78
      const tilt = screens === 1 ? 0 : (i - (screens - 1) / 2) * 0.34
      return (
        <group key={i} position={[spread, 1.16, -0.2]} rotation={[0, -tilt, 0]}>
          <Screen size={[0.8, 0.46]} color={accent} intensity={0.9} pulse />
        </group>
      )
    })}
    {chairs && <Chair position={[0, 0, 1.05]} yaw={Math.PI} />}
    {pedestal > 0 && (
      <mesh position={[0, 0.1, 0]}>
        <boxGeometry args={[1.9, 0.2, 1.2]} />
        <meshStandardMaterial {...DARK} />
      </mesh>
    )}
  </group>
)

/** The CEO podium on the dais — click it to order a scan cycle. */
export const Podium: React.FC<{
  position: [number, number, number]
  accent: string
  onClick?: () => void
}> = ({ position, accent, onClick }) => {
  const ring = useRef<THREE.Mesh>(null!)
  useFrame(({ clock }) => {
    if (!ring.current) return
    ring.current.rotation.z = clock.getElapsedTime() * 0.6
  })
  return (
    <group position={position}>
      <mesh position={[0, 0.5, 0]} castShadow>
        <cylinderGeometry args={[0.55, 0.7, 1.0, 6]} />
        <meshStandardMaterial color="#151c28" metalness={0.7} roughness={0.3} />
      </mesh>
      <mesh position={[0, 1.03, 0]} rotation={[-0.5, 0, 0]}>
        <boxGeometry args={[0.95, 0.05, 0.55]} />
        <meshStandardMaterial color="#0e131c" metalness={0.8} roughness={0.25} />
      </mesh>
      <group position={[0, 1.12, 0.02]} rotation={[-0.5, 0, 0]} onPointerDown={onClick}>
        <Screen size={[0.8, 0.4]} color={accent} intensity={1.0} pulse />
      </group>
      <mesh ref={ring} position={[0, 0.02, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.86, 0.95, 48, 1, 0, Math.PI * 1.4]} />
        <meshBasicMaterial color={accent} transparent opacity={0.75} />
      </mesh>
      <Html position={[0, 1.75, 0]} center distanceFactor={30} zIndexRange={[6, 0]}>
        <div className="pointer-events-none select-none whitespace-nowrap rounded-full px-2 py-[2px]"
          style={{
            background: 'rgba(4,8,14,0.7)', border: `1px solid ${accent}66`,
            fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.16em',
            textTransform: 'uppercase', color: accent,
          }}>
          ceo · order cycle
        </div>
      </Html>
    </group>
  )
}

/** The wall-sized screen the whole floor faces — click to change its view. */
export const BigScreen: React.FC<{
  position: [number, number, number]
  accent: string
  onClick?: () => void
  children?: React.ReactNode
}> = ({ position, accent, onClick, children }) => {
  const glow = useRef<THREE.MeshStandardMaterial>(null!)
  useFrame(({ clock }) => {
    if (!glow.current) return
    glow.current.emissiveIntensity = 0.55 + Math.abs(Math.sin(clock.getElapsedTime() * 0.5)) * 0.22
  })
  return (
    <group position={position}>
      <mesh position={[0, 0, -0.06]}>
        <boxGeometry args={[15.4, 5.0, 0.14]} />
        <meshStandardMaterial color="#080c12" metalness={0.85} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0, 0.015]}>
        <planeGeometry args={[15.0, 4.7]} />
        <meshStandardMaterial ref={glow} color="#071019" emissive={accent}
          emissiveIntensity={0.6} toneMapped={false} />
      </mesh>
      <mesh position={[0, -2.62, 0.08]}>
        <boxGeometry args={[15.0, 0.06, 0.06]} />
        <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.9} />
      </mesh>
      <group position={[0, 0, 0.12]} onPointerDown={onClick}>{children}</group>
    </group>
  )
}

/** Four monitoring panels spread along a wall — one per monitor agent. */
export const VideoWall: React.FC<{
  position: [number, number, number]
  accents: string[]
  spacing?: number
  y?: number
  onSelect?: (index: number) => void
}> = ({ position, accents, spacing = 2.7, y = 1.95, onSelect }) => {
  const w = spacing - 0.36
  const span = spacing * accents.length
  return (
    <group position={position} rotation={[0, -Math.PI / 2, 0]}>
      {/* frame */}
      <mesh position={[0, y, -0.1]}>
        <boxGeometry args={[span + 0.3, 2.1, 0.12]} />
        <meshStandardMaterial {...DARK} />
      </mesh>
      <mesh position={[0, y - 1.02, -0.04]}>
        <boxGeometry args={[span + 0.3, 0.05, 0.22]} />
        <meshStandardMaterial color="#2a3140" metalness={0.85} roughness={0.3} />
      </mesh>
      {accents.map((c, i) => (
        <group key={i} position={[(i - (accents.length - 1) / 2) * spacing, y, 0.02]}
          onPointerDown={() => onSelect?.(i)}>
          <Screen size={[w, 1.62]} color={c} intensity={0.75} pulse />
        </group>
      ))}
    </group>
  )
}

/** The verification gate every execution passes through. */
export const Gate: React.FC<{
  position: [number, number, number]
  accent: string
  active: boolean
  onClick?: () => void
}> = ({ position, accent, active, onClick }) => {
  const beam = useRef<THREE.Mesh>(null!)
  const curtain = useRef<THREE.Mesh>(null!)
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime()
    if (beam.current) beam.current.position.y = 1.0 + Math.sin(t * (active ? 2.6 : 0.9)) * 0.85
    if (curtain.current) {
      const m = curtain.current.material as THREE.MeshBasicMaterial
      m.opacity = (active ? 0.28 : 0.1) + Math.sin(t * 3) * 0.04
    }
  })
  return (
    <group position={position} onPointerDown={onClick}>
      {[-1.5, 1.5].map((x) => (
        <group key={x} position={[x, 0, 0]}>
          <mesh position={[0, 1.6, 0]} castShadow>
            <boxGeometry args={[0.42, 3.2, 0.42]} />
            <meshStandardMaterial color="#141a26" metalness={0.75} roughness={0.3} />
          </mesh>
          <mesh position={[0, 3.25, 0]}>
            <boxGeometry args={[0.46, 0.12, 0.46]} />
            <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.85} />
          </mesh>
        </group>
      ))}
      <mesh position={[0, 3.3, 0]}>
        <boxGeometry args={[3.5, 0.24, 0.5]} />
        <meshStandardMaterial color="#101623" metalness={0.8} roughness={0.28} />
      </mesh>
      <mesh ref={curtain} position={[0, 1.7, 0]}>
        <planeGeometry args={[2.6, 3.1]} />
        <meshBasicMaterial color={accent} transparent opacity={0.14} side={THREE.DoubleSide} />
      </mesh>
      <mesh ref={beam} position={[0, 1.2, 0]} rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.05, 0.05, 2.9, 12]} />
        <meshBasicMaterial color={accent} transparent opacity={0.8} />
      </mesh>
    </group>
  )
}

/** Vault door + the equity hologram above it. */
export const Vault: React.FC<{ position: [number, number, number]; accent: string }> =
  ({ position, accent }) => {
    const dial = useRef<THREE.Group>(null!)
    useFrame(({ clock }) => {
      if (dial.current) dial.current.rotation.z = clock.getElapsedTime() * 0.15
    })
    return (
      <group position={position}>
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <cylinderGeometry args={[1.9, 1.9, 0.25, 40]} />
          <meshStandardMaterial color="#1a212e" metalness={0.9} roughness={0.28} />
        </mesh>
        <mesh rotation={[0, Math.PI / 2, 0]} position={[0.14, 0, 0]}>
          <torusGeometry args={[1.55, 0.07, 10, 48]} />
          <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.7} />
        </mesh>
        <group ref={dial} position={[0.18, 0, 0]} rotation={[0, Math.PI / 2, 0]}>
          {[0, 1, 2, 3].map((i) => (
            <mesh key={i} rotation={[0, 0, (i * Math.PI) / 4]}>
              <boxGeometry args={[2.2, 0.09, 0.09]} />
              <meshStandardMaterial color="#2f3947" metalness={0.9} roughness={0.25} />
            </mesh>
          ))}
          <mesh rotation={[0, 0, Math.PI / 2]}>
            <cylinderGeometry args={[0.3, 0.3, 0.16, 20]} />
            <meshStandardMaterial color={accent} emissive={accent} emissiveIntensity={0.5} />
          </mesh>
        </group>
      </group>
    )
  }

export const Plant: React.FC<{ position: [number, number, number]; scale?: number }> =
  ({ position, scale = 1 }) => (
    <group position={position} scale={scale}>
      <mesh position={[0, 0.22, 0]} castShadow>
        <cylinderGeometry args={[0.24, 0.18, 0.44, 16]} />
        <meshStandardMaterial color="#232a34" metalness={0.4} roughness={0.6} />
      </mesh>
      <mesh position={[0, 0.6, 0]}>
        <cylinderGeometry args={[0.035, 0.05, 0.5, 8]} />
        <meshStandardMaterial color="#3c2f22" roughness={0.9} />
      </mesh>
      {[[0, 1.15, 0, 0.44], [0.22, 0.95, 0.1, 0.3], [-0.2, 1.0, -0.08, 0.32]].map((v, i) => (
        <mesh key={i} position={[v[0], v[1], v[2]]} castShadow>
          <icosahedronGeometry args={[v[3], 1]} />
          <meshStandardMaterial color={i === 1 ? '#1d5c3a' : '#206b42'}
            roughness={0.85} flatShading />
        </mesh>
      ))}
    </group>
  )

/** Floor decal + label for a station. */
export const ZoneMark: React.FC<{
  position: [number, number, number]
  accent: string
  label: string
  radius?: number
  active?: boolean
  onClick?: () => void
}> = ({ position, accent, label, radius = 3.2, active = false, onClick }) => {
  const ring = useRef<THREE.MeshBasicMaterial>(null!)
  useFrame(({ clock }) => {
    if (ring.current) {
      ring.current.opacity = active
        ? 0.34 + Math.abs(Math.sin(clock.getElapsedTime() * 1.6)) * 0.3
        : 0.14
    }
  })
  return (
    <group position={[position[0], 0.012, position[2]]} onPointerDown={onClick}>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[radius - 0.12, radius, 64]} />
        <meshBasicMaterial ref={ring} color={accent} transparent opacity={0.16} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]}>
        <circleGeometry args={[radius - 0.14, 48]} />
        <meshBasicMaterial color={accent} transparent opacity={active ? 0.07 : 0.03} />
      </mesh>
      <Html position={[0, 0.05, radius]} center distanceFactor={34} zIndexRange={[4, 0]}>
        <div className="pointer-events-none select-none whitespace-nowrap rounded-full px-2 py-[1px]"
          style={{
            background: 'rgba(4,8,14,0.66)', border: `1px solid ${accent}55`,
            fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.22em',
            textTransform: 'uppercase', color: accent,
          }}>
          {label}
        </div>
      </Html>
    </group>
  )
}

/** The equity hologram: a live curve floating over the vault. */
export const HoloCurve: React.FC<{
  points: number[]
  position: [number, number, number]
  accent: string
}> = ({ points, position, accent }) => {
  const geo = useMemo(() => {
    const pts = points.length > 2 ? points : [10000, 10010, 9990, 10040, 10020, 10060]
    const lo = Math.min(...pts)
    const hi = Math.max(...pts)
    const span = hi - lo || 1
    const v = pts.map((p, i) => new THREE.Vector3(
      (i / (pts.length - 1) - 0.5) * 7.2,
      ((p - lo) / span - 0.5) * 2.1,
      0,
    ))
    const curve = new THREE.CatmullRomCurve3(v)
    return new THREE.TubeGeometry(curve, 90, 0.045, 8, false)
  }, [points])

  return (
    <group position={position}>
      <mesh geometry={geo}>
        <meshBasicMaterial color={accent} transparent opacity={0.92} toneMapped={false} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
        <ringGeometry args={[3.7, 3.9, 64]} />
        <meshBasicMaterial color={accent} transparent opacity={0.28} />
      </mesh>
      <Html position={[0, 1.7, 0]} center distanceFactor={34} zIndexRange={[4, 0]}>
        <div className="pointer-events-none select-none whitespace-nowrap"
          style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.2em',
            textTransform: 'uppercase', color: accent, opacity: 0.8 }}>
          equity hologram
        </div>
      </Html>
    </group>
  )
}

/** Dais under the command deck. */
export const Dais: React.FC<{ accent: string; active?: boolean }> = ({ accent, active }) => {
  const ring = useRef<THREE.MeshBasicMaterial>(null!)
  useFrame(({ clock }) => {
    if (ring.current) {
      ring.current.opacity = active ? 0.5 + Math.abs(Math.sin(clock.getElapsedTime())) * 0.35 : 0.22
    }
  })
  return (
    <group position={[0, 0.02, 6]}>
      <mesh position={[0, 0.18, 0]} receiveShadow>
        <cylinderGeometry args={[5.4, 5.8, 0.36, 72]} />
        <meshStandardMaterial color="#0d131e" metalness={0.55} roughness={0.42} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.37, 0]}>
        <ringGeometry args={[5.05, 5.35, 72]} />
        <meshBasicMaterial ref={ring} color={accent} transparent opacity={0.3} />
      </mesh>
    </group>
  )
}
