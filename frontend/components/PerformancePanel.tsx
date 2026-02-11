/**
 * 信号胜率面板 — 回答"信号到底准不准"的核心 UI。
 *
 * 展示：
 *   - 5分钟/15分钟/1小时方向胜率（关键决策指标）
 *   - 按信号类型细分胜率
 *   - 按币种细分胜率
 *   - 最近追踪记录列表
 */

'use client';

import { usePerformance, PerformanceData, WindowStats, formatTimestamp, signalColor } from '@/lib/hooks';

function WinRateBar({ rate, label, total }: { rate: number; label: string; total: number }) {
  // 50% 为基准线，>55% 绿，<50% 红
  const color = rate >= 55
    ? 'bg-bull'
    : rate >= 50
      ? 'bg-info'
      : 'bg-bear';
  const textColor = rate >= 55
    ? 'text-bull'
    : rate >= 50
      ? 'text-info'
      : 'text-bear';

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between">
        <span className="text-sm text-text-secondary">{label}</span>
        <div className="flex items-baseline gap-2">
          <span className={`text-2xl font-bold mono-num ${textColor}`}>
            {total > 0 ? `${rate.toFixed(1)}%` : '--'}
          </span>
          <span className="text-xs text-text-tertiary">
            {total > 0 ? `(${total}笔)` : '待收集'}
          </span>
        </div>
      </div>
      <div className="h-2.5 bg-surface-2 rounded-full overflow-hidden">
        {total > 0 && (
          <div
            className={`h-full ${color} rounded-full transition-all duration-500`}
            style={{ width: `${Math.min(rate, 100)}%` }}
          />
        )}
      </div>
      {/* 50% 基准线标记 */}
      <div className="relative h-0">
        <div className="absolute left-1/2 -top-3 w-px h-2.5 bg-text-tertiary/30" />
      </div>
    </div>
  );
}

function StatCard({ title, value, sub, color }: {
  title: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="card p-4 text-center">
      <div className="text-xs text-text-tertiary mb-1">{title}</div>
      <div className={`text-xl font-bold mono-num ${color || 'text-text-primary'}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-text-tertiary mt-0.5">{sub}</div>}
    </div>
  );
}

function SignalTypeTable({ bySignal }: { bySignal: Record<string, Record<string, WindowStats>> }) {
  const signals = Object.entries(bySignal);
  if (signals.length === 0) {
    return <div className="text-sm text-text-tertiary text-center py-4">暂无数据</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-surface-2">
            <th className="text-left py-2 text-text-tertiary font-medium">信号类型</th>
            <th className="text-right py-2 text-text-tertiary font-medium">5m 胜率</th>
            <th className="text-right py-2 text-text-tertiary font-medium">15m 胜率</th>
            <th className="text-right py-2 text-text-tertiary font-medium">1h 胜率</th>
          </tr>
        </thead>
        <tbody>
          {signals.map(([signal, windows]) => (
            <tr key={signal} className="border-b border-surface-2/50">
              <td className={`py-2 font-medium ${signalColor(signal)}`}>{signal}</td>
              {['5m', '15m', '1h'].map((w) => {
                const ws = windows[w];
                const rate = ws?.win_rate ?? 0;
                const total = ws?.total ?? 0;
                const color = total === 0
                  ? 'text-text-tertiary'
                  : rate >= 55 ? 'text-bull' : rate >= 50 ? 'text-info' : 'text-bear';
                return (
                  <td key={w} className={`text-right py-2 mono-num ${color}`}>
                    {total > 0 ? `${rate.toFixed(1)}%` : '--'}
                    {total > 0 && (
                      <span className="text-text-tertiary text-xs ml-1">({total})</span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentTracks({ recent }: { recent: PerformanceData['recent'] }) {
  if (recent.length === 0) {
    return <div className="text-sm text-text-tertiary text-center py-4">暂无追踪记录</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-surface-2">
            <th className="text-left py-2 text-text-tertiary font-medium">时间</th>
            <th className="text-left py-2 text-text-tertiary font-medium">币种</th>
            <th className="text-left py-2 text-text-tertiary font-medium">信号</th>
            <th className="text-right py-2 text-text-tertiary font-medium">入场价</th>
            <th className="text-right py-2 text-text-tertiary font-medium">5m</th>
            <th className="text-right py-2 text-text-tertiary font-medium">15m</th>
            <th className="text-right py-2 text-text-tertiary font-medium">1h</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((r, i) => (
            <tr key={i} className="border-b border-surface-2/50 hover:bg-surface-1/50">
              <td className="py-2 text-text-secondary text-xs mono-num">
                {formatTimestamp(r.entry_time_ms)}
              </td>
              <td className="py-2 font-medium text-text-primary">
                {r.symbol.replace('USDT', '')}
              </td>
              <td className={`py-2 font-medium ${signalColor(r.signal)}`}>
                {r.signal}
              </td>
              <td className="py-2 text-right mono-num text-text-secondary">
                ${r.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </td>
              {['5m', '15m', '1h'].map((w) => {
                const res = r.results[w];
                if (!res || res.pnl_pct === null) {
                  return (
                    <td key={w} className="py-2 text-right text-text-tertiary text-xs">
                      <span className="inline-block w-2 h-2 rounded-full bg-text-tertiary/30 animate-pulse" />
                    </td>
                  );
                }
                const pnl = res.pnl_pct;
                const correct = res.correct;
                const color = correct ? 'text-bull' : 'text-bear';
                const icon = correct ? '✓' : '✗';
                return (
                  <td key={w} className={`py-2 text-right mono-num ${color}`}>
                    <span className="text-xs mr-0.5">{icon}</span>
                    {pnl > 0 ? '+' : ''}{pnl.toFixed(3)}%
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function PerformancePanel() {
  const { data, loading, error } = usePerformance(undefined, 10000);

  if (loading) {
    return (
      <div className="card p-6 animate-pulse">
        <div className="h-6 bg-surface-2 rounded w-48 mb-4" />
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-10 bg-surface-2 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !data || !('total_signals' in data) || !data.windows) {
    return (
      <div className="card p-6">
        <div className="text-sm text-text-tertiary text-center">
          胜率追踪暂不可用 — 等待信号数据积累
        </div>
      </div>
    );
  }

  const w = data.windows;
  const noData = data.total_signals === 0;

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-text-primary">信号胜率追踪</h2>
          <p className="text-sm text-text-tertiary mt-0.5">
            {noData
              ? '追踪器已启动，等待信号变化收集数据...'
              : `已追踪 ${data.total_signals} 条信号`
            }
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-bull animate-pulse" />
          <span className="text-xs text-text-tertiary">实时追踪中</span>
        </div>
      </div>

      {/* 关键指标卡片 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard
          title="总信号数"
          value={data.total_signals.toString()}
          sub="有方向信号"
        />
        <StatCard
          title="5m 胜率"
          value={w['5m']?.total > 0 ? `${w['5m'].win_rate.toFixed(1)}%` : '--'}
          sub={w['5m']?.total > 0 ? `${w['5m'].correct}/${w['5m'].total}` : '待收集'}
          color={w['5m']?.win_rate >= 55 ? 'text-bull' : w['5m']?.win_rate >= 50 ? 'text-info' : 'text-bear'}
        />
        <StatCard
          title="15m 胜率"
          value={w['15m']?.total > 0 ? `${w['15m'].win_rate.toFixed(1)}%` : '--'}
          sub={w['15m']?.total > 0 ? `${w['15m'].correct}/${w['15m'].total}` : '待收集'}
          color={w['15m']?.win_rate >= 52 ? 'text-bull' : w['15m']?.win_rate >= 50 ? 'text-info' : 'text-bear'}
        />
        <StatCard
          title="1h 胜率"
          value={w['1h']?.total > 0 ? `${w['1h'].win_rate.toFixed(1)}%` : '--'}
          sub={w['1h']?.total > 0 ? `${w['1h'].correct}/${w['1h'].total}` : '待收集'}
          color={w['1h']?.win_rate >= 52 ? 'text-bull' : w['1h']?.win_rate >= 50 ? 'text-info' : 'text-bear'}
        />
      </div>

      {/* 胜率条 */}
      <div className="card p-6 space-y-5">
        <h3 className="text-base font-semibold text-text-primary">方向胜率</h3>
        <WinRateBar
          label="5 分钟"
          rate={w['5m']?.win_rate ?? 0}
          total={w['5m']?.total ?? 0}
        />
        <WinRateBar
          label="15 分钟"
          rate={w['15m']?.win_rate ?? 0}
          total={w['15m']?.total ?? 0}
        />
        <WinRateBar
          label="1 小时"
          rate={w['1h']?.win_rate ?? 0}
          total={w['1h']?.total ?? 0}
        />
        <div className="text-xs text-text-tertiary pt-2 border-t border-surface-2">
          基准线 50%（随机猜测）。5m 胜率 &gt; 55% 表示信号有短期边，15m &gt; 52% 可用于中频交易。
        </div>
      </div>

      {/* 按信号类型细分 */}
      {Object.keys(data.by_signal).length > 0 && (
        <div className="card p-6">
          <h3 className="text-base font-semibold text-text-primary mb-4">按信号类型</h3>
          <SignalTypeTable bySignal={data.by_signal} />
        </div>
      )}

      {/* 最近追踪记录 */}
      <div className="card p-6">
        <h3 className="text-base font-semibold text-text-primary mb-4">最近追踪</h3>
        <RecentTracks recent={data.recent} />
      </div>
    </div>
  );
}
