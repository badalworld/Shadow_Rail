/**
 * API access token (only needed when the server was started with
 * SHADOW_RAIL_API_TOKEN).  Stored in localStorage so a reload keeps it; the
 * request layer adds it to every call and the websocket passes it in the query
 * string (browsers cannot set headers on a websocket handshake).
 */
const KEY = 'shadowrail.token'

export const getToken = (): string => {
  try { return localStorage.getItem(KEY) || '' } catch { return '' }
}

export const setToken = (token: string): void => {
  try {
    const t = token.trim()
    if (t) localStorage.setItem(KEY, t)
    else localStorage.removeItem(KEY)
  } catch { /* private mode — the session still works until reload */ }
}

export const authHeaders = (): Record<string, string> => {
  const t = getToken()
  return t ? { 'X-Api-Token': t } : {}
}
