/**
 * FlowEdge API 客户端
 * 封装所有后端通信，支持 REST 查询和 SSE 实时订阅。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8005';

// ── REST API ──

export async function fetchDashboard() {
  const res = await fetch(`${API_BASE}/dashboard`);
  if (!res.ok) throw new Error(`Dashboard fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchSignals() {
  const res = await fetch(`${API_BASE}/signals`);
  if (!res.ok) throw new Error(`Signals fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchSignal(symbol: string) {
  const res = await fetch(`${API_BASE}/signals/${symbol}`);
  if (!res.ok) throw new Error(`Signal fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchFeatures(symbol?: string) {
  const url = symbol
    ? `${API_BASE}/features/snapshot?symbol=${symbol}`
    : `${API_BASE}/features/snapshot`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Features fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchSignalHistory(symbol: string, limit = 100) {
  const res = await fetch(`${API_BASE}/signals/history/${symbol}?limit=${limit}`);
  if (!res.ok) throw new Error(`History fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchPerformance(symbol?: string) {
  const url = symbol
    ? `${API_BASE}/signals/performance?symbol=${symbol}`
    : `${API_BASE}/signals/performance`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Performance fetch failed: ${res.status}`);
  return res.json();
}

export async function fetchHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/status`);
  if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`);
  return res.json();
}

// ── SSE 实时流 ──

export function subscribeFeatures(
  onData: (data: Record<string, unknown>) => void,
  symbol?: string,
): () => void {
  const url = symbol
    ? `${API_BASE}/features/stream?symbol=${symbol}`
    : `${API_BASE}/features/stream`;
  const es = new EventSource(url);

  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onData(data);
    } catch {
      /* 忽略解析错误 */
    }
  };

  es.onerror = () => {
    /* EventSource 会自动重连 */
  };

  return () => es.close();
}

export function subscribeSignals(
  onData: (data: Record<string, unknown>) => void,
): () => void {
  const es = new EventSource(`${API_BASE}/signals/stream/all`);

  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onData(data);
    } catch {
      /* 忽略解析错误 */
    }
  };

  return () => es.close();
}

export { API_BASE };
