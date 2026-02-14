/**
 * 进化看板 — 版本时间线
 * 从新到旧排列所有优化版本，每版一张 VersionCard。
 * 自动匹配调度器历史和参数变更历史。
 */

'use client';

import {
  useSchedulerHistory,
  useParamHistory,
  SchedulerHistoryItem,
  ParamHistoryItem,
} from '@/lib/hooks';
import VersionCard from './VersionCard';

function EmptyTimeline() {
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <div className="h-12 w-12 rounded-xl bg-surface-2 flex items-center justify-center">
        <svg className="h-6 w-6 text-text-tertiary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
      </div>
      <div className="text-sm text-text-secondary font-medium">暂无进化记录</div>
      <div className="text-xs text-text-tertiary text-center max-w-sm">
        系统将在积累足够信号数据后自动触发首轮优化，届时进化记录将在此展示
      </div>
    </div>
  );
}

/**
 * 将调度器历史和参数历史按时间匹配
 * 策略：对每个 scheduler run，找最近的 paramChange（时间差 < 5 分钟）
 */
function matchRunsWithParams(
  runs: SchedulerHistoryItem[],
  params: ParamHistoryItem[],
): Array<{ run: SchedulerHistoryItem; paramChange?: ParamHistoryItem }> {
  return runs.map((run) => {
    if (!run.started_at || params.length === 0) return { run };

    const runTime = new Date(run.started_at).getTime();
    let bestMatch: ParamHistoryItem | undefined;
    let bestDiff = Infinity;

    for (const p of params) {
      const pTime = p.timestamp * 1000; // timestamp 是秒级
      const diff = Math.abs(pTime - runTime);
      if (diff < bestDiff && diff < 5 * 60 * 1000) { // 5 分钟内
        bestDiff = diff;
        bestMatch = p;
      }
    }

    return { run, paramChange: bestMatch };
  });
}

export default function VersionTimeline() {
  const { data: schedulerData, loading: schedulerLoading } = useSchedulerHistory(15000);
  const { data: paramData, loading: paramLoading } = useParamHistory(30000);

  const runs = schedulerData?.history ?? [];
  const params = paramData?.history ?? [];

  const loading = schedulerLoading && !schedulerData;

  if (loading) {
    return (
      <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
        <div className="flex items-center justify-center py-8">
          <span className="text-xs text-text-tertiary animate-pulse">加载进化记录...</span>
        </div>
      </div>
    );
  }

  // 从新到旧排列
  const sortedRuns = [...runs].sort((a, b) => {
    return new Date(b.started_at).getTime() - new Date(a.started_at).getTime();
  });

  const matched = matchRunsWithParams(sortedRuns, params);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-text-primary">版本时间线</span>
          {runs.length > 0 && (
            <span className="text-xxs text-text-tertiary">
              共 {runs.length} 轮
            </span>
          )}
        </div>
      </div>

      {matched.length === 0 ? (
        <EmptyTimeline />
      ) : (
        <div className="space-y-2">
          {matched.map((item, idx) => (
            <VersionCard
              key={item.run.run_id}
              versionIndex={runs.length - idx}
              run={item.run}
              paramChange={item.paramChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}
