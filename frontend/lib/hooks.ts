/**
 * 自定义 React Hooks — 实时数据订阅与轮询
 */

'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { fetchDashboard, fetchSignal, fetchFeatures, fetchSignalHistory, fetchPerformance } from './api';

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
