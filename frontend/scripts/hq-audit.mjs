#!/usr/bin/env node
/*
 * hq-audit — checks the 3D headquarters plan against the live roster.
 *
 * The floor is data (`src/components/hq/layout.ts`): where every agent sits,
 * what furniture stands in front of them and how the room is sized.  Those
 * numbers never reach a unit test, and a scene cannot be rendered headless, so
 * this script bundles the real layout, runs it against the real bot list and
 * asserts the invariants a wrong number would break: nobody outside the walls,
 * nobody inside furniture, nobody standing on top of a colleague, and every
 * agent of the roster seated somewhere.
 *
 *   node scripts/hq-audit.mjs [http://localhost:8080]
 *
 * Exit code 1 on any violation, so it can gate a rebuild.
 */
import { build } from 'esbuild'
import { readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const API = process.argv[2] || process.env.SMOKE_API || 'http://localhost:8080'

const ENTRY = `import { seatsFor, ROOM, STATIONS, PELVIS_Y } from '../src/components/hq/layout'
export function plan(bots) {
  return { seats: seatsFor(bots), room: ROOM, stations: STATIONS, pelvis: PELVIS_Y }
}
export function seatsOf(bots) { return seatsFor(bots) }
`

const MARGIN = 0.55                       // bodies never touch the walls
const MIN_GAP = 1.15                      // centre-to-centre between colleagues
const LEG_DROP = 0.452 + 0.424 + 0.062    // thigh + shin + shoe: feet must land on the floor

const entryFile = resolve(here, '.hq-audit-entry.ts')
const outFile = resolve(here, '.hq-audit-bundle.cjs')
writeFileSync(entryFile, ENTRY)

const problems = []
const note = (ok, msg) => {
  console.log(`${ok ? '✓' : '✗'} ${msg}`)
  if (!ok) problems.push(msg)
}

/* the roster: the live engine first, then the captured fixture */
const roster = await (async () => {
  try {
    const res = await fetch(`${API}/api/bots`)
    const body = await res.json()
    if (Array.isArray(body.bots) && body.bots.length) {
      console.log(`· roster: live engine (${body.bots.length} agents)`)
      return body.bots
    }
  } catch { /* engine offline — fall back to the fixture */ }
  try {
    const body = JSON.parse(readFileSync(resolve(here, 'fixtures.json'), 'utf8'))
    const bots = body['/api/bots']?.bots || []
    console.log(`· roster: captured fixture (${bots.length} agents)`)
    return bots
  } catch {
    console.log('· roster: none available')
    return []
  }
})()

try {
  await build({
    entryPoints: [entryFile], bundle: true, format: 'cjs', platform: 'node',
    outfile: outFile, logLevel: 'error', jsx: 'automatic',
  })
  const { plan } = await import(outFile)
  const { seats, room, stations, pelvis } = plan(roster)
  const halfX = room.halfX - MARGIN
  const halfZ = room.halfZ - MARGIN

  const inside = (x, z) => Math.abs(x) <= halfX && Math.abs(z) <= halfZ

  // ── 1. every agent of the roster has a place on the floor ────────────────
  const ids = new Set(seats.map((s) => s.id))
  const missing = roster.filter((b) => !ids.has(b.bot_id)).map((b) => b.bot_id)
  note(missing.length === 0, `all ${roster.length} agents seated${missing.length ? ` — missing: ${missing}` : ''}`)

  const dupes = seats.map((s) => s.id).filter((id, i, a) => a.indexOf(id) !== i)
  note(dupes.length === 0, `no agent seated twice${dupes.length ? ` — ${dupes}` : ''}`)

  // ── 2. nobody is outside the room ───────────────────────────────────────
  const outside = seats.filter((s) => !inside(s.position[0], s.position[2]))
  note(outside.length === 0,
    `every body inside the walls${outside.length ? ` — ${outside.map((s) => `${s.id} (${s.position[0]}, ${s.position[2]})`)}` : ''}`)

  // furniture sits in front of its owner: desk 0.74 m + half depth, console 1.0 +
  // half its 0.5 depth — the far edge must still be inside the room.
  const depth = { desk: 0.42, console: 0.5, podium: 0.55, gate: 1.5, wall: 0, vault: 1.9 }
  const badFurniture = []
  for (const s of seats) {
    if (!s.furniture) continue
    const d = depth[s.furniture.kind] ?? 0.5
    const off = s.furniture.kind === 'console' ? 1.0 : s.furniture.kind === 'podium' ? 1.15 : 0.74
    const x = s.position[0] + Math.sin(s.yaw) * (off + d)
    const z = s.position[2] + Math.cos(s.yaw) * (off + d)
    if (!inside(x, z)) badFurniture.push(`${s.id} ${s.furniture.kind} → (${x.toFixed(1)}, ${z.toFixed(1)})`)
  }
  note(badFurniture.length === 0,
    `every desk/console/podium inside the room${badFurniture.length ? ` — ${badFurniture}` : ''}`)

  // ── 3. two agents never occupy the same square metre ────────────────────
  const tight = []
  for (let i = 0; i < seats.length; i++) {
    for (let j = i + 1; j < seats.length; j++) {
      const a = seats[i], b = seats[j]
      const dx = a.position[0] - b.position[0]
      const dz = a.position[2] - b.position[2]
      const d = Math.hypot(dx, dz)
      if (d < MIN_GAP) tight.push(`${a.id} ↔ ${b.id} = ${d.toFixed(2)} m`)
    }
  }
  note(tight.length === 0, `no two agents closer than ${MIN_GAP} m${tight.length ? ` — ${tight.slice(0, 6)}` : ''}`)

  // ── 4. nobody stands inside somebody else's furniture ───────────────────
  const boxes = seats.filter((s) => s.furniture).map((s) => {
    const kind = s.furniture.kind
    const off = kind === 'console' ? 1.0 : kind === 'podium' ? 1.15 : 0.74
    const cx = s.position[0] + Math.sin(s.yaw) * off
    const cz = s.position[2] + Math.cos(s.yaw) * off
    const halfW = kind === 'console' ? 0.85 : kind === 'podium' ? 0.5 : 0.9
    const halfD = kind === 'console' ? 0.5 : kind === 'podium' ? 0.35 : 0.42
    // the desk is rotated with the agent: its local x runs across the yaw
    return { id: s.id, cx, cz, halfW, halfD, yaw: s.yaw }
  })
  const collisions = []
  for (const b of boxes) {
    for (const s of seats) {
      if (s.id === b.id) continue
      // de-rotate the seat into the desk's frame
      const dx = s.position[0] - b.cx
      const dz = s.position[2] - b.cz
      const lx = dx * Math.cos(b.yaw) - dz * Math.sin(b.yaw)
      const lz = dx * Math.sin(b.yaw) + dz * Math.cos(b.yaw)
      if (Math.abs(lx) < b.halfW - 0.05 && Math.abs(lz) < b.halfD + 0.35) {
        collisions.push(`${s.id} inside ${b.id}'s ${'desk'}`)
      }
    }
  }
  note(collisions.length === 0,
    `no agent inside a colleague's workstation${collisions.length ? ` — ${collisions.slice(0, 6)}` : ''}`)

  // ── 5. the body actually reaches the floor ──────────────────────────────
  const standing = pelvis.stand - LEG_DROP
  const seated = pelvis.sit - LEG_DROP
  note(Math.abs(standing) < 0.06, `standing shoes touch the floor (${(standing * 1000).toFixed(0)} mm above)`)
  note(seated < 0, `seated knees fold under the desk (hip ${pelvis.sit.toFixed(2)} m, legs ${LEG_DROP.toFixed(3)} m)`)

  // ── 6. every station of the workflow is manned ──────────────────────────
  const byStation = {}
  for (const s of seats) byStation[s.station] = (byStation[s.station] || 0) + 1
  for (const st of stations) {
    note((byStation[st.key] || 0) > 0, `${st.label.padEnd(18)} manned (${byStation[st.key] || 0})`)
  }

  // ── 7. the room can hold all of it ─────────────────────────────────────
  const area = (room.halfX * 2) * (room.halfZ * 2)
  note(area / seats.length > 30, `floor area per agent: ${(area / seats.length).toFixed(1)} m²`)
  note(room.height >= 4, `ceiling ${room.height} m — the room reads as a hall, not a corridor`)
} catch (err) {
  console.error(`✗ audit could not run: ${err.message}`)
  problems.push(err.message)
} finally {
  rmSync(entryFile, { force: true })
  rmSync(outFile, { force: true })
}

console.log(problems.length
  ? `\n✗ ${problems.length} problem(s) in the headquarters plan`
  : '\n✓ the headquarters plan holds up against the live roster')
process.exit(problems.length ? 1 : 0)
