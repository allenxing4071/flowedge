/**
 * 进化看板 — 实时状态条
 * 展示当前参数版本号、数据积累进度、调度器/Agent 状态、下一轮预估。
 */

'use client';

import {
  useSchedulerStatus,
  useAgentStatus,
  useParamVersion,
  useOptimizerStats,
  formatDateTimeBJT,
} from '@/lib/hooks';

function StatItem({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xxs text-text-tertiary uppercase tracking-wider">{label}</span>
      <span className="text-sm font-semibold text-text-primary">{value}</span>
      {sub && <span className="text-xxs text-text-tertiary">{sub}</span>}
    </div>
  );
}

function ProgressBar({ current, total, label }: { current: number; total: number; label: string }) {
  const pct = total > 0 ? Math.min((current / total) * 100, 100) : 0;
  return (
    <div className="flex flex-col gap-1 min-w-[140px]">
      <div className="flex items-center justify-between text-xxs">
        <span className="text-text-tertiary">{label}</span>
        <span className="text-text-secondary font-mono">{current}/{total}</span>
      </div>
      <div className="h-1.5 bg-surface-3/50 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500 ease-out"
          style={{
            width: `${pct}%`,
            background: pct >= 100
              ? 'linear-gradient(90deg, #00e676, #00c853)'
              : 'linear-gradient(90deg, #448aff, #00e676)',
          }}
        />
      </div>
    </div>
  );
}

function StatusDot({ active, label }: { active: boolean; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className={`h-1.5 w-1.5 rounded-full ${active ? 'bg-bull animate-pulse-slow' : 'bg-text-tertiary'}`} />
      <span className={`text-xs ${active ? 'text-bull' : 'text-text-tertiary'}`}>{label}</span>
    </div>
  );
}

export default function StatusBar() {
  const { data: scheduler } = useSchedulerStatus(10000);
  const { data: agent } = useAgentStatus(10000);
  const { data: version } = useParamVersion(10000);
  const { data: stats } = useOptimizerStats(10000);

  const dataInfo = stats?.data;
  const minSamples = scheduler?.config?.min_samples ?? 30;
  const currentSamples = dataInfo?.records_with_1h ?? 0;

  return (
    <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
        {/* 版本号 */}
        <StatItem
          label="参数版本"
          value={
            <span className="font-mono text-info">
              v{version?.version ?? 0}
            </span>
          }
          sub={version?.updated_at ? formatDateTimeBJT(version.updated_at) : undefined}
        />

        {/* 数据积累进度 */}
        <ProgressBar
          current={currentSamples}
          total={minSamples}
          label="已验证信号"
        />

        {/* 调度器状态 */}
        <div className="flex flex-col gap-1">
          <span className="text-xxs text-text-tertiary uppercase tracking-wider">调度器</span>
          <div className="flex items-center gap-3">
            <StatusDot
              active={!!scheduler?.background_active}
              label={scheduler?.trigger_mode === 'sample_driven' ? '样本驱动' : '定时'}
            />
            {scheduler?.config && (
              <span className="text-xxs text-text-tertiary">
                每{Math.round(scheduler.config.check_interval_s / 60)}分钟检查
              </span>
            )}
          </div>
          {scheduler?.trigger_reason && (
            <span className="text-xxs text-text-tertiary truncate max-w-[200px]">
              {scheduler.trigger_reason}
            </span>
          )}
        </div>

        {/* Agent 状态 */}
        <div className="flex flex-col gap-1">
          <span className="text-xxs text-text-tertiary uppercase tracking-wider">Agent</span>
          <div className="flex items-center gap-3">
            <StatusDot
              active={!!agent?.api_key_set}
              label={agent?.provider || '未配置'}
            />
            {agent?.api_key_set && (
              <span className="text-xxs text-text-tertiary font-mono">
                {agent.calls_today}/{agent.max_calls_per_day} 调用
              </span>
            )}
          </div>
        </div>

        {/* 运行统计 */}
        {scheduler?.stats && (
          <StatItem
            label="优化统计"
            value={
              <span className="flex items-center gap-2 text-xs">
                <span className="text-bull">{scheduler.stats.success} 成功</span>
                <span className="text-bear">{scheduler.stats.failed} 失败</span>
                <span className="text-text-tertiary">{scheduler.stats.skipped} 跳过</span>
              </span>
            }
          />
        )}
      </div>
    </div>
  );
}
