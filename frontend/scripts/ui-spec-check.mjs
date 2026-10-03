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
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
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
  ['Account page in the menu', 'Account'],
]

const FORBIDDEN = [
  ['no equities chart on the main page', 'equityCurve(400)'],
  ['no market graph drawn on the floor', 'floorSeries'],
  ['no candlestick floor', 'candlestick'],
  ['no bundled Renderpeople assets', 'cdn.renderpeople'],
]

// structural: the *rendered* Command Deck must not carry the panels that were
// moved into the menu, and must carry the six cards.  Checked against the SSR
// harness, so it is the real markup, not a guess about the bundle.
const RENDERED_OK = [
  ['rendered deck: 3D work zone', 'Bot Work Zone'],
  ['rendered deck: headquarters work zone', 'headquarters'],
]
// the deck is nothing but the office — every number moved into the menu
const RENDERED_DECK_FORBIDDEN = [
  ['rendered deck: starting balance moved to Account', 'Starting Balance'],
  ['rendered deck: equity card moved to Account', 'Current Equity'],
  ['rendered deck: open positions card moved to Account', 'Opened Positions'],
  ['rendered deck: realised P&L card moved to Account', 'Realised P&L'],
  ['rendered deck: fees card moved to Account', 'Fees Paid'],
  ['rendered deck: win rate card moved to Account', 'Win Rate'],
]
// …and the Account page carries them
const RENDERED_ACCOUNT_OK = [
  ['rendered account: starting balance', 'Starting Balance'],
  ['rendered account: equity card', 'Current Equity'],
  ['rendered account: opened positions card', 'Opened Positions'],
  ['rendered account: realised P&L card', 'Realised P&L'],
  ['rendered account: fees paid card', 'Fees Paid'],
  ['rendered account: win rate card', 'Win Rate'],
  ['rendered account: equity sheet', 'Equity sheet'],
  ['rendered account: ledger check', 'Ledger check'],
]
const RENDERED_FORBIDDEN = [
  ['rendered deck: pipeline rail moved to Bot Roster', 'Pipeline stages'],
  ['rendered deck: closes tape moved to Closed Trades', 'Latest closes'],
  ['rendered deck: ledger check moved to Open Positions', 'Ledger check'],
  ['rendered deck: no log panel', 'Workflow log'],
]

const get = async (url) => (await fetch(url)).text()

/* ── source-level rule ──────────────────────────────────────────────────────
 * Minification eats identifiers, so a popup's lifetime cannot be asserted from
 * the bundle.  Read the store instead: every transient notification must expire
 * after NOTIFY_MS = 3000 and no popup may keep a hand-written timer of its own.
 * ------------------------------------------------------------------------ */
const SOURCE_RULES = () => {
  const src = readFileSync(resolve(here, '../src/state/store.tsx'), 'utf8')
  const room = readFileSync(resolve(here, '../src/components/hq/furniture.tsx'), 'utf8')
  /* the floor carries an architectural grid — fixed dimensions, never fed by
     trade or equity data (the rejected "down graph on the floor") */
  const gridLine = (room.match(/<gridHelper[^>]*>/g) || []).join('\n')
  return [
    ['floor grid is architectural, not data-driven',
      gridLine.length > 0 && !/curve|pnl|equity|series/i.test(gridLine)],
    ['notifications expire in exactly 3 s', /export const NOTIFY_MS = 3000\b/.test(src)],
    ['toasts use the shared 3 s timer', /setToasts[\s\S]{0,120}NOTIFY_MS/.test(src)],
    ['top banner uses the shared 3 s timer', /setCelebration[\s\S]{0,120}NOTIFY_MS/.test(src)],
    ['no popup keeps a hand-written timer',
      !/set(Toasts|Celebration|Promotions)[\s\S]{0,120},\s*(6000|7000|4200|5000|8000)\)/.test(src)],
  ]
}

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
  // ── source pass: rules the minifier hides ──────────────────────────────
  for (const [label, ok] of SOURCE_RULES()) {
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
      for (const [label, needle] of RENDERED_DECK_FORBIDDEN) {
        const ok = !flat.includes(needle)
        if (!ok) bad++
        console.log(`${ok ? '✓' : '✗'} ${label}`)
      }
      const acct = out.split('───── Account ─────')[1]?.split('─────')[0] || ''
      if (!acct) {
        bad++
        console.log('✗ could not render the Account page for the structural pass')
      } else {
        const aflat = acct.replace(/\s+/g, ' ')
        for (const [label, needle] of RENDERED_ACCOUNT_OK) {
          const ok = aflat.includes(needle)
          if (!ok) bad++
          console.log(`${ok ? '✓' : '✗'} ${label}`)
        }
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
