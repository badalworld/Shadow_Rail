import React, { useMemo, useRef, useState } from 'react'
import { useFrame } from '@react-three/fiber'
import { Html, Sparkles, useCursor } from '@react-three/drei'
import * as THREE from 'three'
import type { Bot } from '../../lib/types'
import { statusColor } from '../Glass'
import {
  PATROL, PELVIS_Y, buildSizes, type Detail, type HumanMode, type Look,
} from './layout'

/**
 * A lifelike office human, in the Renderpeople catalogue language: business /
 * smart-casual clothing, typed by activity (`sit` typing at a desk, `stand`
 * presenting, `walk` patrolling, plus celebrating / sad moods), with varied
 * build, height, skin tone, hair and age.
 *
 * The body is a proper little rig — pelvis → spine → neck/head and four limb
 * chains — so poses are animated, not swapped meshes.  Every agent gets a
 * deterministic look (`buildLook`) and an ID badge in its own signal colour, so
 * the same bot always looks like the same person.
 *
 * Licensed Renderpeople scans can replace these bodies: drop the GLBs in
 * `public/models/people/` and see the README (`SCAN_SLOT`).
 */

const H = {
  spineAt: 0.18, chestY: 0.16, shoulderY: 0.30, shoulderX: 0.205,
  neckY: 0.30, headY: 0.095,
  upperArm: { r: 0.052, len: 0.20 },     // total 0.304
  foreArm: { r: 0.045, len: 0.19 },      // total 0.280
  hipX: 0.105,
  thigh: { r: 0.086, len: 0.28 },        // total 0.452
  shin: { r: 0.062, len: 0.30 },         // total 0.424
}

const armDrop = H.upperArm.len + H.upperArm.r * 2
const foreDrop = H.foreArm.len + H.foreArm.r * 2
const thighDrop = H.thigh.len + H.thigh.r * 2
const shinDrop = H.shin.len + H.shin.r * 2

export interface HumanProps {
  bot: Bot
  look: Look
  position: [number, number, number]
  yaw: number
  mode: HumanMode
  /** the bot's own workflow stage is running → typing / scanning motion */
  working?: boolean
  /** bumps when a hand-off pulse leaves this agent → gesture */
  handoff?: number
  /** bump when this agent's trade closes → brief celebration */
  celebrateAt?: number
  detail?: Detail
  shadows?: boolean
  selected?: boolean
  hovered?: boolean
  label?: boolean
  patrol?: boolean
  onSelect?: (botId: string) => void
  onHover?: (botId: string | null) => void
}

export const Human: React.FC<HumanProps> = ({
  bot, look, position, yaw, mode, working = false, handoff = 0, celebrateAt = 0,
  detail = 'balanced', shadows = true, selected = false, hovered = false,
  label = true, patrol = false, onSelect, onHover,
}) => {
  const rich = detail === 'cinematic'
  const mid = detail !== 'performance'

  const root = useRef<THREE.Group>(null!)
  const pelvis = useRef<THREE.Group>(null!)
  const spine = useRef<THREE.Group>(null!)
  const neck = useRef<THREE.Group>(null!)
  const head = useRef<THREE.Group>(null!)
  const armA = useRef<THREE.Group>(null!)
  const armB = useRef<THREE.Group>(null!)
  const elbowA = useRef<THREE.Group>(null!)
  const elbowB = useRef<THREE.Group>(null!)
  const handA = useRef<THREE.Group>(null!)
  const thighA = useRef<THREE.Group>(null!)
  const thighB = useRef<THREE.Group>(null!)
  const kneeA = useRef<THREE.Group>(null!)
  const kneeB = useRef<THREE.Group>(null!)
  const [hover, setHover] = useState(false)
  useCursor(hover)

  const accent = bot.color || '#22d3ee'
  const state = statusColor(bot.status)
  const sizes = buildSizes(look.build)
  const h = look.height / 1.78 * 1.08                      // whole-body height scale
  const chestW = 0.40 * sizes.chest
  const hipW = 0.35 * sizes.waist
  const coatW = chestW + 0.075 * sizes.chest

  /* the patrol path, walked by the maintenance bot */
  const route = useMemo(() => {
    if (!patrol) return null
    const pts = PATROL.map(([x, z]) => new THREE.Vector3(x, 0, z))
    const segs: { a: THREE.Vector3; b: THREE.Vector3; len: number }[] = []
    let total = 0
    for (let i = 0; i < pts.length - 1; i++) {
      const len = pts[i].distanceTo(pts[i + 1])
      segs.push({ a: pts[i], b: pts[i + 1], len })
      total += len
    }
    return { segs, total }
  }, [patrol])

  const gestureUntil = useRef(0)
  const celebrateUntil = useRef(0)
  const lastHandoff = useRef(handoff)
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime()
    const ph = look.phase
    // a new pulse that touched this agent → one clear gesture, then back to work
    if (handoff > lastHandoff.current) {
      lastHandoff.current = handoff
      gestureUntil.current = Math.max(gestureUntil.current, t + 1.25)
    }
    if (celebrateAt) celebrateUntil.current = Math.max(celebrateUntil.current, t + 2.6)
    const gesturing = t < gestureUntil.current && !patrol
    const partying = t < celebrateUntil.current

    let py = PELVIS_Y[mode] * h
    let sx = 0.02, hx = 0.0, hy = 0.0, spineZ = 0.0
    let aX = 0, bX = 0, aZ = 0.09, bZ = -0.09, aF = -0.14, bF = -0.14
    let tA = 0, tB = 0, kA = 0, kB = 0
    let walkYaw: number | null = null

    if (mode === 'sit') {
      tA = -1.45; tB = -1.45; kA = 1.45; kB = 1.45
      sx = 0.10; hx = 0.14
      aX = working ? -0.60 : -0.46
      bX = working ? -0.60 : -0.46
      aF = working ? -1.00 : -0.72
      bF = working ? -1.00 : -0.72
      if (working) {
        const tap = Math.sin(t * 7.4 + ph) * 0.06
        aF += tap; bF -= tap
        hx += Math.sin(t * 3.1 + ph) * 0.02
      }
      py += Math.sin(t * 1.5 + ph) * 0.006 * h
    } else if (mode === 'walk' && route) {
      const speed = 0.85
      let d = (t * speed + ph * 2) % route.total
      let seg = route.segs[0]
      for (const s of route.segs) { if (d <= s.len) { seg = s; break } d -= s.len }
      const k = seg.len > 0 ? d / seg.len : 0
      const x = seg.a.x + (seg.b.x - seg.a.x) * k
      const z = seg.a.z + (seg.b.z - seg.a.z) * k
      walkYaw = Math.atan2(seg.b.x - seg.a.x, seg.b.z - seg.a.z)
      if (root.current) root.current.position.set(x, 0.02, z)
      const step = Math.sin(t * 3.0 + ph)
      tA = step * 0.46; tB = -step * 0.46
      kA = Math.max(0, -step) * 0.75; kB = Math.max(0, step) * 0.75
      aX = -step * 0.42; bX = step * 0.42
      py += Math.abs(Math.sin(t * 3.0 + ph)) * 0.028 * h
      hx = 0.04
    } else if (mode === 'celebrate') {
      py += Math.abs(Math.sin(t * 5.2 + ph)) * 0.13 * h
      aX = -2.55; bX = -2.55; aZ = 2.45; bZ = -2.45
      aF = -0.18; bF = -0.18
      hx = -0.20; sx = -0.05
      tA = 0.05; tB = -0.05
    } else if (mode === 'sad') {
      sx = 0.30; hx = 0.44
      aX = 0.12; bX = 0.12; aZ = 0.02; bZ = -0.02
      aF = -0.22; bF = -0.22
      py += Math.sin(t * 1.1 + ph) * 0.008 * h
    } else {
      // standing: idle sway, looking around, presenting when it is their turn
      py += Math.sin(t * 1.4 + ph) * 0.008 * h
      sx = 0.015 + Math.sin(t * 0.7 + ph) * 0.012
      hy = Math.sin(t * 0.33 + ph) * 0.42
      hx = Math.sin(t * 0.55 + ph * 1.7) * 0.05
      aZ = 0.12; bZ = -0.12
      aF = -0.2; bF = -0.2
      if (working) { aX = -0.55; aF = -1.15; hy = Math.sin(t * 0.8) * 0.2 }
    }

    if (gesturing) { aX = -1.52; aF = -0.35; aZ = 0.3 }
    if (partying) {
      py += Math.abs(Math.sin(t * 5.2 + ph)) * 0.13 * h
      aX = -2.5; bX = -2.5; aZ = 2.4; bZ = -2.4
      aF = -0.2; bF = -0.2
      hx = -0.22
    }

    const breathe = 1 + Math.sin(t * 1.6 + ph) * 0.012
    if (pelvis.current) { pelvis.current.position.y = py; pelvis.current.scale.x = breathe }
    if (spine.current) { spine.current.rotation.x = sx + spineZ; spine.current.rotation.y = Math.sin(t * 0.5 + ph) * 0.05 }
    if (head.current) { head.current.rotation.x = hx; head.current.rotation.y = hy + (gesturing ? 0.2 : 0) }
    if (neck.current) neck.current.rotation.x = hx * 0.35
    if (armA.current) { armA.current.rotation.x = aX; armA.current.rotation.z = aZ }
    if (armB.current) { armB.current.rotation.x = bX; armB.current.rotation.z = bZ }
    if (elbowA.current) elbowA.current.rotation.x = aF
    if (elbowB.current) elbowB.current.rotation.x = bF
    if (handA.current) handA.current.rotation.x = -aF * 0.35
    if (thighA.current) { thighA.current.rotation.x = tA; thighA.current.rotation.z = 0.03 }
    if (thighB.current) { thighB.current.rotation.x = tB; thighB.current.rotation.z = -0.03 }
    if (kneeA.current) kneeA.current.rotation.x = kA
    if (kneeB.current) kneeB.current.rotation.x = kB
    if (root.current && walkYaw === null) root.current.rotation.y = yaw
    if (root.current && walkYaw !== null) root.current.rotation.y = walkYaw
  })

  /* ── materials ─────────────────────────────────────────────────────── */
  const skin = <meshPhysicalMaterial color={look.skin} roughness={0.56} metalness={0}
    clearcoat={0.08} clearcoatRoughness={0.6} sheen={0.25} sheenRoughness={0.7} />
  const cloth = <meshPhysicalMaterial color={look.suit} roughness={0.86} metalness={0.02}
    sheen={0.5} sheenRoughness={0.9} sheenColor="#c9d4e2" />
  const shirtMat = <meshPhysicalMaterial color={look.shirt} roughness={0.8}
    sheen={0.35} sheenRoughness={0.85} sheenColor="#ffffff" />
  const hairMat = <meshStandardMaterial color={look.hair} roughness={0.82} metalness={0.04} />
  const shoeMat = <meshPhysicalMaterial color={look.shoe} roughness={0.3} clearcoat={0.55}
    clearcoatRoughness={0.3} />
  const trouser = <meshPhysicalMaterial color={look.suit} roughness={0.9} metalness={0.02} />
  const sigil = <meshStandardMaterial color={accent} emissive={accent}
    emissiveIntensity={selected ? 0.85 : 0.5} roughness={0.4} />

  const showShadow = shadows && detail !== 'performance'

  return (
    <group ref={root} position={position}>
      {/* selection + hover affordance on the floor */}
      {(selected || hovered) && (
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.012, 0]}>
          <ringGeometry args={[0.52, selected ? 0.66 : 0.58, 48]} />
          <meshBasicMaterial color={selected ? accent : state} transparent
            opacity={selected ? 0.85 : 0.45} />
        </mesh>
      )}

      <group ref={pelvis} position={[0, PELVIS_Y[mode] * h, 0]} scale={[1, h, 1]}>
        {/* hips / trousers */}
        <mesh castShadow={showShadow} position={[0, 0.04, 0]}>
          <boxGeometry args={[hipW, 0.2, 0.23]} />
          {trouser}
        </mesh>
        {rich && (
          <mesh position={[0, 0.13, 0]}>
            <boxGeometry args={[hipW * 0.98, 0.05, 0.235]} />
            <meshStandardMaterial color="#0a0d13" roughness={0.5} />
          </mesh>
        )}

        <group ref={spine} position={[0, H.spineAt, 0]}>
          {/* chest under the jacket */}
          <mesh position={[0, H.chestY, 0]} castShadow={showShadow}>
            <boxGeometry args={[chestW, 0.34, 0.22]} />
            {shirtMat}
          </mesh>
          {/* jacket shell */}
          <mesh position={[0, H.chestY + 0.015, -0.005]} castShadow={showShadow}>
            <boxGeometry args={[coatW, 0.40, 0.27]} />
            {cloth}
          </mesh>
          {mid && (
            <>
              {/* shirt front + lapels + tie: reads as a suit from the front */}
              <mesh position={[0, H.chestY + 0.02, 0.132]}>
                <boxGeometry args={[0.155, 0.31, 0.02]} />
                {shirtMat}
              </mesh>
              <mesh position={[0.075, H.chestY + 0.045, 0.145]} rotation={[0, 0, -0.16]}>
                <boxGeometry args={[0.075, 0.30, 0.016]} />
                {cloth}
              </mesh>
              <mesh position={[-0.075, H.chestY + 0.045, 0.145]} rotation={[0, 0, 0.16]}>
                <boxGeometry args={[0.075, 0.30, 0.016]} />
                {cloth}
              </mesh>
              <mesh position={[0, H.chestY + 0.015, 0.155]}>
                <boxGeometry args={[0.042, 0.24, 0.012]} />
                {sigil}
              </mesh>
              {/* ID badge in the agent's own colour */}
              <mesh position={[0.085, H.chestY - 0.11, 0.145]}>
                <boxGeometry args={[0.05, 0.075, 0.01]} />
                {sigil}
              </mesh>
              <mesh position={[0.085, H.chestY + 0.03, 0.133]}>
                <boxGeometry args={[0.012, 0.16, 0.008]} />
                <meshStandardMaterial color={accent} roughness={0.5} transparent opacity={0.5} />
              </mesh>
            </>
          )}

          {/* neck + head */}
          <group ref={neck} position={[0, H.neckY, 0]}>
            <mesh position={[0, 0.03, 0]}>
              <cylinderGeometry args={[0.048, 0.055, 0.09, 16]} />
              {skin}
            </mesh>
            <group ref={head} position={[0, H.headY, 0]}>
              <mesh castShadow={showShadow} scale={[1, 1.16, 1.04]}>
                <sphereGeometry args={[0.107, 24, 20]} />
                {skin}
              </mesh>
              {mid && (
                <>
                  {/* hair: short, long or shaved — varied across the cast */}
                  {!look.bald && (
                    <mesh position={[0, 0.028, -0.008]} scale={[1.02, 0.82, 1.0]}>
                      <sphereGeometry args={[0.112, 22, 18]} />
                      {hairMat}
                    </mesh>
                  )}
                  {look.longHair && !look.bald && (
                    <mesh position={[0, -0.10, -0.065]} scale={[1, 1, 0.8]}>
                      <boxGeometry args={[0.185, 0.26, 0.08]} />
                      {hairMat}
                    </mesh>
                  )}
                  {look.beard && (
                    <mesh position={[0, -0.062, 0.018]} scale={[1, 0.72, 0.88]}>
                      <sphereGeometry args={[0.088, 18, 14]} />
                      {hairMat}
                    </mesh>
                  )}
                  <mesh position={[0.098, 0.008, 0]} scale={[0.42, 1, 0.72]}>
                    <sphereGeometry args={[0.03, 12, 10]} />
                    {skin}
                  </mesh>
                  <mesh position={[-0.098, 0.008, 0]} scale={[0.42, 1, 0.72]}>
                    <sphereGeometry args={[0.03, 12, 10]} />
                    {skin}
                  </mesh>
                </>
              )}
              {rich && (
                <>
                  {/* face */}
                  <mesh position={[0.036, 0.012, 0.088]}>
                    <sphereGeometry args={[0.0125, 12, 12]} />
                    <meshStandardMaterial color="#1b1410" roughness={0.35} />
                  </mesh>
                  <mesh position={[-0.036, 0.012, 0.088]}>
                    <sphereGeometry args={[0.0125, 12, 12]} />
                    <meshStandardMaterial color="#1b1410" roughness={0.35} />
                  </mesh>
                  <mesh position={[0.036, 0.042, 0.092]} rotation={[0, 0, -0.1]}>
                    <boxGeometry args={[0.038, 0.008, 0.012]} />
                    {hairMat}
                  </mesh>
                  <mesh position={[-0.036, 0.042, 0.092]} rotation={[0, 0, 0.1]}>
                    <boxGeometry args={[0.038, 0.008, 0.012]} />
                    {hairMat}
                  </mesh>
                  <mesh position={[0, -0.004, 0.098]} rotation={[-Math.PI / 2, 0, 0]}>
                    <coneGeometry args={[0.019, 0.045, 4]} />
                    {skin}
                  </mesh>
                  <mesh position={[0, -0.042, 0.09]}>
                    <boxGeometry args={[0.036, 0.008, 0.012]} />
                    <meshStandardMaterial color="#8d5348" roughness={0.6} />
                  </mesh>
                  {look.glasses && (
                    <>
                      <mesh position={[0.036, 0.014, 0.096]} rotation={[Math.PI / 2, 0, 0]}>
                        <torusGeometry args={[0.021, 0.0035, 8, 20]} />
                        <meshPhysicalMaterial color="#c8d2dd" metalness={0.7} roughness={0.25} />
                      </mesh>
                      <mesh position={[-0.036, 0.014, 0.096]} rotation={[Math.PI / 2, 0, 0]}>
                        <torusGeometry args={[0.021, 0.0035, 8, 20]} />
                        <meshPhysicalMaterial color="#c8d2dd" metalness={0.7} roughness={0.25} />
                      </mesh>
                      <mesh position={[0, 0.014, 0.094]}>
                        <boxGeometry args={[0.03, 0.004, 0.004]} />
                        <meshPhysicalMaterial color="#c8d2dd" metalness={0.7} roughness={0.25} />
                      </mesh>
                    </>
                  )}
                </>
              )}
            </group>
          </group>

          {/* arms: shoulder → elbow → hand */}
          <group ref={armA} position={[H.shoulderX * sizes.shoulder, H.shoulderY, 0]}>
            <mesh>
              <sphereGeometry args={[0.062, 14, 12]} />
              {cloth}
            </mesh>
            <mesh position={[0, -armDrop / 2, 0]} castShadow={showShadow}>
              <capsuleGeometry args={[H.upperArm.r, H.upperArm.len, 6, 14]} />
              {cloth}
            </mesh>
            <group ref={elbowA} position={[0, -armDrop, 0]}>
              <mesh position={[0, -foreDrop / 2, 0]}>
                <capsuleGeometry args={[H.foreArm.r, H.foreArm.len, 6, 14]} />
                {shirtMat}
              </mesh>
              <group ref={handA} position={[0, -foreDrop, 0]}>
                <mesh scale={[1, 1.1, 0.62]}>
                  <sphereGeometry args={[0.055, 14, 12]} />
                  {skin}
                </mesh>
                {rich && (
                  <mesh position={[0, 0.045, 0]} rotation={[Math.PI / 2, 0, 0]}>
                    <torusGeometry args={[0.042, 0.008, 8, 18]} />
                    <meshPhysicalMaterial color={accent} metalness={0.85} roughness={0.2} />
                  </mesh>
                )}
              </group>
            </group>
          </group>
          <group ref={armB} position={[-H.shoulderX * sizes.shoulder, H.shoulderY, 0]}>
            <mesh>
              <sphereGeometry args={[0.062, 14, 12]} />
              {cloth}
            </mesh>
            <mesh position={[0, -armDrop / 2, 0]} castShadow={showShadow}>
              <capsuleGeometry args={[H.upperArm.r, H.upperArm.len, 6, 14]} />
              {cloth}
            </mesh>
            <group ref={elbowB} position={[0, -armDrop, 0]}>
              <mesh position={[0, -foreDrop / 2, 0]}>
                <capsuleGeometry args={[H.foreArm.r, H.foreArm.len, 6, 14]} />
                {shirtMat}
              </mesh>
              <mesh position={[0, -foreDrop, 0]} scale={[1, 1.1, 0.62]}>
                <sphereGeometry args={[0.055, 14, 12]} />
                {skin}
              </mesh>
            </group>
          </group>
        </group>

        {/* legs: hip → knee → ankle */}
        <group ref={thighA} position={[H.hipX, 0, 0]}>
          <mesh position={[0, -thighDrop / 2, 0]} castShadow={showShadow}>
            <capsuleGeometry args={[H.thigh.r, H.thigh.len, 6, 14]} />
            {trouser}
          </mesh>
          <group ref={kneeA} position={[0, -thighDrop, 0]}>
            <mesh position={[0, -shinDrop / 2, 0]}>
              <capsuleGeometry args={[H.shin.r, H.shin.len, 6, 14]} />
              {trouser}
            </mesh>
            <mesh position={[0, -shinDrop - 0.03, 0.045]}>
              <boxGeometry args={[0.098, 0.062, 0.25]} />
              {shoeMat}
            </mesh>
          </group>
        </group>
        <group ref={thighB} position={[-H.hipX, 0, 0]}>
          <mesh position={[0, -thighDrop / 2, 0]} castShadow={showShadow}>
            <capsuleGeometry args={[H.thigh.r, H.thigh.len, 6, 14]} />
            {trouser}
          </mesh>
          <group ref={kneeB} position={[0, -thighDrop, 0]}>
            <mesh position={[0, -shinDrop / 2, 0]}>
              <capsuleGeometry args={[H.shin.r, H.shin.len, 6, 14]} />
              {trouser}
            </mesh>
            <mesh position={[0, -shinDrop - 0.03, 0.045]}>
              <boxGeometry args={[0.098, 0.062, 0.25]} />
              {shoeMat}
            </mesh>
          </group>
        </group>
      </group>

      {/* hitbox: fat invisible capsule makes clicking a person easy */}
      <mesh position={[0, 0.95, 0]} visible={false}
        onPointerOver={(e) => { e.stopPropagation(); setHover(true); onHover?.(bot.bot_id) }}
        onPointerOut={() => { setHover(false); onHover?.(null) }}
        onClick={(e) => { e.stopPropagation(); onSelect?.(bot.bot_id) }}>
        <capsuleGeometry args={[0.42, 1.0, 4, 8]} />
        <meshBasicMaterial />
      </mesh>

      {mode === 'celebrate' && detail !== 'performance' && (
        <Sparkles count={16} scale={[0.7, 1.0, 0.7]} position={[0, 1.35, 0]} size={3}
          speed={1.4} color={accent} opacity={0.9} />
      )}

      {label && (
        <Html position={[0, (mode === 'sit' ? 1.28 : 1.95) * h, 0]} center
          distanceFactor={26} zIndexRange={[12, 0]}>
          <div onClick={() => onSelect?.(bot.bot_id)}
            className="cursor-pointer select-none whitespace-nowrap rounded-full px-2 py-[2px] text-center"
            style={{
              background: 'rgba(4,8,14,0.78)',
              border: `1px solid ${(selected ? accent : state)}88`,
              color: 'var(--sr-text)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              letterSpacing: '0.03em',
              boxShadow: selected ? `0 0 14px ${accent}66` : undefined,
            }}>
            {/* name only — the floor is not a status board */}
            <span style={{ color: accent }}>{bot.name}</span>
          </div>
        </Html>
      )}
    </group>
  )
}
