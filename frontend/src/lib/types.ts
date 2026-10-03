export type BotStatus =
  | 'offline' | 'idle' | 'working' | 'success' | 'error'
  | 'blocked' | 'sad' | 'celebrating' | 'halted'

export interface BotMetrics {
  tasks_done: number
  tasks_failed: number
  errors: number
  wins: number
  losses: number
  avg_latency_ms: number
  api_spent_window: number
  api_spent_total: number
  api_blocked: number
  score: number
}

export interface Bot {
  bot_id: string
  name: string
  role: string
  group: string
  sigil: string
  color: string
  slot: number
  status: BotStatus
  task: string
  message: string
  progress: number
  mood: 'neutral' | 'happy' | 'sad' | 'excited' | 'worried'
  rank: string
  rank_index: number
  promotions: number
  metrics: BotMetrics
  assigned: string[]
  last_active: number
}

export interface Link {
  from: string
  to: string
  label: string
}

export interface SosState {
  active: boolean
  level: 'none' | 'warning' | 'critical'
  reasons: string[]
  since: number
  checks: number
}

export interface ApiState {
  limit_per_min: number
  cap: number
  budget_pct: number
  used: number
  used_pct: number
  cap_used_pct: number
  available: number
  blocked_total: number
  halted: boolean
  retry_after: number
  per_bot: Record<string, { spent_window: number; spent_total: number; blocked: number }>
}

export interface WorkflowStage {
  status: string
  detail: string
  at: number
  count?: number
  total?: number
  approved?: number
}

export interface EngineStatus {
  running: boolean
  paused: boolean
  cycle: number
  mode: string
  transport: string
  universe: number
  open_trades: number
  max_trades: number
  sos: SosState
  health: Record<string, any>
  api: ApiState
  workflow: { cycle: number; stage: string; stages: Record<string, WorkflowStage>; updated_at: number }
  scanner_buckets: number[]
  links: Link[]
  risk: Record<string, any>
  last_scan_at: number
  started_at: number
}

export interface EquityState {
  starting_balance: number
  starting_locked: boolean
  starting_source: string
  starting_at: number
  equity: number
  balance: number
  available: number
  unrealized: number
  margin_used: number
  open_positions: number
  released_pnl: number
  fees_paid: number
  fees_paid_total?: number
  open_entry_fees?: number
  equity_bridge?: number
  funding_paid: number
  funding_net: number
  daily_pnl: number
  daily?: number
  day_start_equity: number
  peak_equity: number
  drawdown_pct: number
  growth_pct: number
  growth_abs: number
  net_after_costs: number
  margin_budget?: number
  open_slots?: number
  max_trades?: number
  stats?: Stats
}

export interface Reconcile {
  journal_net: number
  journal_fees: number
  open_entry_fees: number
  exchange_net: number | null
  exchange_fees?: number | null
  expected_from_journal: number
  net_drift: number
  balanced: boolean
  transport?: string
  rows?: number
  reason?: string
}

export interface Stats {
  total_trades: number
  rated_trades?: number
  unreconciled?: number
  wins: number
  losses: number
  win_rate: number
  net_pnl: number
  gross_profit: number
  gross_loss: number
  profit_factor: number
  avg_win: number
  avg_loss: number
  fees_paid: number
  fees_paid_total?: number
  open_entry_fees?: number
  equity_bridge?: number
  funding_paid: number
  funding_net: number
  best_trade: number
  best_symbol: string
  worst_trade: number
  worst_symbol: string
}

export interface Trade {
  id: number
  /** ROI on margin (leverage-adjusted) and the ROI trail state */
  roi_pct?: number
  peak_roi_pct?: number
  stop_roi_pct?: number
  trail_enabled?: boolean
  trail_active?: boolean
  trail_stop?: number
  trail_activation_roi_pct?: number
  trail_distance_roi_pct?: number
  symbol: string
  side: 'LONG' | 'SHORT'
  status: string
  qty: number
  entry_price: number
  exit_price?: number
  leverage: number
  margin: number
  notional: number
  sl_price: number
  tp_price: number
  liquidation_price: number
  opened_at: number
  closed_at?: number
  close_reason?: string
  pnl_source?: 'fills' | 'estimated' | 'unknown'
  gross_pnl: number
  fee_paid: number
  funding_paid: number
  net_pnl: number
  r_multiple?: number
  signal_confidence?: number
  signal_tier?: string
  analyst_id?: string
  scanner_id?: string
  exec_bot_id?: string
  monitor_bot_id?: string
  mode?: string
  mark?: number
  unrealized?: number
  unrealized_pct?: number
  liquidation_live?: number
  monitor_id?: string
}

export interface LogRow {
  id?: number
  ts: number
  level: string
  bot_id: string
  topic?: string
  message: string
  payload?: any
}

export interface ScanRow {
  symbol: string
  price: number
  trend: string
  trend_side: number
  quality: number | null
  rail: number | null
  rail_distance_pct: number | null
  atr_pct: number | null
  htf_bull: boolean
  ghost_close: number | null
  flip: 'LONG' | 'SHORT' | null
  tier: string | null
  has_position: boolean
  bar_time: number
}

export interface Opportunity {
  symbol: string
  direction: 'LONG' | 'SHORT'
  confidence: number
  approved: boolean
  analyst_id: string
  factors: Record<string, number>
  notes: string[]
  model: string
  reason: string
  entry: number
  stop: number
  target: number
  atr: number
  atr_pct: number
  trend_quality: number
  tier: string
  bar_time: number
}

export interface WsEvent {
  topic: string
  ts: number
  data: any
}
