/**
 * KKline 交易 API 客户端
 * 通过 KKline 的 REST API 执行交易、查看持仓和余额。
 * 认证方式：X-Opus-Key header
 */

const KKLINE_BASE = process.env.NEXT_PUBLIC_KKLINE_URL || 'https://kk.kline007.top';
const OPUS_KEY = process.env.NEXT_PUBLIC_OPUS_KEY || '';

function headers(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-Opus-Key': OPUS_KEY,
  };
}

// ── 交易操作 ──

export interface OpenTradeParams {
  symbol: string;
  side: 'LONG' | 'SHORT';
  amount_usdt: number;
  leverage?: number;
  stop_loss_pct?: number;
}

export interface TradeResult {
  success: boolean;
  trade_id?: string;
  message?: string;
  entry_price?: number;
  quantity?: number;
  error?: string;
}

export async function openTrade(params: OpenTradeParams): Promise<TradeResult> {
  const res = await fetch(`${KKLINE_BASE}/api/trade/open`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(params),
  });
  return res.json();
}

export interface CloseTradeParams {
  symbol: string;
  ratio?: number;     // 0.0-1.0, 默认 1.0 全部平仓
  reason?: string;
}

export async function closeTrade(params: CloseTradeParams): Promise<TradeResult> {
  const res = await fetch(`${KKLINE_BASE}/api/trade/close`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify(params),
  });
  return res.json();
}

// ── 持仓查询 ──

export interface Position {
  symbol: string;
  side: string;
  entry_price: number;
  mark_price: number;
  quantity: number;
  notional: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  leverage: number;
  margin: number;
}

export async function fetchPositions(): Promise<Position[]> {
  const res = await fetch(`${KKLINE_BASE}/api/positions`, {
    headers: headers(),
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.positions || data || [];
}

// ── 余额查询 ──

export interface BalanceInfo {
  total_balance: number;
  available_balance: number;
  unrealized_pnl: number;
  margin_used: number;
  margin_ratio: number;
}

export async function fetchBalance(): Promise<BalanceInfo | null> {
  try {
    const res = await fetch(`${KKLINE_BASE}/api/balance`, {
      headers: headers(),
    });
    if (!res.ok) return null;
    const raw = await res.json();
    // KKline API 返回 {total, available, ...}，需要映射到前端字段名
    return {
      total_balance: raw.total_balance ?? raw.total ?? raw.margin_balance ?? 0,
      available_balance: raw.available_balance ?? raw.available ?? 0,
      unrealized_pnl: raw.unrealized_pnl ?? 0,
      margin_used: raw.margin_used ?? 0,
      margin_ratio: raw.margin_ratio ?? 0,
    };
  } catch {
    return null;
  }
}

// ── 最近交易 ──

export interface RecentTrade {
  id: string;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price?: number;
  quantity: number;
  pnl?: number;
  pnl_pct?: number;
  status: string;
  opened_at: string;
  closed_at?: string;
}

export async function fetchRecentTrades(limit = 10): Promise<RecentTrade[]> {
  try {
    const res = await fetch(`${KKLINE_BASE}/api/trades?limit=${limit}`, {
      headers: headers(),
    });
    if (!res.ok) return [];
    const data = await res.json();
    return data.trades || data || [];
  } catch {
    return [];
  }
}

export { KKLINE_BASE };
