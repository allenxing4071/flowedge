/**
 * 异常告警面板 — 实时显示所有活跃异常事件
 * 参考 KKline 右侧面板：text-sm 基础，标题 text-base，间距宽裕
 */

'use client';

import { useEffect, useState } from 'react';
import { fetchSignals } from '@/lib/api';

interface AnomalyEvent {
  type: string;
  severity: string;
  title: string;
  description: string;
  metric_value: number;
  threshold: number;
  symbol?: string;
}

const SEVERITY_ORDER: Record<string, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
};

const SEVERITY_STYLE: Record<string, { bg: string; border: string; text: string }> = {
  CRITICAL: {
    bg: 'bg-bear/10',
    border: 'border-bear/40',
    text: 'text-bear',
  },
  HIGH: {
    bg: 'bg-warn/10',
    border: 'border-warn/40',
    text: 'text-warn',
  },
  MEDIUM: {
    bg: 'bg-anomaly/10',
    border: 'border-anomaly/30',
    text: 'text-anomaly',
  },
  LOW: {
    bg: 'bg-surface-2',
    border: 'border-surface-3',
    text: 'text-text-secondary',
  },
};

const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: '严重',
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
};

export default function AnomalyPanel() {
  const [anomalies, setAnomalies] = useState<(AnomalyEvent & { symbol: string })[]>([]);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const signals = await fetchSignals();
        const all: (AnomalyEvent & { symbol: string })[] = [];
        for (const [symbol, detail] of Object.entries(signals)) {
          const d = detail as Record<string, unknown>;
          const events = (d.anomalies as AnomalyEvent[]) || [];
          events.forEach(e => all.push({ ...e, symbol }));
        }
        all.sort((a, b) =>
          (SEVERITY_ORDER[a.severity] ?? 4) - (SEVERITY_ORDER[b.severity] ?? 4)
        );
        if (mounted) setAnomalies(all);
      } catch {
        /* 静默 */
      }
      if (mounted) setTimeout(poll, 2000);
    };
    poll();
    return () => { mounted = false; };
  }, []);

  return (
    <div className="card p-6">
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm text-text-secondary font-medium">
          异常告警
        </span>
        {anomalies.length > 0 && (
          <span className="badge badge-anomaly text-sm">
            {anomalies.length}
          </span>
        )}
      </div>

      {anomalies.length === 0 ? (
        <div className="text-center py-10">
          <div className="text-3xl mb-2 opacity-30">&#10003;</div>
          <div className="text-sm text-text-tertiary">当前无异常</div>
        </div>
      ) : (
        <div className="space-y-3 max-h-[500px] overflow-y-auto">
          {anomalies.map((a, i) => {
            const style = SEVERITY_STYLE[a.severity] || SEVERITY_STYLE.LOW;
            return (
              <div
                key={`${a.type}-${a.symbol}-${i}`}
                className={`p-4 rounded-lg border ${style.bg} ${style.border} animate-fade-in`}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2.5">
                    <span className={`text-xs font-bold mono-num ${style.text}`}>
                      {SEVERITY_LABEL[a.severity] || a.severity}
                    </span>
                    <span className="text-sm font-semibold text-text-primary">
                      {a.symbol}
                    </span>
                  </div>
                </div>
                <div className={`text-sm font-medium ${style.text} mb-1`}>
                  {a.title}
                </div>
                <div className="text-xs text-text-tertiary leading-relaxed">
                  {a.description}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
