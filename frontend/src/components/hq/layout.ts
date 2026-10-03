/**
 * HQ floor plan + the Renderpeople-style cast.
 *
 * renderpeople.com sells scanned, rigged people filtered by *activity*
 * (sitting / typing / standing / walking / pointing / talking), *place*
 * (office), *clothing* (business / smart casual), *body type*, *age* and
 * *ethnicity*.  These scans are licensed assets we cannot bundle, so the cast
 * is generated procedurally with the same catalogue language: office workers in
 * business / smart-casual clothing, typed by activity, with varied skin tones,
 * hair, builds, heights and ages — and a drop-in slot for licensed GLB scans
 * (see `SCAN_SLOT` below).
 *
 * All numbers are metres.  The room is 48 × 30 with a 10 m ceiling; the camera
 * starts at +z looking down the hall.
 */
import type { Bot } from '../../lib/types'

/* ─────────────────────────── scan drop-in slot ─────────────────────────── */
/** Put licensed Renderpeople GLBs here and set VITE_HQ_SCANS=on to use them. */
export const SCAN_SLOT = '/models/people'

export type StationKey =
  | 'command' | 'scan' | 'analyze' | 'execute' | 'verify' | 'monitor' | 'finance'

export interface Station {
  key: StationKey
  label: string
  group: string
  /** stage name in the engine workflow that lights this bay up */
  stage: string
  accent: string
  /** camera preset */
  cam: { pos: [number, number, number]; target: [number, number, number] }
}

export const ROOM = { halfX: 23, halfZ: 15, height: 10 }

export const STATIONS: Station[] = [
  {
    key: 'command', label: 'Command Deck', group: 'core', stage: 'connector',
    accent: '#22d3ee',
    cam: { pos: [0, 6.4, 19], target: [0, 2.7, 5.0] },
  },
  {
    key: 'scan', label: 'Scanner Bay', group: 'scanner', stage: 'scan',
    accent: '#00e5a8',
    cam: { pos: [-8.5, 5.4, 0.5], target: [-18, 1.7, -6] },
  },
  {
    key: 'analyze', label: 'Analyst Wing', group: 'analyst', stage: 'analyze',
    accent: '#38bdf8',
    cam: { pos: [4.5, 6.2, 4.5], target: [9.5, 1.7, -9] },
  },
  {
    key: 'execute', label: 'Execution Pods', group: 'execution', stage: 'execute',
    accent: '#fbbf24',
    cam: { pos: [-2.2, 4.4, 3.6], target: [-9.2, 1.6, 3.4] },
  },
  {
    key: 'verify', label: 'Verification Gate', group: 'verify', stage: 'verify',
    accent: '#a78bfa',
    cam: { pos: [1.2, 4.0, 5.6], target: [0.4, 2.0, -3.2] },
  },
  {
    key: 'monitor', label: 'Monitor Wall', group: 'monitor', stage: 'monitor',
    accent: '#f472b6',
    cam: { pos: [11.5, 5.6, -3.5], target: [19.4, 2.4, -6.2] },
  },
  {
    key: 'finance', label: 'Vault & Ledger', group: 'finance', stage: 'close',
    accent: '#34d399',
    cam: { pos: [-11, 6.0, 4.5], target: [-18.6, 2.3, 4.0] },
  },
]

export const STATION_BY_GROUP: Record<string, StationKey> = {
  core: 'command', scanner: 'scan', analyst: 'analyze',
  execution: 'execute', verify: 'verify', monitor: 'monitor', finance: 'finance',
}

export const stationOf = (key: StationKey): Station =>
  STATIONS.find((s) => s.key === key) ?? STATIONS[0]

/* ────────────────────────────── the cast ───────────────────────────────── */

export type BodyType = 'average' | 'slim' | 'fit' | 'curvy'
export type HumanMode = 'sit' | 'stand' | 'walk' | 'celebrate' | 'sad'
export type Detail = 'cinematic' | 'balanced' | 'performance'

export interface Look {
  skin: string
  hair: string
  suit: string
  shirt: string
  tie: string
  shoe: string
  height: number          // metres, 1.62 – 1.92
  build: BodyType
  longHair: boolean
  bald: boolean
  beard: boolean
  glasses: boolean
  earring: boolean
  /** per-agent motion phase so nobody moves in lockstep */
  phase: number
}

/* Renderpeople's catalogue runs the full range — the office cast should too. */
const SKIN = ['#f4d6bd', '#eec3a1', '#dda87c', '#c9905f', '#b4794c', '#96603a',
  '#7a4c2c', '#5d3a22', '#f7e0cb', '#e0b48c']
const HAIR = ['#171310', '#241a12', '#33210f', '#4a3218', '#6b4a24', '#a3723c',
  '#c49a5f', '#8f8f8f', '#e6e3dc', '#2c2320']
const SUIT = ['#141a24', '#1b2230', '#222a38', '#2a3140', '#0f1725', '#2d3444',
  '#1d2733', '#333a48']
const SHIRT = ['#eef2f7', '#e2e9f2', '#d3dce8', '#f6f8fb', '#cfd9e6']
const SHOE = ['#0a0c11', '#15181f', '#241d16', '#101317']

/** Deterministic pseudo-random from a bot id — the same agent always looks the same. */
function hash(seed: string): () => number {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507)
    h = Math.imul(h ^ (h >>> 13), 3266489909)
    return ((h ^= h >>> 16) >>> 0) / 4294967296
  }
}

const pick = <T,>(arr: T[], r: number) => arr[Math.min(arr.length - 1, Math.floor(r * arr.length))]

export function buildLook(bot: Bot): Look {
  const r = hash(bot.bot_id)
  const a = r(), b = r(), c = r(), d = r(), e = r(), f = r(), g = r(), h = r()
  const builds: BodyType[] = ['average', 'slim', 'fit', 'curvy']
  const build = builds[Math.floor(b * builds.length) % builds.length]
  const buildBonus = build === 'curvy' ? 0.05 : build === 'fit' ? 0.03 : build === 'slim' ? -0.03 : 0
  return {
    skin: pick(SKIN, a),
    hair: pick(HAIR, c),
    suit: pick(SUIT, d),
    shirt: pick(SHIRT, e),
    tie: bot.color || '#22d3ee',
    shoe: pick(SHOE, f),
    height: 1.62 + g * 0.30 + buildBonus,
    build,
    longHair: h > 0.66,
    bald: h < 0.12,
    beard: r() > 0.72,
    glasses: r() > 0.78,
    earring: r() > 0.9,
    phase: r() * Math.PI * 2,
  }
}

const buildScale = (build: BodyType) => ({
  average: { chest: 1, waist: 1, shoulder: 1 },
  slim: { chest: 0.9, waist: 0.86, shoulder: 0.94 },
  fit: { chest: 1.06, waist: 0.96, shoulder: 1.06 },
  curvy: { chest: 1.08, waist: 1.12, shoulder: 1.0 },
}[build])

/* ──────────────────────────────── seating ──────────────────────────────── */

export interface Seat {
  id: string
  position: [number, number, number]
  /** yaw in radians; the human faces +z when this is 0 */
  yaw: number
  mode: HumanMode
  station: StationKey
  /** desk anchor: where the furniture goes + how it is rotated */
  furniture?: { kind: 'desk' | 'console' | 'podium' | 'gate' | 'wall' | 'vault'; yaw: number }
}

const DESK_FACE = Math.PI / 2          // scanners face the room centre (+x → yaw 90°)
const WALL_FACE = Math.PI / 2         // monitor crew face their wall (yaw +90° → +x)

/**
 * Where every agent physically stands or sits.  Deterministic: scanners in a row
 * along the west wall, analysts in two rows facing the big screen, execution in
 * pods, the CEO on the dais, and so on.
 */
export function seatsFor(bots: Bot[]): Seat[] {
  const out: Seat[] = []
  const byGroup = (g: string) => bots.filter((b) => b.group === g).sort((a, b) => a.slot - b.slot)

  // ── command deck: CEO at the podium, connector + api guard at the consoles,
  //    the maintenance bot patrols (walk mode, no desk)
  const core = byGroup('core')
  core.forEach((b) => {
    if (b.bot_id === 'ceo-bot') {
      out.push({ id: b.bot_id, position: [0, 0.36, 4.5], yaw: Math.PI, mode: 'stand',
        station: 'command', furniture: { kind: 'podium', yaw: 0 } })
    } else if (b.bot_id === 'maintenance-bot') {
      out.push({ id: b.bot_id, position: [-3.4, 0.02, 10.6], yaw: Math.PI, mode: 'walk',
        station: 'command' })
    } else {
      const i = b.bot_id === 'connector-bot' ? -1 : 1
      out.push({ id: b.bot_id, position: [i * 3.6, 0.36, 7.6], yaw: Math.PI + i * 0.25,
        mode: 'sit', station: 'command', furniture: { kind: 'console', yaw: Math.PI } })
    }
  })

  // ── scanner bay: a row of five desks along the west wall
  byGroup('scanner').forEach((b, i) => {
    out.push({
      id: b.bot_id, position: [-19.2, 0.02, -7.4 + i * 2.7], yaw: DESK_FACE,
      mode: 'sit', station: 'scan', furniture: { kind: 'desk', yaw: DESK_FACE },
    })
  })

  // ── analyst wing: two rows of five, both facing the big screen (−z)
  byGroup('analyst').forEach((b, i) => {
    const row = i < 5 ? 0 : 1
    const col = i % 5
    out.push({
      id: b.bot_id,
      position: [3.6 + col * 2.7, 0.02, row === 0 ? -10.4 : -7.2],
      yaw: Math.PI,
      mode: 'sit', station: 'analyze', furniture: { kind: 'desk', yaw: Math.PI },
    })
  })

  // ── execution pods
  byGroup('execution').forEach((b, i) => {
    out.push({
      id: b.bot_id, position: [-9.2, 0.02, 1.8 + i * 3.6], yaw: DESK_FACE,
      mode: 'sit', station: 'execute', furniture: { kind: 'console', yaw: DESK_FACE },
    })
  })

  // ── the verification gate: one officer beside the arch
  byGroup('verify').forEach((b) => {
    out.push({
      id: b.bot_id, position: [2.1, 0.02, -2.4], yaw: -DESK_FACE,
      mode: 'stand', station: 'verify', furniture: { kind: 'gate', yaw: 0 },
    })
  })

  // ── monitor wall: four stations facing their screens
  byGroup('monitor').forEach((b, i) => {
    out.push({
      id: b.bot_id, position: [19.2, 0.02, -8.2 + i * 2.7], yaw: WALL_FACE,
      mode: 'sit', station: 'monitor', furniture: { kind: 'desk', yaw: WALL_FACE },
    })
  })

  // ── vault & ledger: three desks by the vault door, the risk bot among the pods
  const finance = byGroup('finance')
  finance.forEach((b, i) => {
    const risk = b.bot_id === 'risk-bot'
    out.push({
      id: b.bot_id,
      position: risk ? [-12.4, 0.02, 6.6] : [-18.6, 0.02, 5.4 + i * 2.7],
      yaw: risk ? DESK_FACE : DESK_FACE,
      mode: risk ? 'stand' : 'sit',
      station: 'finance',
      furniture: risk ? undefined : { kind: 'desk', yaw: DESK_FACE },
    })
  })

  // any agent this build does not know about still gets a desk in the vault
  const placed = new Set(out.map((s) => s.id))
  bots.filter((b) => !placed.has(b.bot_id)).forEach((b, i) => {
    out.push({
      id: b.bot_id, position: [-18.6, 0.02, 12.2 + i * 2.7], yaw: DESK_FACE,
      mode: 'sit', station: 'finance', furniture: { kind: 'desk', yaw: DESK_FACE },
    })
  })
  return out
}

/** The patrol route for the maintenance bot (a closed loop through the hall). */
export const PATROL: [number, number][] = [
  [-3.4, 10.6], [-14.5, 10.6], [-20.0, 1.0], [-14.5, -10.5], [-4.0, -12.0],
  [8.0, -12.4], [16.5, 0.0], [8.0, 11.0], [-3.4, 10.6],
]

/** Hip-joint height per pose, chosen so the shoes actually touch the floor:
 *  the leg chain (thigh 0.452 + shin + shoe) drops 0.937, and a seated hip sits
 *  0.57 above the floor for the 0.46 m chair seat used everywhere. */
export const PELVIS_Y: Record<HumanMode, number> = {
  sit: 0.57, stand: 0.94, walk: 0.94, celebrate: 0.94, sad: 0.94,
}

export const buildSizes = buildScale
