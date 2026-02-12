/**
 * 信号汇总条 — 全局信号分布概览
 * 放大字体、增加间距
 */

'use client';

import { DashboardData } from '@/lib/hooks';

interface Props {
  data: DashboardData;
}

export default function SignalSummary({ data }: Props) {
  const { summary } = data;
  const total = summary.total_symbols || 1;

  const segments = [
    { key: 'strong_buy', count: summary.strong_buy, label: '强多', color: 'bg-bull', textColor: 'text-bull' },
    { key: 'buy', count: summary.buy, label: '看多', color: 'bg-bull/50', textColor: 'text-bull/70' },
    { key: 'neutral', count: summary.neutral, label: '中性', color: 'bg-surface-3', textColor: 'text-text-secondary' },
    { key: 'sell', count: summary.sell, label: '看空', color: 'bg-bear/50', textColor: 'text-bear/70' },
    { key: 'strong_sell', count: summary.strong_sell, label: '强空', color: 'bg-bear', textColor: 'text-bear' },
  ].filter(s => s.count > 0);

  return (
    <div className="card px-3 sm:px-6 py-3 sm:py-4">
      <div className="flex items-center justify-between mb-2 sm:mb-3 flex-wrap gap-1">
        <span className="text-xs sm:text-sm text-text-secondary font-medium">
          信号分布
        </span>
        <span className="text-xxs sm:text-sm text-text-tertiary mono-num">
          {summary.total_symbols} 币种 · 第 {summary.eval_count} 次
        </span>
      </div>

      {/* 分布条 */}
      <div className="flex h-2.5 sm:h-3 rounded-full overflow-hidden gap-0.5 mb-2 sm:mb-3">
        {segments.map(seg => (
          <div
            key={seg.key}
            className={`${seg.color} transition-all duration-500 rounded-full`}
            style={{ width: `${(seg.count / total) * 100}%`, minWidth: seg.count > 0 ? '6px' : '0' }}
          />
        ))}
      </div>

      {/* 标签 */}
      <div className="flex items-center flex-wrap gap-3 sm:gap-6">
        {segments.map(seg => (
          <div key={seg.key} className="flex items-center gap-1.5 sm:gap-2">
            <div className={`h-2 w-2 sm:h-2.5 sm:w-2.5 rounded-sm ${seg.color}`} />
            <span className={`text-xs sm:text-sm font-medium ${seg.textColor}`}>
              {seg.label} {seg.count}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
