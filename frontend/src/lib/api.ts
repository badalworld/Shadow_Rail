import { authHeaders, getToken } from './token'

/** Typed REST client. Everything is same-origin, so no CORS juggling. */
export class Unauthorised extends Error {
  constructor() { super('API token required') }
}

/** Fired when the server refuses a call for lack of a valid token. */
export const AUTH_EVENT = 'shadowrail:unauthorised'

const json = async (res: Response) => {
  if (res.status === 401) {
    window.dispatchEvent(new Event(AUTH_EVENT))
    throw new Unauthorised()
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail || body.error || detail
    } catch { /* ignore */ }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.json()
}

export const api = {
  get: <T = any>(path: string): Promise<T> =>
    fetch(path, { headers: authHeaders() }).then(json),
  post: <T = any>(path: string, body?: any): Promise<T> =>
    fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(json),
  put: <T = any>(path: string, body?: any): Promise<T> =>
    fetch(path, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body ?? {}),
    }).then(json),
}

export const endpoints = {
  status: () => api.get('/api/status'),
  health: () => api.get('/api/health'),
  probe: () => api.post('/api/health/probe'),
  config: () => api.get('/api/config'),
  saveConfig: (patch: any) => api.put('/api/config', patch),
  testConnection: (body: any) => api.post('/api/config/test-connection', body),
  ipInfo: () => api.get('/api/ip'),
  verifyIp: (body: any = {}) => api.post('/api/config/verify-ip', body),
  equity: () => api.get('/api/equity'),
  equityCurve: (limit = 1200) => api.get(`/api/equity/curve?limit=${limit}`),
  stats: () => api.get('/api/stats'),
  reconcile: () => api.get('/api/reconcile'),
  openTrades: () => api.get('/api/trades/open'),
  closedTrades: (q: Record<string, string | number> = {}) => {
    const params = new URLSearchParams(
      Object.entries(q).filter(([, v]) => v !== '' && v !== undefined && v !== null)
        .map(([k, v]) => [k, String(v)] as [string, string]),
    )
    return api.get(`/api/trades/closed?${params.toString()}`)
  },
  trade: (id: number) => api.get(`/api/trades/${id}`),
  scan: () => api.get('/api/scan'),
  runCycle: () => api.post('/api/scan/run'),
  bots: () => api.get('/api/bots'),
  office: () => api.get('/api/office'),
  bot: (id: string) => api.get(`/api/bots/${id}`),
  promote: (id: string) => api.post(`/api/bots/${id}/promote`),
  logs: (q: Record<string, string | number> = {}) => {
    const params = new URLSearchParams(
      Object.entries(q).filter(([, v]) => v !== '' && v !== undefined && v !== null)
        .map(([k, v]) => [k, String(v)] as [string, string]),
    )
    return api.get(`/api/logs?${params.toString()}`)
  },
  engineStart: () => api.post('/api/engine/start'),
  engineStop: () => api.post('/api/engine/stop'),
  enginePause: (paused: boolean) => api.post(`/api/engine/pause?paused=${paused}`),
  emergencyClose: () => api.post('/api/engine/emergency-close?confirm=FLATTEN'),
  indicatorTest: () => api.get('/api/indicator/selftest'),
}

export function wsUrl(): string {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = getToken()
  const query = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${proto}//${location.host}/ws${query}`
}
