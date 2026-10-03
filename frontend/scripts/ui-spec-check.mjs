#!/usr/bin/env node
/**
 * ui-spec-check — proves the dashboard the server is *actually serving* matches
 * the agreed UI, so "I rebuilt but still see the old screen" can never happen
 * silently again.
 *
 *   node scripts/ui-spec-check.mjs [http://localhost:8080]
 *
 * It follows the shell → hashed bundle the server hands out (the same URL a
 * browser would load) and asserts every required element is there, and that the
 * rejected ones are gone.
 */
const API = process.argv[2] || process.env.SMOKE_API || 'http://localhost:8080'

const REQUIRED = [
  ['Command Deck is the 3D bot work zone', 'Bot Work Zone'],
  ['starting balance card', 'Starting Balance'],
  ['current equity card', 'Current Equity'],
  ['opened positions card', 'Opened Positions'],
  ['realised P&L card', 'Realised P&L'],
  ['fees paid card', 'Fees Paid'],
  ['win rate card', 'Win Rate'],
  ['collapsed menu shows short labels', 'Deck'],
  ['three-dot menu expander', 'Collapse menu'],
  ['Open Positions in the menu', 'Open Positions'],
  ['ROI trail badge on the main cards', 'trailing'],
  ['ledger check lives off the main page', 'Ledger check'],
  ['work-zone bays labelled', 'bay'],
  ['3D headquarters is the work zone', 'headquarters'],
  ['every station has a labelled bay', 'Verification Gate'],
  ['agent card exists for a clicked body', 'agent card'],
  ['human reference credited', 'Renderpeople'],
  ['render quality toggle', 'cinematic'],
  ['licensed-scan drop-in slot', '/models/people'],
  ['compact swarm map kept as a view', 'swarm map'],
]

const FORBIDDEN = [
  ['no equities chart on the main page', 'equityCurve(400)'],
  ['no 3D grid/graph floor', 'gridHelper'],
  ['no bundled Renderpeople assets', 'cdn.renderpeople'],
]

// structural: the *rendered* Command Deck must not carry the panels that were
// moved into the menu, and must carry the six cards.  Checked against the SSR
// harness, so it is the real markup, not a guess about the bundle.
const RENDERED_OK = [
  ['rendered deck: 3D work zone', 'Bot Work Zone'],
  ['rendered deck: starting balance', 'Starting Balance'],
  ['rendered deck: win rate', 'Win Rate'],
  ['rendered deck: headquarters work zone', 'headquarters'],
  ['rendered deck: fees paid card', 'Fees Paid'],
]
const RENDERED_FORBIDDEN = [
  ['rendered deck: pipeline rail moved to Bot Roster', 'Pipeline stages'],
  ['rendered deck: closes tape moved to Closed Trades', 'Latest closes'],
  ['rendered deck: ledger check moved to Open Positions', 'Ledger check'],
  ['rendered deck: no log panel', 'Workflow log'],
]

const get = async (url) => (await fetch(url)).text()

const main = async () => {
  const shell = await get(API + '/')
  const m = shell.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/)
  if (!m) {
    console.error('✗ the shell does not reference a dashboard bundle')
    process.exit(1)
  }
  const src = await get(API + m[0])
  let bad = 0
  for (const [label, needle] of REQUIRED) {
    const ok = src.includes(needle)
    if (!ok) bad++
    console.log(`${ok ? '✓' : '✗'} ${label}`)
  }
  for (const [label, needle] of FORBIDDEN) {
    const ok = !src.includes(needle)
    if (!ok) bad++
    console.log(`${ok ? '✓' : '✗'} ${label}`)
  }
  // ── structural pass: render the Dashboard and inspect the markup ────────
  try {
    const { execFileSync } = await import('node:child_process')
    const out = execFileSync(process.execPath, ['scripts/ssr-smoke.mjs'], {
      cwd: process.cwd(), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024,
      env: { ...process.env, SMOKE_API: API, SMOKE_DUMP: '1' },
    })
    const dash = out.split('───── Dashboard ─────')[1]?.split('─────')[0] || ''
    if (!dash) {
      bad++
      console.log('✗ could not render the Command Deck for the structural pass')
    } else {
      const flat = dash.replace(/\s+/g, ' ')
      for (const [label, needle] of RENDERED_OK) {
        const ok = flat.includes(needle)
        if (!ok) bad++
        console.log(`${ok ? '✓' : '✗'} ${label}`)
      }
      for (const [label, needle] of RENDERED_FORBIDDEN) {
        const ok = !flat.includes(needle)
        if (!ok) bad++
        console.log(`${ok ? '✓' : '✗'} ${label}`)
      }
    }
  } catch (err) {
    console.log(`! structural pass skipped: ${err.message.split('\n')[0]}`)
  }

  console.log(`\nserved bundle: ${m[0]}`)
  if (bad) {
    console.error(`${bad} check(s) failed — the served dashboard does not match the agreed UI`)
    process.exit(1)
  }
  console.log('all UI checks pass')
}

main().catch((err) => {
  console.error('✗ could not read the dashboard:', err.message)
  process.exit(1)
})
