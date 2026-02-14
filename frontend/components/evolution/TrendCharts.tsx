/**
 * 进化看板 — 趋势图
 * 展示 Sharpe Ratio / 验证得分 / AI 评分 三条趋势折线。
 * 无数据时显示友好的空态提示。
 */

'use client';

import { useEvolutionTrend, EvolutionTrendItem, formatDateTimeBJT } from '@/lib/hooks';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';

// 将 AI 评级转为数值（用于图表）
function gradeToScore(grade: string): number {
  switch (grade) {
    case 'A': return 5;
    case 'B': return 4;
    case 'C': return 3;
    case 'D': return 2;
    case 'F': return 1;
    default: return 0;
  }
}

interface ChartDataPoint {
  name: string;
  sharpe: number;
  validation: number;
  aiScore: number;
  fullDate: string;
}

function transformData(items: EvolutionTrendItem[]): ChartDataPoint[] {
  return items.map((item, idx) => ({
    name: `v${idx + 1}`,
    sharpe: Number(item.best_sharpe?.toFixed(3) ?? 0),
    validation: Number((item.validation_score * 100)?.toFixed(1) ?? 0),
    aiScore: item.ai_score ?? gradeToScore(item.ai_grade),
    fullDate: formatDateTimeBJT(item.started_at),
  }));
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const point = payload[0]?.payload as ChartDataPoint;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 shadow-xl text-xs">
      <div className="text-text-primary font-semibold mb-1">{label}</div>
      <div className="text-text-tertiary mb-2">{point?.fullDate}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full" style={{ background: p.color }} />
          <span className="text-text-secondary">{p.name}:</span>
          <span className="text-text-primary font-mono">{p.value}</span>
        </div>
      ))}
    </div>
  );
}
/* eslint-enable @typescript-eslint/no-explicit-any */

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3">
      <div className="h-12 w-12 rounded-xl bg-info/10 flex items-center justify-center">
        <svg className="h-6 w-6 text-info" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
        </svg>
      </div>
      <div className="text-sm text-text-secondary font-medium">系统正在积累数据</div>
      <div className="text-xs text-text-tertiary text-center max-w-sm">
        首轮进化将在积累足够信号后自动触发，趋势图将在完成首次优化后展示
      </div>
    </div>
  );
}

export default function TrendCharts() {
  const { data, loading } = useEvolutionTrend(30000);

  const trend = data?.trend ?? [];
  const chartData = transformData(trend);

  if (loading && !data) {
    return (
      <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
        <div className="h-[260px] flex items-center justify-center">
          <span className="text-xs text-text-tertiary animate-pulse">加载趋势数据...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary">进化趋势</span>
          {trend.length > 0 && (
            <span className="text-xxs text-text-tertiary">
              共 {data?.total_successful ?? trend.length} 轮成功优化
            </span>
          )}
        </div>
      </div>

      {chartData.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="h-[260px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1a1a28" />
              <XAxis
                dataKey="name"
                tick={{ fill: '#555570', fontSize: 11 }}
                axisLine={{ stroke: '#1a1a28' }}
              />
              <YAxis
                yAxisId="left"
                tick={{ fill: '#555570', fontSize: 11 }}
                axisLine={{ stroke: '#1a1a28' }}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                tick={{ fill: '#555570', fontSize: 11 }}
                axisLine={{ stroke: '#1a1a28' }}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: 11, color: '#8888a0' }}
              />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="sharpe"
                name="Sharpe"
                stroke="#00e676"
                strokeWidth={2}
                dot={{ r: 3, fill: '#00e676' }}
                activeDot={{ r: 5 }}
              />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="validation"
                name="验证得分"
                stroke="#448aff"
                strokeWidth={2}
                dot={{ r: 3, fill: '#448aff' }}
                activeDot={{ r: 5 }}
              />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="aiScore"
                name="AI评分"
                stroke="#ffab00"
                strokeWidth={2}
                dot={{ r: 3, fill: '#ffab00' }}
                activeDot={{ r: 5 }}
                strokeDasharray="5 5"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
