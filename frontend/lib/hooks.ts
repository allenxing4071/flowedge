/**
 * 自定义 React Hooks — 实时数据订阅与轮询
 */

'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchDashboard, fetchSignal, fetchFeatures, fetchSignalHistory,
  fetchPerformance, fetchGateStatus,
  fetchSchedulerStatus, fetchAgentStatus, fetchParamVersion,
  fetchEvolutionTrend, fetchEvolutionHistory, fetchSchedulerHistory,
  fetchParamHistory, fetchOptimizerStats,
} from './api';

// ── 通用轮询 Hook ──

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 1000,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      try {
        const result = await fetcher();
        if (mountedRef.current) {
          setData(result);
          setError(null);
          setLoading(false);
        }
      } catch (e) {
        if (mountedRef.current) {
          setError(e instanceof Error ? e.message : '连接失败');
          setLoading(false);
        }
      }
      if (mountedRef.current) {
        timer = setTimeout(poll, intervalMs);
      }
    };

    poll();

    return () => {
      mountedRef.current = false;
      clearTimeout(timer);
    };
  }, [intervalMs]); // eslint-disable-line react-hooks/exhaustive-deps

  return { data, error, loading };
}

// ── Dashboard 实时数据 ──

export interface DashboardData {
  symbols: Record<string, SymbolSignal>;
  summary: {
    total_symbols: number;
    strong_buy: number;
    buy: number;
    neutral: number;
    sell: number;
    strong_sell: number;
    eval_count: number;
  };
}

export interface SymbolSignal {
  signal: string;
  score: number;
  confidence: number;
  risk_level: string;
  anomaly_count: number;
  bullish_factors: number;
  bearish_factors: number;
  signal_changed: boolean;
  last_update_ms: number;
}

export interface SignalDetail {
  symbol: string;
  signal: string;
  score: number;
  confidence: number;
  bullish_count: number;
  bearish_count: number;
  neutral_count: number;
  factors: FactorDetail[];
  risk_level: string;
  anomalies: AnomalyDetail[];
  anomaly_count: number;
  prev_signal: string | null;
  signal_changed: boolean;
  last_update_ms: number;
}

export interface FactorDetail {
  name: string;
  score: number;
  weight: number;
  raw_value: number;
  reason: string;
  weighted_score: number;
}

export interface AnomalyDetail {
  type: string;
  severity: string;
  title: string;
  description: string;
  metric_value: number;
  threshold: number;
}

export function useDashboard(intervalMs = 1000) {
  return usePolling<DashboardData>(fetchDashboard, intervalMs);
}

export function useSignalDetail(symbol: string, intervalMs = 1000) {
  const fetcher = useCallback(() => fetchSignal(symbol), [symbol]);
  return usePolling<SignalDetail>(fetcher, intervalMs);
}

export function useFeatures(symbol?: string, intervalMs = 2000) {
  const fetcher = useCallback(() => fetchFeatures(symbol), [symbol]);
  return usePolling<Record<string, unknown>>(fetcher, intervalMs);
}

export function useSignalHistory(symbol: string, limit = 100, intervalMs = 5000) {
  const fetcher = useCallback(
    () => fetchSignalHistory(symbol, limit),
    [symbol, limit],
  );
  return usePolling<Array<Record<string, unknown>>>(fetcher, intervalMs);
}

// ── 工具函数 ──

export function signalColor(signal: string): string {
  switch (signal) {
    case 'STRONG_BUY': return 'text-bull';
    case 'BUY': return 'text-bull/70';
    case 'STRONG_SELL': return 'text-bear';
    case 'SELL': return 'text-bear/70';
    default: return 'text-text-secondary';
  }
}

export function signalBg(signal: string): string {
  switch (signal) {
    case 'STRONG_BUY': return 'bg-bull-dim';
    case 'BUY': return 'bg-bull-glow';
    case 'STRONG_SELL': return 'bg-bear-dim';
    case 'SELL': return 'bg-bear-glow';
    default: return 'bg-surface-2';
  }
}

export function riskColor(level: string): string {
  switch (level) {
    case 'EXTREME': return 'text-bear';
    case 'HIGH': return 'text-warn';
    case 'ELEVATED': return 'text-warn/70';
    default: return 'text-text-tertiary';
  }
}

export function riskLabel(level: string): string {
  switch (level) {
    case 'EXTREME': return '极高';
    case 'HIGH': return '高';
    case 'ELEVATED': return '偏高';
    case 'LOW': return '低';
    case 'MEDIUM': return '中';
    default: return level || '--';
  }
}

export function signalLabel(signal: string): string {
  switch (signal) {
    case 'STRONG_BUY': return '强烈看多';
    case 'BUY': return '看多';
    case 'NEUTRAL': return '中性';
    case 'SELL': return '看空';
    case 'STRONG_SELL': return '强烈看空';
    default: return signal;
  }
}

export function formatScore(score: number): string {
  const sign = score > 0 ? '+' : '';
  return `${sign}${(score * 100).toFixed(1)}`;
}

export function sideLabel(side: string): string {
  switch (side) {
    case 'LONG': return '多头';
    case 'SHORT': return '空头';
    case 'BUY': return '买入';
    case 'SELL': return '卖出';
    default: return side || '--';
  }
}

export function formatTimestamp(ms: number): string {
  if (!ms) return '--';
  const d = new Date(ms);
  return d.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Shanghai',
  });
}

// ── 门卫状态 ──

export interface GateLayerResult {
  passed: boolean;
  detail: string;
  data: Record<string, unknown>;
}

export interface GateStatusItem {
  passed: boolean;
  signal: string;
  side: string;
  regime: GateLayerResult;
  location: GateLayerResult;
  behavior: GateLayerResult;
  direction: GateLayerResult;
  reject_layer: string | null;
  reject_reason: string | null;
  suggested_stop_loss_pct: number;
  suggested_take_profit_pct: number;
}

export type GateStatusMap = Record<string, GateStatusItem>;

export function useGateStatus(intervalMs = 2000) {
  return usePolling<GateStatusMap>(fetchGateStatus, intervalMs);
}

// ── 信号胜率追踪 ──

export interface WindowStats {
  total: number;
  correct: number;
  win_rate: number;
  avg_pnl: number;
  avg_win?: number;
  avg_loss?: number;
}

export interface PerformanceData {
  total_signals: number;
  windows: Record<string, WindowStats>;
  by_signal: Record<string, Record<string, WindowStats>>;
  by_symbol: Record<string, Record<string, WindowStats>>;
  recent: Array<{
    symbol: string;
    signal: string;
    score: number;
    confidence: number;
    entry_price: number;
    entry_time_ms: number;
    results: Record<string, {
      price: number | null;
      pnl_pct: number | null;
      correct: boolean | null;
    }>;
  }>;
}

export function usePerformance(symbol?: string, intervalMs = 10000) {
  const fetcher = useCallback(
    () => fetchPerformance(symbol),
    [symbol],
  );
  return usePolling<PerformanceData>(fetcher, intervalMs);
}

// ── 进化看板 Hooks ──

export interface SchedulerStatusData {
  is_running: boolean;
  background_active: boolean;
  total_runs: number;
  trigger_mode: string;
  should_trigger: boolean;
  trigger_reason: string;
  last_run_sample_count: number;
  config: {
    check_interval_s: number;
    lookback_days: number;
    min_samples: number;
    min_new_signals: number;
    n_trials: number;
    auto_apply: boolean;
  };
  last_run?: {
    run_id: string;
    status: string;
    started_at: string;
    elapsed_s: number;
    validation_passed: boolean;
    applied: boolean;
  };
  stats: { success: number; failed: number; skipped: number };
}

export interface AgentStatusData {
  enabled: boolean;
  provider: string;
  model: string;
  api_key_set: boolean;
  calls_today: number;
  max_calls_per_day: number;
  est_cost_today_usd: number;
  daily_budget_usd: number;
  last_run_at: string;
  last_error: string;
}

export interface ParamVersionData {
  version: number;
  updated_at: string;
  last_label: string;
}

export interface EvolutionTrendItem {
  cycle_id: string;
  started_at: string;
  best_sharpe: number;
  validation_score: number;
  ai_grade: string;
  ai_score: number;
  total_signals: number;
}

export interface EvolutionTrendData {
  trend: EvolutionTrendItem[];
  total_successful: number;
  message: string;
}

export interface EvolutionHistoryItem {
  cycle_id: string;
  status: string;
  started_at: string;
  elapsed_s: number;
  total_signals: number;
  new_signals: number;
  validation_passed: boolean;
  validation_score: number;
  ai_grade: string;
  ai_score: number;
  ai_summary: string;
  applied: boolean;
  failure_reason: string | null;
}

export interface SchedulerHistoryItem {
  run_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  elapsed_s: number;
  total_signals: number;
  train_size: number;
  test_size: number;
  validation_passed: boolean;
  validation_score: number;
  applied: boolean;
  snapshot_name: string | null;
  failure_reason: string | null;
}

export interface ParamHistoryItem {
  timestamp: number;
  timestamp_iso: string;
  version: number;
  label: string;
  source: string;
  changes_count: number;
  changes: Record<string, { old: number; new: number }>;
}

export interface OptimizerStatsData {
  registry: {
    total_params: number;
    version: number;
    updated_at: string;
    last_label: string;
    history_count: number;
    snapshots_count: number;
  };
  data: {
    total_records: number;
    records_with_1h: number;
    records_with_factors: number;
    symbols: string[];
    date_range_days: number;
    min_sample_ok: boolean;
    issues: string[];
  };
  scheduler: SchedulerStatusData;
  agent: AgentStatusData;
}

export function useSchedulerStatus(intervalMs = 10000) {
  return usePolling<SchedulerStatusData>(fetchSchedulerStatus, intervalMs);
}

export function useAgentStatus(intervalMs = 10000) {
  return usePolling<AgentStatusData>(fetchAgentStatus, intervalMs);
}

export function useParamVersion(intervalMs = 10000) {
  return usePolling<ParamVersionData>(fetchParamVersion, intervalMs);
}

export function useEvolutionTrend(intervalMs = 30000) {
  return usePolling<EvolutionTrendData>(fetchEvolutionTrend, intervalMs);
}

export function useEvolutionHistory(intervalMs = 15000) {
  const fetcher = useCallback(() => fetchEvolutionHistory(50), []);
  return usePolling<{ history: EvolutionHistoryItem[] }>(fetcher, intervalMs);
}

export function useSchedulerHistory(intervalMs = 15000) {
  const fetcher = useCallback(() => fetchSchedulerHistory(20), []);
  return usePolling<{ history: SchedulerHistoryItem[] }>(fetcher, intervalMs);
}

export function useParamHistory(intervalMs = 30000) {
  const fetcher = useCallback(() => fetchParamHistory(50), []);
  return usePolling<{ history: ParamHistoryItem[] }>(fetcher, intervalMs);
}

export function useOptimizerStats(intervalMs = 10000) {
  return usePolling<OptimizerStatsData>(fetchOptimizerStats, intervalMs);
}

// 进化看板工具函数

export function gradeColor(grade: string): string {
  switch (grade) {
    case 'A': return 'text-bull';
    case 'B': return 'text-info';
    case 'C': return 'text-warn';
    case 'D': case 'F': return 'text-bear';
    default: return 'text-text-tertiary';
  }
}

export function gradeBg(grade: string): string {
  switch (grade) {
    case 'A': return 'bg-bull/15';
    case 'B': return 'bg-info/15';
    case 'C': return 'bg-warn/15';
    case 'D': case 'F': return 'bg-bear/15';
    default: return 'bg-surface-2';
  }
}

export function statusColor(status: string): string {
  switch (status) {
    case 'success': return 'text-bull';
    case 'completed': return 'text-info';
    case 'failed': return 'text-bear';
    case 'skipped': return 'text-text-tertiary';
    case 'pending_approval': return 'text-warn';
    case 'running': return 'text-info';
    default: return 'text-text-tertiary';
  }
}

export function statusLabel(status: string): string {
  switch (status) {
    case 'success': return '成功';
    case 'completed': return '已完成';
    case 'failed': return '失败';
    case 'skipped': return '跳过';
    case 'pending_approval': return '待确认';
    case 'running': return '运行中';
    default: return status || '--';
  }
}

export function formatDateTimeBJT(isoStr: string): string {
  if (!isoStr) return '--';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  } catch {
    return isoStr;
  }
}
