/*
 * Server-side render smoke test.
 *
 * Renders every page of the dashboard against *real* API fixtures captured from a
 * running engine, so a crash from a missing or renamed backend field is caught
 * without needing a browser.
 *
 *   node frontend/scripts/ssr-smoke.mjs capture   # refresh fixtures from :8080
 *   node frontend/scripts/ssr-smoke.mjs           # render every page
 */
import { build } from 'esbuild'
import { readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const fixturePath = resolve(here, 'fixtures.json')
const entryFile = resolve(root, 'src/__smoke.tsx')
const stubFile = resolve(here, '.stub-3d.tsx')
const outFile = resolve(here, '.smoke-bundle.cjs')
const API = process.env.SMOKE_API || 'http://localhost:8080'

/* ───────────────────────── capture ───────────────────────── */

async function capture() {
  const paths = [
    '/api/status', '/api/health', '/api/config', '/api/ip', '/api/equity',
    '/api/equity/curve?limit=400', '/api/stats', '/api/trades/open',
    '/api/trades/closed?limit=60', '/api/scan', '/api/bots', '/api/logs?limit=120',
    '/api/about', '/api/events', '/api/indicator/selftest',
  ]
  const out = {}
  for (const p of paths) {
    try {
      out[p] = await (await fetch(API + p)).json()
    } catch (err) {
      console.error(`! ${p}: ${err.message}`)
      out[p] = {}
    }
  }
  // grab the exact boot snapshot the server inlines into index.html
  try {
    const html = await (await fetch(API + '/')).text()
    const m = html.match(/window\.__SHADOW_RAIL_BOOT__=([\s\S]*?)<\/script>/)
    if (m) {
      out['__boot__'] = JSON.parse(m[1].replace(/<\\\//g, '</'))
      console.log('✓ captured server boot payload')
    }
  } catch (err) { console.error('! boot payload:', err.message) }

  // point the trade-detail fixture at a real id
  const id = out['/api/trades/closed?limit=60']?.trades?.[0]?.id
  if (id) out[`/api/trades/${id}`] = await (await fetch(`${API}/api/trades/${id}`)).json()
  writeFileSync(fixturePath, JSON.stringify(out, null, 2))
  console.log(`✓ captured ${Object.keys(out).length} fixtures from ${API}`)
}

/* ───────────────────────── render ───────────────────────── */

const ENTRY = `
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { StoreProvider } from './state/store'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { Positions } from './pages/Positions'
import { Scan } from './pages/Scan'
import { Trades } from './pages/Trades'
import { Bots } from './pages/Bots'
import { Logs } from './pages/Logs'
import { Settings } from './pages/Settings'
import { About } from './pages/About'

const PAGES: [string, React.ComponentType<any>, string][] = [
  ['Dashboard', Dashboard, 'dashboard'],
  ['Positions', Positions, 'positions'],
  ['Scan', Scan, 'scan'],
  ['Trades', Trades, 'trades'],
  ['Bots', Bots, 'bots'],
  ['Logs', Logs, 'logs'],
  ['Settings', Settings, 'settings'],
  ['About', About, 'about'],
]

export function run() {
  const results: Record<string, any> = {}
  for (const [name, Page, key] of PAGES) {
    try {
      const props = name === 'Dashboard' ? { goTo: () => {} } : {}
      const html = renderToStaticMarkup(
        React.createElement(StoreProvider, null,
          React.createElement(Layout, { page: key as any, setPage: () => {},
            children: React.createElement(Page, props) } as any)),
      )
      results[name] = { ok: true, length: html.length,
        text: html.replace(/<[^>]*>/g, ' ').replace(/&amp;/g, '&').replace(/&#x27;/g, "'")
                  .replace(/&quot;/g, '"').replace(/\\s+/g, ' ').trim(),
        raw: html,
        empty: html.replace(/<[^>]*>/g, '').trim().length < 40 }
    } catch (err: any) {
      results[name] = { ok: false, error: String(err && err.stack || err) }
    }
  }
  return results
}
`

async function render() {
  if (!existsSync(fixturePath)) {
    console.error('no fixtures — run: node frontend/scripts/ssr-smoke.mjs capture')
    process.exit(1)
  }
  const fixtures = JSON.parse(readFileSync(fixturePath, 'utf8'))

  // give the store the same boot snapshot the server inlines, so pages render
  // with real numbers instead of an empty first-paint
  globalThis.__SHADOW_RAIL_BOOT__ = fixtures['__boot__'] ?? {
    status: fixtures['/api/status']?.status,
    bots: fixtures['/api/status']?.bots,
    links: fixtures['/api/status']?.links,
    equity: fixtures['/api/equity'],
    stats: fixtures['/api/stats']?.stats,
    scan: fixtures['/api/scan'],
    open_trades: fixtures['/api/trades/open']?.trades,
    logs: fixtures['/api/logs?limit=120']?.logs,
  }

  writeFileSync(entryFile, ENTRY)
  // The stub replaces three/@react-three/* for the node render.  It has to
  // export every symbol the 3D components import, and it must not need a GPU:
  // the HQ checks for a real canvas and renders a text placeholder without one.
  const STUB = `import React from 'react'
function Stub(props: any) { return React.createElement('div', { 'data-stub': '3d' }, props?.children ?? null) }
export default Stub
export const BotMap3D = Stub
export const Canvas = Stub
export const Html = Stub
export const Sparkles = Stub
export const ContactShadows = Stub
export const Environment = Stub
export const Lightformer = Stub
export const OrbitControls = Stub
export const MeshReflectorMaterial = Stub
export const SoftShadows = Stub
export const Stage = Stub
export const Text = Stub
export const Billboard = Stub
export const RoundedBox = Stub
export function useFrame() {}
export function useThree() { return {} }
export function useCursor() {}
export function useGLTF() { return { scene: null } }
export function resolveNode() { return [0, 0, 0] }
export function useNodePositions() { return [] }
`
  writeFileSync(stubFile, STUB)

  await build({
    entryPoints: [entryFile],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    outfile: outFile,
    jsx: 'automatic',
    logLevel: 'error',
    define: { 'process.env.NODE_ENV': '"development"' },
    plugins: [{
      name: 'stub-3d',
      setup(b) {
        // WebGL/canvas cannot run in node — swap the 3D swarm for a placeholder
        b.onResolve({ filter: /BotMap3D|^three$|^@react-three/ }, () => ({ path: stubFile }))
      },
    }],
  })

  class FakeElement { constructor() { this.style = {}; this.dataset = {} } addEventListener() {} removeEventListener() {} appendChild() {} setAttribute() {} }
  globalThis.HTMLElement = FakeElement
  globalThis.SVGElement = FakeElement
  globalThis.Element = FakeElement
  globalThis.Node = FakeElement
  globalThis.window = globalThis
  globalThis.document = {
    documentElement: { dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {} } },
    addEventListener() {}, removeEventListener() {}, querySelector: () => null,
  }
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
  globalThis.MutationObserver = class { observe() {} disconnect() {} }
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} }
  globalThis.WebSocket = class { constructor() {} close() {} send() {} addEventListener() {} }
  globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 16)
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id)
  globalThis.addEventListener = () => {}
  globalThis.removeEventListener = () => {}
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' })
  globalThis.location = { href: 'http://localhost:8080/', origin: 'http://localhost:8080', protocol: 'http:' }

  globalThis.fetch = async (url) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, '')
    const body = fixtures[path] ?? fixtures[path.split('?')[0]]
    if (!body) { console.error(`   (no fixture for ${path})`); return { ok: true, status: 200, json: async () => ({}) } }
    return { ok: true, status: 200, json: async () => body }
  }

  const { run } = await import(outFile)
  const results = run()
  let failed = 0
  for (const [name, r] of Object.entries(results)) {
    if (r.ok) {
      console.log(`  ✓ ${name.padEnd(9)} ${String(r.length).padStart(6)} chars${r.empty ? '  ⚠ suspiciously empty' : ''}`)
      if (process.env.SMOKE_DUMP) console.log(`\n───── ${name} ─────\n${r.text.slice(0, 2600)}\n`)
    }
    else { failed++; console.error(`  ✗ ${name}\n${r.error}\n`) }
  }
  if (process.env.SMOKE_HTML) writeFileSync(process.env.SMOKE_HTML, results.Dashboard?.raw || '')
  rmSync(entryFile, { force: true })
  rmSync(outFile, { force: true })
  console.log(failed ? `\n${failed} page(s) failed` : '\n✓ all pages rendered')
  process.exit(failed ? 1 : 0)
}

if (process.argv[2] === 'capture') await capture()
else await render()
