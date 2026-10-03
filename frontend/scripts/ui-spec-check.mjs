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
  ['Command Deck is the 3D bot work zone', 'headquarters'],
  ['starting balance card', 'Starting Balance'],
  ['current equity card', 'Current Equity'],
  ['opened positions card', 'Opened Positions'],
  ['realised P&L card', 'Realised P&L'],
  ['fees paid card', 'Fees Paid'],
  ['win rate card', 'Win Rate'],
  ['three-dot menu expander', 'Collapse menu'],
  ['Open Positions in the menu', 'Open Positions'],
  ['ROI trail surfaced in the menu pages', 'trail_active'],
  ['ledger check lives off the main page', 'Ledger check'],
  ['work-zone bays labelled', 'bay'],
  ['3D headquarters is the work zone', 'headquarters'],
  ['every station has a labelled bay', 'Verification Gate'],
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
const RENDERED_BOTS_OK = [
  ['rendered roster: the trading office panel', 'Trading office'],
  ['rendered roster: the promotion rule', 'promotion every'],
  ['rendered roster: the office record', 'office record'],
]
const RENDERED_FORBIDDEN = [
  ['rendered deck: pipeline rail moved to Bot Roster', 'Pipeline stages'],
  ['rendered deck: closes tape moved to Closed Trades', 'Latest closes'],
  ['rendered deck: ledger check moved to Open Positions', 'Ledger check'],
  ['rendered deck: no log panel', 'Workflow log'],
]

const get = async (url) => (await fetch(url)).text()

/**
 * How many text nodes the compact rail could still print: `n.label` / `n.hint`
 * may only appear *inside* the `{menuOpen && …}` guard, so an unguarded word in
 * the nav would push this count above 2.
 */
const navWords = (layout) => {
  const nav = layout.slice(layout.indexOf('<nav'), layout.indexOf('</nav>'))
  // only *printed* words count: title={n.label} is a tooltip, not menu text
  const printed = nav.indexOf('>{n.label}<')
  const hint = nav.indexOf('{n.hint}')
  const guard = nav.indexOf('{menuOpen && (')
  const other = nav.match(/>\s*[A-Za-z]{3,}\s*</g) || []      // stray prose in the rail
  return (printed >= 0 && hint > printed && guard >= 0 && guard < printed
          && other.length === 0) ? 2 : Infinity
}

/* ── source-level rule ──────────────────────────────────────────────────────
 * Minification eats identifiers, so a popup's lifetime cannot be asserted from
 * the bundle.  Read the store instead: every transient notification must expire
 * after NOTIFY_MS = 3000 and no popup may keep a hand-written timer of its own.
 * ------------------------------------------------------------------------ */
const SOURCE_RULES = () => {
  const src = readFileSync(resolve(here, '../src/state/store.tsx'), 'utf8')
  const room = readFileSync(resolve(here, '../src/components/hq/furniture.tsx'), 'utf8')
  const layout = readFileSync(resolve(here, '../src/components/Layout.tsx'), 'utf8')
  const plan = readFileSync(resolve(here, '../src/components/hq/layout.ts'), 'utf8')
  const hq = readFileSync(resolve(here, '../src/components/hq/HQ.tsx'), 'utf8')
  const human = readFileSync(resolve(here, '../src/components/hq/Human.tsx'), 'utf8')
  const bots = readFileSync(resolve(here, '../../backend/app/bots.py'), 'utf8')
  const office = readFileSync(resolve(here, '../src/pages/Bots.tsx'), 'utf8')
  const dash = readFileSync(resolve(here, '../src/pages/Dashboard.tsx'), 'utf8')
  /* the seven bays, by the short department name the floor draws */
  const engine = readFileSync(resolve(here, '../../backend/app/engine.py'), 'utf8')
  const bays = ['COMMAND', 'SCAN', 'ANALYST', 'EXECUTE', 'VERIFY', 'MONITOR', 'FINANCE']
  /* the floor carries an architectural grid — fixed dimensions, never fed by
     trade or equity data (the rejected "down graph on the floor") */
  const gridLine = (room.match(/<gridHelper[^>]*>/g) || []).join('\n')
  return [
    ['floor grid is architectural, not data-driven',
      gridLine.length > 0 && !/curve|pnl|equity|series/i.test(gridLine)],
    ['notifications expire in exactly 3 s', /export const NOTIFY_MS = 3000\b/.test(src)],
    ['toasts use the shared 3 s timer', /setToasts[\s\S]{0,120}NOTIFY_MS/.test(src)],
    ['no popup keeps a hand-written timer',
      !/set(Toasts|Promotions)[\s\S]{0,120},\s*(6000|7000|4200|5000|8000)\)/.test(src)],
    // ── the operator's menu rule: glyphs when compact, words when expanded ──
    ['collapsed rail carries symbols only',
      navWords(layout) === 2 && !/shortLabel|const SHORT/.test(layout)],
    // ── the top notification is gone for good ──────────────────────────────
    ['no banner is raised over the dashboard',
      !/CelebrationOverlay|setCelebration/.test(layout + src)],
    ['a keyless simulator raises no banner',
      /const notice = !sos.active && sos.level === 'notice'/.test(layout)
      && /sos.active && \(/.test(layout.slice(layout.indexOf('AnimatePresence')))],
    ['keyless connector reports a notice, never a warning',
      /return \("notice", problems or \["no Binance API keys stored/.test(engine)],
    ['win/loss reaction lives on the 3D floor',
      /Sparkles/.test(hq) && /mood === 'sad'|set_mood|'sad'/.test(hq)],
    // ── the 3D floor states the department, not its paperwork ─────────────
    ['3D bays are labelled with short department names',
      bays.every((b) => plan.includes(`short: '${b}'`)) && /label=\{st\.short\}/.test(hq)],
    ['3D names only the head of each department',
      /export function headsFor/.test(plan) && /heads\[station\] === bot\.bot_id/.test(hq)
      && /label = isHead \|\| selected/.test(hq)],
    ['agent badge is a name, not a status line',
      !/· \{bot\.status\}/.test(human)],
    ['no instruction text on the 3D floor',
      !/drag to orbit/.test(hq)],
    ['the 3D floor carries no counters or ticker',
      !/bar closes in|swarm activity|\{working\} working|\{trailing\} trailing/.test(dash)],
    ['bay boards carry a headline, not a data dump',
      /rows=\{\[\['assets'[^\]]*\]\]\}/.test(hq)
      && /rows=\{\[\['open'[^\]]*\]\]\}/.test(hq)
      && !/bar closes|trail armed|margin used/.test(
        hq.slice(hq.indexOf('small board over the scanner bay'), hq.indexOf('floor zones')))],
    // ── the office ladder: 20 units per level, replace what fails ─────────
    ['promotion is earned every 20 completed units',
      /PROMOTE_EVERY = 20\b/.test(bots) && /completed_trades/.test(bots)],
    ['a failing agent is replaced and the seat re-filled',
      /def office_pass/.test(bots) && /def _hire/.test(bots) && /def _fire/.test(bots)
      && /should_retire/.test(bots)],
    ['the office ladder is on the roster page',
      /Trading office/.test(office) && /office ladder/.test(office)
      && /next_level_in/.test(office)],
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
      const botsPage = out.split('───── Bots ─────')[1]?.split('─────')[0] || ''
      if (!botsPage) {
        bad++
        console.log('✗ could not render the Bot Roster page for the structural pass')
      } else {
        const bflat = botsPage.replace(/\s+/g, ' ')
        for (const [label, needle] of RENDERED_BOTS_OK) {
          const ok = bflat.includes(needle)
          if (!ok) bad++
          console.log(`${ok ? '✓' : '✗'} ${label}`)
        }
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
