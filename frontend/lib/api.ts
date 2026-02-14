/**
 * FlowEdge API 客户端
 * 封装所有后端通信，支持 REST 查询和 SSE 实时订阅。
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8005';

// ── REST API ──

export async function fetchDashboard() {
  const res = await fetch(`${API_BASE}/dashboard`);
  if (!res.ok) throw new Error(`仪表盘请求失败: ${res.status}`);
  return res.json();
}

export async function fetchSignals() {
  const res = await fetch(`${API_BASE}/signals`);
  if (!res.ok) throw new Error(`信号列表请求失败: ${res.status}`);
  return res.json();
}

export async function fetchSignal(symbol: string) {
  const res = await fetch(`${API_BASE}/signals/${symbol}`);
  if (!res.ok) throw new Error(`信号详情请求失败: ${res.status}`);
  return res.json();
}

export async function fetchFeatures(symbol?: string) {
  const url = symbol
    ? `${API_BASE}/features/snapshot?symbol=${symbol}`
    : `${API_BASE}/features/snapshot`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`特征快照请求失败: ${res.status}`);
  return res.json();
}

export async function fetchSignalHistory(symbol: string, limit = 100) {
  const res = await fetch(`${API_BASE}/signals/history/${symbol}?limit=${limit}`);
  if (!res.ok) throw new Error(`信号历史请求失败: ${res.status}`);
  return res.json();
}

export async function fetchPerformance(symbol?: string) {
  const url = symbol
    ? `${API_BASE}/signals/performance?symbol=${symbol}`
    : `${API_BASE}/signals/performance`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`胜率统计请求失败: ${res.status}`);
  return res.json();
}

export async function fetchHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error(`健康检查失败: ${res.status}`);
  return res.json();
}

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/status`);
  if (!res.ok) throw new Error(`系统状态请求失败: ${res.status}`);
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

// ── 纸盘交易 API ──

export async function fetchPaperStatus() {
  const res = await fetch(`${API_BASE}/paper/status`);
  if (!res.ok) throw new Error(`纸盘状态请求失败: ${res.status}`);
  return res.json();
}

export async function fetchPaperTrades(limit = 50) {
  const res = await fetch(`${API_BASE}/paper/trades?limit=${limit}`);
  if (!res.ok) throw new Error(`纸盘交易记录请求失败: ${res.status}`);
  return res.json();
}

export async function fetchPaperEquity(limit = 1440) {
  const res = await fetch(`${API_BASE}/paper/equity?limit=${limit}`);
  if (!res.ok) throw new Error(`纸盘净值曲线请求失败: ${res.status}`);
  return res.json();
}

export async function updatePaperConfig(updates: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/paper/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error(`纸盘配置更新失败: ${res.status}`);
  return res.json();
}

export async function resetPaperAccount() {
  const res = await fetch(`${API_BASE}/paper/reset`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`纸盘账户重置失败: ${res.status}`);
  return res.json();
}

export async function fetchPaperSignalLog(limit = 50) {
  const res = await fetch(`${API_BASE}/paper/signal-log?limit=${limit}`);
  if (!res.ok) throw new Error(`纸盘信号日志请求失败: ${res.status}`);
  return res.json();
}

// ── 订单流可视化 API ──

export async function fetchOrderflow(symbol: string) {
  const res = await fetch(`${API_BASE}/orderflow/${symbol}`);
  if (!res.ok) throw new Error(`订单流请求失败: ${res.status}`);
  return res.json();
}

export async function fetchTape(symbol: string) {
  const res = await fetch(`${API_BASE}/orderflow/${symbol}/tape`);
  if (!res.ok) throw new Error(`逐笔成交请求失败: ${res.status}`);
  return res.json();
}

export async function fetchDOM(symbol: string) {
  const res = await fetch(`${API_BASE}/orderflow/${symbol}/dom`);
  if (!res.ok) throw new Error(`深度盘口请求失败: ${res.status}`);
  return res.json();
}

export async function fetchFootprint(symbol: string) {
  const res = await fetch(`${API_BASE}/orderflow/${symbol}/footprint`);
  if (!res.ok) throw new Error(`足迹图请求失败: ${res.status}`);
  return res.json();
}

export async function fetchIceberg(symbol: string) {
  const res = await fetch(`${API_BASE}/orderflow/${symbol}/iceberg`);
  if (!res.ok) throw new Error(`冰山单请求失败: ${res.status}`);
  return res.json();
}

export function subscribeOrderflow(
  symbol: string,
  onData: (data: Record<string, unknown>) => void,
): () => void {
  const es = new EventSource(`${API_BASE}/orderflow/${symbol}/stream`);

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

// ── 门卫与质量看板 API ──

export async function fetchGateStatus() {
  const res = await fetch(`${API_BASE}/gate/status`);
  if (!res.ok) throw new Error(`门卫状态请求失败: ${res.status}`);
  return res.json();
}

export async function fetchQualityBoard() {
  const res = await fetch(`${API_BASE}/quality-board`);
  if (!res.ok) throw new Error(`质量看板请求失败: ${res.status}`);
  return res.json();
}

// ── 进化看板 API ──

export async function fetchOptimizerStats() {
  const res = await fetch(`${API_BASE}/optimizer/stats`);
  if (!res.ok) throw new Error(`优化统计请求失败: ${res.status}`);
  return res.json();
}

export async function fetchSchedulerStatus() {
  const res = await fetch(`${API_BASE}/optimizer/scheduler/status`);
  if (!res.ok) throw new Error(`调度器状态请求失败: ${res.status}`);
  return res.json();
}

export async function fetchAgentStatus() {
  const res = await fetch(`${API_BASE}/optimizer/agent/status`);
  if (!res.ok) throw new Error(`Agent状态请求失败: ${res.status}`);
  return res.json();
}

export async function fetchParamVersion() {
  const res = await fetch(`${API_BASE}/optimizer/params/version`);
  if (!res.ok) throw new Error(`参数版本请求失败: ${res.status}`);
  return res.json();
}

export async function fetchParamHistory(limit = 50) {
  const res = await fetch(`${API_BASE}/optimizer/params/history?limit=${limit}`);
  if (!res.ok) throw new Error(`参数历史请求失败: ${res.status}`);
  return res.json();
}

export async function fetchEvolutionStatus() {
  const res = await fetch(`${API_BASE}/optimizer/evolution/status`);
  if (!res.ok) throw new Error(`进化状态请求失败: ${res.status}`);
  return res.json();
}

export async function fetchEvolutionHistory(limit = 50) {
  const res = await fetch(`${API_BASE}/optimizer/evolution/history?limit=${limit}`);
  if (!res.ok) throw new Error(`进化历史请求失败: ${res.status}`);
  return res.json();
}

export async function fetchEvolutionTrend() {
  const res = await fetch(`${API_BASE}/optimizer/evolution/trend`);
  if (!res.ok) throw new Error(`进化趋势请求失败: ${res.status}`);
  return res.json();
}

export async function fetchSchedulerHistory(limit = 20) {
  const res = await fetch(`${API_BASE}/optimizer/scheduler/history?limit=${limit}`);
  if (!res.ok) throw new Error(`调度历史请求失败: ${res.status}`);
  return res.json();
}

// ── 进化操作 API ──

export async function triggerEvolveNow() {
  const res = await fetch(`${API_BASE}/optimizer/evolve`, { method: 'POST' });
  if (!res.ok) throw new Error(`触发进化失败: ${res.status}`);
  return res.json();
}

export async function approveEvolution(cycleId: string) {
  const res = await fetch(`${API_BASE}/optimizer/evolution/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cycle_id: cycleId }),
  });
  if (!res.ok) throw new Error(`审批进化失败: ${res.status}`);
  return res.json();
}

// ── 参数全景 API ──

export async function fetchAllParams() {
  const res = await fetch(`${API_BASE}/optimizer/params`);
  if (!res.ok) throw new Error(`参数列表请求失败: ${res.status}`);
  return res.json();
}

export async function fetchParamGroups() {
  const res = await fetch(`${API_BASE}/optimizer/params/groups`);
  if (!res.ok) throw new Error(`参数分组请求失败: ${res.status}`);
  return res.json();
}

export async function fetchParamSnapshots() {
  const res = await fetch(`${API_BASE}/optimizer/params/snapshots`);
  if (!res.ok) throw new Error(`参数快照请求失败: ${res.status}`);
  return res.json();
}

export { API_BASE };
