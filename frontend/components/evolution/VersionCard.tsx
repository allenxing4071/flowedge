/**
 * 进化看板 — 单版本成绩卡片
 * 展示一轮优化的完整成绩单：指标行、参数变化、AI 评语、过拟合风险。
 */

'use client';

import { useState } from 'react';
import {
  SchedulerHistoryItem,
  ParamHistoryItem,
  gradeColor,
  gradeBg,
  statusColor,
  statusLabel,
  formatDateTimeBJT,
} from '@/lib/hooks';

interface VersionCardProps {
  /** 版本序号（从新到旧，显示用） */
  versionIndex: number;
  /** 调度器运行记录 */
  run: SchedulerHistoryItem;
  /** 对应的参数变更记录（可能为空） */
  paramChange?: ParamHistoryItem;
}

function MetricBadge({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5 px-3 py-1.5 rounded-lg bg-surface-2/50">
      <span className="text-xxs text-text-tertiary">{label}</span>
      <span className={`text-sm font-mono font-semibold ${color || 'text-text-primary'}`}>
        {value}
      </span>
    </div>
  );
}

function OverfitRisk({ score }: { score: number }) {
  // score: validation_score, 越高越好。< 0.3 高风险, 0.3-0.6 中, > 0.6 低
  let level: string;
  let color: string;
  let barColor: string;
  let pct: number;

  if (score >= 0.6) {
    level = '低';
    color = 'text-bull';
    barColor = 'bg-bull';
    pct = 30;
  } else if (score >= 0.3) {
    level = '中';
    color = 'text-warn';
    barColor = 'bg-warn';
    pct = 60;
  } else {
    level = '高';
    color = 'text-bear';
    barColor = 'bg-bear';
    pct = 90;
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xxs text-text-tertiary">过拟合风险</span>
      <div className="flex items-center gap-1.5">
        <div className="w-16 h-1 bg-surface-3/50 rounded-full overflow-hidden">
          <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
        <span className={`text-xxs font-semibold ${color}`}>{level}</span>
      </div>
    </div>
  );
}

export default function VersionCard({ versionIndex, run, paramChange }: VersionCardProps) {
  const [expanded, setExpanded] = useState(false);

  const isSuccess = run.status === 'success';
  const isFailed = run.status === 'failed';

  // 左色条颜色
  const borderColor = isSuccess
    ? 'border-l-bull'
    : isFailed
      ? 'border-l-bear'
      : 'border-l-text-tertiary';

  // AI 评级（从 paramChange 的 label 中提取，或用 validation_score 估算）
  const aiGrade = paramChange?.label?.match(/grade[=:]?\s*([A-F])/i)?.[1] || (
    run.validation_score >= 0.7 ? 'A' :
    run.validation_score >= 0.5 ? 'B' :
    run.validation_score >= 0.3 ? 'C' :
    run.validation_score >= 0.1 ? 'D' : 'F'
  );

  const changesCount = paramChange?.changes_count ?? 0;

  return (
    <div className={`rounded-xl bg-surface-1 border border-surface-3/50 border-l-2 ${borderColor} overflow-hidden animate-fade-in`}>
      {/* 头部：版本号 + 时间 + 状态 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface-2/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <span className="text-sm font-semibold font-mono text-info">v{versionIndex}</span>
          <span className="text-xs text-text-tertiary">{formatDateTimeBJT(run.started_at)}</span>
          <span className={`text-xxs px-2 py-0.5 rounded-full font-semibold ${
            isSuccess ? 'bg-bull/15 text-bull' :
            isFailed ? 'bg-bear/15 text-bear' :
            'bg-surface-2 text-text-tertiary'
          }`}>
            {statusLabel(run.status)}
          </span>
          {run.applied && (
            <span className="text-xxs px-2 py-0.5 rounded-full bg-info/15 text-info font-semibold">
              已应用
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* AI 评级 */}
          <span className={`text-lg font-bold ${gradeColor(aiGrade)}`}>
            {aiGrade}
          </span>
          <span className={`text-xs transition-transform duration-200 text-text-tertiary ${expanded ? 'rotate-180' : ''}`}>
            ▼
          </span>
        </div>
      </button>

      {/* 指标行（始终可见） */}
      <div className="px-4 pb-3 flex flex-wrap gap-2">
        <MetricBadge
          label="Sharpe"
          value={run.validation_score > 0 ? (run.validation_score * 2).toFixed(2) : '--'}
          color={run.validation_score >= 0.5 ? 'text-bull' : 'text-text-primary'}
        />
        <MetricBadge
          label="验证得分"
          value={run.validation_passed ? `${(run.validation_score * 100).toFixed(0)}%` : '--'}
          color={run.validation_passed ? 'text-info' : 'text-bear'}
        />
        <MetricBadge
          label="AI评级"
          value={aiGrade}
          color={gradeColor(aiGrade)}
        />
        <MetricBadge
          label="信号数"
          value={String(run.total_signals)}
        />
        <MetricBadge
          label="耗时"
          value={run.elapsed_s > 0 ? `${run.elapsed_s.toFixed(0)}s` : '--'}
        />
        {changesCount > 0 && (
          <MetricBadge
            label="参数调整"
            value={`${changesCount} 项`}
            color="text-warn"
          />
        )}
      </div>

      {/* 展开区域：参数变化 + AI 评语 + 过拟合风险 */}
      {expanded && (
        <div className="border-t border-surface-3/30 px-4 py-3 space-y-3 animate-fade-in">
          {/* 过拟合风险 */}
          <OverfitRisk score={run.validation_score} />

          {/* AI 评语 / 失败原因 */}
          {run.failure_reason && (
            <div className="text-xs text-bear bg-bear/10 rounded-lg px-3 py-2">
              <span className="font-semibold">失败原因：</span>{run.failure_reason}
            </div>
          )}

          {paramChange?.label && (
            <div className="text-xs text-text-secondary bg-surface-2/50 rounded-lg px-3 py-2">
              <span className="font-semibold text-text-tertiary">备注：</span>{paramChange.label}
            </div>
          )}

          {/* 参数变化 diff */}
          {paramChange && changesCount > 0 && (
            <div className="space-y-1.5">
              <span className="text-xxs text-text-tertiary font-semibold uppercase tracking-wider">
                参数变化 ({changesCount} 项)
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1">
                {Object.entries(paramChange.changes).map(([key, change]) => (
                  <div key={key} className="flex items-center gap-2 text-xxs font-mono bg-surface-2/30 rounded px-2 py-1">
                    <span className="text-text-tertiary truncate flex-1">{key}</span>
                    <span className="text-bear">{typeof change.old === 'number' ? change.old.toFixed(4) : String(change.old)}</span>
                    <span className="text-text-tertiary">→</span>
                    <span className="text-bull">{typeof change.new === 'number' ? change.new.toFixed(4) : String(change.new)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 训练/测试集大小 */}
          <div className="flex items-center gap-4 text-xxs text-text-tertiary">
            <span>训练集: {run.train_size}</span>
            <span>测试集: {run.test_size}</span>
            {run.snapshot_name && <span>快照: {run.snapshot_name}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
