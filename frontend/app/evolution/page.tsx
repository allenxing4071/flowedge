/**
 * 进化看板页面 — /evolution
 * 参考 KKline 风格重构：Tab 导航 + 状态卡片 + 进度条 + 操作按钮 + 历史表格。
 * 展示系统自动优化的完整进化过程。
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import TrendCharts from '@/components/evolution/TrendCharts';
import VersionTimeline from '@/components/evolution/VersionTimeline';
import {
  useOptimizerStats,
  useEvolutionHistory,
  useParamHistory,
  OptimizerStatsData,
  EvolutionHistoryItem,
  ParamHistoryItem,
  formatDateTimeBJT,
  gradeColor,
  statusLabel,
} from '@/lib/hooks';
import { triggerEvolveNow, fetchAllParams, fetchParamSnapshots } from '@/lib/api';

/* ─── 参数类型定义 ─── */
interface ParamDef {
  value: number;
  min: number;
  max: number;
  step: number;
  type: string;
  group: string;
  description: string;
  optimizable: boolean;
}

interface ParamsResponse {
  params: Record<string, ParamDef>;
}

interface SnapshotEntry {
  filename: string;
  version: number;
  timestamp: number;
  label: string;
}

/* ─── 参数组中文名 & 图标 ─── */
const GROUP_META: Record<string, { label: string; icon: string; color: string; desc: string }> = {
  weights: { label: '特征权重', icon: '⚖️', color: '#448aff', desc: 'CVD/OFI/VPIN 等 14 个特征的综合权重' },
  signal_thresholds: { label: '信号阈值', icon: '📡', color: '#00e676', desc: '买入/卖出/退出信号的触发阈值' },
  regime_multipliers: { label: '环境乘数', icon: '🌊', color: '#d500f9', desc: '趋势/震荡/突破/极端行情的微观/宏观乘数' },
  gate: { label: '入场门卫', icon: '🚧', color: '#f59e0b', desc: '置信度/对齐度/止损/时间过滤等入场条件' },
  detector: { label: '异常检测', icon: '🔍', color: '#ff1744', desc: 'VPIN/资金费率/OI/恐慌贪婪等异常阈值' },
  features: { label: '特征参数', icon: '📐', color: '#60a5fa', desc: 'VPIN桶/大单窗口/OFI层数等计算参数' },
  feature: { label: '高级特征', icon: '🧮', color: '#a78bfa', desc: '吸筹检测/量价分布/挂单墙等高级特征' },
  paper: { label: '纸盘交易', icon: '📝', color: '#4dd0e1', desc: '杠杆/仓位/止损/止盈/滑点/手续费等模拟参数' },
  confidence: { label: '置信度', icon: '🎯', color: '#ff9800', desc: '多空阈值/主导加成/异常惩罚等置信计算' },
  scorer: { label: '评分引擎', icon: '⚙️', color: '#e0e0e0', desc: '58 个子评分器的灵敏度/权重/阈值参数' },
};

/* ─── Tab 定义 ─── */
type TabId = 'overview' | 'params' | 'history';
const TABS: { id: TabId; label: string; icon: string }[] = [
  { id: 'overview', label: '系统总览', icon: '🧬' },
  { id: 'params', label: '参数全景', icon: '📊' },
  { id: 'history', label: '进化历史', icon: '📜' },
];

/* ─── 通用子组件 ─── */

function StatCard({
  icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
  color: string;
}) {
  return (
    <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4 sm:p-5">
      <div className="flex items-center gap-2 mb-2.5">
        <span className="text-xl">{icon}</span>
        <span className="text-xs text-text-tertiary uppercase tracking-wider font-medium">{label}</span>
      </div>
      <div className="text-2xl md:text-3xl font-bold" style={{ color }}>
        {value}
      </div>
      <div className="text-sm text-text-tertiary mt-2">{sub}</div>
    </div>
  );
}

function MiniStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="rounded-lg bg-surface-1 border border-surface-3/50 px-3.5 py-3">
      <div className="text-xs text-text-tertiary">{label}</div>
      <div className="text-xl font-mono font-bold mt-1" style={{ color }}>
        {value}
      </div>
    </div>
  );
}

function CycleStatusBadge({ status }: { status: string }) {
  const config: Record<string, { bg: string; text: string }> = {
    success: { bg: 'bg-bull/15', text: 'text-bull' },
    completed: { bg: 'bg-info/15', text: 'text-info' },
    running: { bg: 'bg-info/15', text: 'text-info' },
    failed: { bg: 'bg-bear/15', text: 'text-bear' },
    skipped: { bg: 'bg-surface-3', text: 'text-text-tertiary' },
    pending_approval: { bg: 'bg-warn/15', text: 'text-warn' },
  };
  const c = config[status] || { bg: 'bg-surface-3', text: 'text-text-tertiary' };
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-sm font-semibold ${c.bg} ${c.text}`}>
      {statusLabel(status)}
    </span>
  );
}

/* ─── 主组件 ─── */

export default function EvolutionPage() {
  const [activeTab, setActiveTab] = useState<TabId>('overview');
  const [busy, setBusy] = useState<'' | 'evolve'>('');
  const [error, setError] = useState('');

  // 综合数据
  const { data: stats, loading: statsLoading } = useOptimizerStats(10000);
  const { data: historyData, loading: historyLoading } = useEvolutionHistory(15000);
  const { data: paramData } = useParamHistory(30000);

  const history = historyData?.history ?? [];
  const paramHistory = paramData?.history ?? [];

  const handleEvolveNow = useCallback(async () => {
    setBusy('evolve');
    setError('');
    try {
      await triggerEvolveNow();
    } catch (e) {
      setError(e instanceof Error ? e.message : '触发失败');
    } finally {
      setBusy('');
    }
  }, []);

  return (
    <div className="min-h-screen bg-surface-0">
      {/* 头部 */}
      <header className="sticky top-0 z-50 border-b border-surface-3/50 bg-surface-0/80 backdrop-blur-xl">
        <div className="mx-auto max-w-[1920px] px-3 sm:px-6 lg:px-8">
          <div className="flex h-14 items-center justify-between">
            <div className="flex items-center gap-3">
              <Link
                href="/"
                className="flex items-center gap-2 text-text-tertiary hover:text-text-secondary transition-colors"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
                </svg>
                <span className="text-xs">返回驾驶舱</span>
              </Link>
              <div className="h-4 w-px bg-surface-3/50" />
              <div className="flex items-center gap-2">
                <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-info to-anomaly flex items-center justify-center">
                  <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6" />
                  </svg>
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-base sm:text-lg font-semibold tracking-tight">进化看板</span>
                    {stats?.scheduler && (
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-semibold ${
                        stats.scheduler.background_active
                          ? 'bg-bull/15 text-bull'
                          : 'bg-surface-3 text-text-tertiary'
                      }`}>
                        {stats.scheduler.background_active ? '运行中' : '待启动'}
                      </span>
                    )}
                  </div>
                  <span className="hidden sm:block text-xs text-text-tertiary">
                    参数优化 · AI 评估 · 自动进化（北京时间）
                  </span>
                </div>
              </div>
            </div>
            {/* 操作按钮 */}
            <div className="flex items-center gap-2">
              <button
                onClick={handleEvolveNow}
                disabled={busy !== ''}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-all bg-info/15 text-info border border-info/30 hover:bg-info/25 disabled:opacity-50"
              >
                {busy === 'evolve' ? (
                  <span className="animate-pulse">触发中...</span>
                ) : (
                  <>🧬 立即进化</>
                )}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* 主内容 */}
      <main className="mx-auto max-w-[1920px] px-3 sm:px-4 lg:px-6 py-4 sm:py-6 space-y-5">
        {/* 错误提示 */}
        {error && (
          <div className="rounded-xl bg-bear/10 border border-bear/30 px-4 py-3 text-sm text-bear">
            ⚠ {error}
          </div>
        )}

        {/* Tab 切换 */}
        <div className="flex gap-1 p-1.5 rounded-xl bg-surface-1 border border-surface-3/50">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm sm:text-base font-medium transition-all ${
                activeTab === tab.id
                  ? 'bg-info/15 text-info border border-info/30'
                  : 'text-text-tertiary hover:text-text-secondary border border-transparent'
              }`}
            >
              <span>{tab.icon}</span>
              <span>{tab.label}</span>
            </button>
          ))}
        </div>

        {/* Tab 内容 */}
        {activeTab === 'overview' && (
          <OverviewTab stats={stats} statsLoading={statsLoading} history={history} paramHistory={paramHistory} />
        )}
        {activeTab === 'params' && <ParamsTab />}
        {activeTab === 'history' && (
          <HistoryTab history={history} loading={historyLoading} />
        )}
      </main>
    </div>
  );
}

/* ══════════════════════════════════════════
   Tab 1: 系统总览
   ══════════════════════════════════════════ */

function OverviewTab({
  stats,
  statsLoading,
  history,
  paramHistory,
}: {
  stats: OptimizerStatsData | null;
  statsLoading: boolean;
  history: EvolutionHistoryItem[];
  paramHistory: ParamHistoryItem[];
}) {
  const registry = stats?.registry;
  const scheduler = stats?.scheduler;
  const agent = stats?.agent;
  const dataInfo = stats?.data;

  const minSamples = scheduler?.config?.min_samples ?? 30;
  const currentSamples = dataInfo?.records_with_1h ?? 0;
  const progressPct = minSamples > 0 ? Math.min((currentSamples / minSamples) * 100, 100) : 0;

  // 统计
  const successHistory = history.filter(
    (h) => h.status === 'success' || h.status === 'completed' || h.status === 'pending_approval'
  );
  const appliedCount = history.filter((h) => h.applied).length;
  const totalSignals = dataInfo?.total_records ?? 0;

  return (
    <div className="space-y-5 animate-fade-in">
      {/* 4 状态卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          icon="📦"
          label="参数版本"
          value={`v${registry?.version ?? 0}`}
          sub={`${registry?.total_params ?? 0} 个参数 · ${registry?.snapshots_count ?? 0} 个快照`}
          color="#448aff"
        />
        <StatCard
          icon={scheduler?.background_active ? '🟢' : '🔴'}
          label="调度器"
          value={scheduler?.background_active ? '运行中' : '已停止'}
          sub={
            scheduler?.background_active
              ? `${scheduler.trigger_mode === 'sample_driven' ? '样本驱动' : '定时'} · 每${Math.round((scheduler.config?.check_interval_s ?? 300) / 60)}分钟`
              : '等待启动'
          }
          color={scheduler?.background_active ? '#00e676' : '#ff1744'}
        />
        <StatCard
          icon="🔄"
          label="已完成优化"
          value={String(successHistory.length)}
          sub={`${appliedCount} 次应用 · ${totalSignals} 条信号`}
          color="#00e676"
        />
        <StatCard
          icon="🤖"
          label="AI Agent"
          value={agent?.api_key_set ? agent.provider || '已配置' : '未配置'}
          sub={
            agent?.api_key_set
              ? `${agent.calls_today}/${agent.max_calls_per_day} 调用`
              : '设置 API Key 后启用'
          }
          color={agent?.api_key_set ? '#d500f9' : '#555570'}
        />
      </div>

      {/* 数据积累进度条 */}
      <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4 sm:p-5">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold text-text-primary">📊 数据积累进度</span>
            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-md text-xs font-semibold ${
              progressPct >= 100
                ? 'bg-bull/15 text-bull'
                : 'bg-info/15 text-info'
            }`}>
              {progressPct >= 100 ? '样本已就绪' : '采集中'}
            </span>
          </div>
          <span className="text-sm text-text-tertiary font-mono">
            {currentSamples} / {minSamples} 已验证信号
          </span>
        </div>
        <div className="h-2.5 sm:h-3 bg-surface-3/50 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-600 ease-out"
            style={{
              width: `${Math.min(progressPct, 100)}%`,
              background: progressPct >= 100
                ? '#00e676'
                : 'linear-gradient(90deg, #448aff, #00e676)',
            }}
          />
        </div>
        <div className="flex justify-between mt-2">
          <span className="text-xxs text-text-tertiary">
            总记录: {dataInfo?.total_records ?? 0} · 日期跨度: {dataInfo?.date_range_days ?? 0} 天
          </span>
          <span className="text-xxs text-text-secondary font-mono">{progressPct.toFixed(0)}%</span>
        </div>
        {/* 数据质量问题提示 */}
        {(dataInfo?.issues?.length ?? 0) > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {dataInfo!.issues.map((issue, idx) => (
              <span key={idx} className="text-xxs px-2 py-0.5 rounded bg-warn/10 text-warn">
                ⚠ {issue}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 最近周期 + 参数注册表 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {/* 最近进化周期 */}
        <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
          <div className="text-xxs font-semibold text-text-tertiary uppercase tracking-wider mb-3">
            最近进化周期
          </div>
          {history.length > 0 ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <CycleStatusBadge status={history[0].status} />
                <span className="font-mono text-sm text-text-secondary truncate">
                  {history[0].cycle_id}
                </span>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-tertiary">
                <span>⏱ {formatDateTimeBJT(history[0].started_at)}</span>
                <span>
                  {history[0].applied ? (
                    <span className="text-bull">✅ 已应用</span>
                  ) : (
                    <span className="text-text-tertiary">⏸ 未应用</span>
                  )}
                </span>
                <span>耗时 {history[0].elapsed_s.toFixed(1)}s</span>
              </div>
              {history[0].ai_summary && (
                <div className="text-xs text-text-secondary bg-surface-2/50 rounded-lg px-3 py-2 mt-2">
                  {history[0].ai_summary}
                </div>
              )}
            </div>
          ) : (
            <div className="py-6 text-center">
              <div className="text-3xl mb-2 opacity-20">🧬</div>
              <div className="text-sm text-text-tertiary">暂无进化记录</div>
              <div className="text-xxs text-text-tertiary mt-1">积累足够数据后将自动触发</div>
            </div>
          )}
        </div>

        {/* 参数注册表状态 */}
        <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
          <div className="text-xxs font-semibold text-text-tertiary uppercase tracking-wider mb-3">
            参数注册表
          </div>
          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">版本</span>
              <span className="font-mono font-bold text-info">v{registry?.version ?? 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">参数总数</span>
              <span className="font-mono text-text-primary">{registry?.total_params ?? 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">历史版本</span>
              <span className="font-mono text-text-primary">{registry?.history_count ?? 0}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">更新时间</span>
              <span className="text-xs text-text-tertiary">
                {formatDateTimeBJT(registry?.updated_at || '')}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-text-secondary">调度统计</span>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-bull">{scheduler?.stats?.success ?? 0} 成功</span>
                <span className="text-bear">{scheduler?.stats?.failed ?? 0} 失败</span>
                <span className="text-text-tertiary">{scheduler?.stats?.skipped ?? 0} 跳过</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 趋势图 */}
      <TrendCharts />

      {/* 最近 5 轮进化快照 */}
      {history.length > 0 && (
        <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
          <div className="text-xxs font-semibold text-text-tertiary uppercase tracking-wider mb-3">
            最近进化快照（前 5 轮）
          </div>
          <div className="space-y-2">
            {history.slice(0, 5).map((h, idx) => (
              <div
                key={h.cycle_id || `snap-${idx}`}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-surface-2/30 border border-surface-3/30 transition-colors hover:bg-surface-2/50"
              >
                <CycleStatusBadge status={h.status} />
                <span className="font-mono text-xs text-text-secondary truncate max-w-[180px]">
                  {h.cycle_id}
                </span>
                <span className="text-xs text-text-tertiary hidden sm:inline">
                  {formatDateTimeBJT(h.started_at)}
                </span>
                <div className="flex-1" />
                {/* AI 评级 */}
                <span className={`text-sm font-bold ${gradeColor(h.ai_grade)}`}>
                  {h.ai_grade || '--'}
                </span>
                {/* 验证得分 */}
                <span className="text-xs font-mono text-text-secondary">
                  {h.validation_passed ? `${(h.validation_score * 100).toFixed(0)}%` : '--'}
                </span>
                {/* 耗时 */}
                <span className="text-xs font-mono text-text-tertiary">
                  {h.elapsed_s.toFixed(1)}s
                </span>
                {/* 应用状态 */}
                {h.applied ? (
                  <span className="text-xs text-bull">✅</span>
                ) : (
                  <span className="text-xs text-text-tertiary">—</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 参数变更时间线 */}
      {paramHistory.length > 0 && (
        <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
          <div className="text-sm font-semibold text-text-primary mb-4">📋 参数变更时间线</div>
          <div className="relative pl-6 border-l-2 border-surface-3/50">
            {paramHistory.slice(0, 10).map((h, idx) => (
              <div key={idx} className="relative pb-4" style={{ minHeight: 48 }}>
                <div
                  className="absolute rounded-full"
                  style={{
                    left: -23,
                    top: 4,
                    width: 12,
                    height: 12,
                    background: h.changes_count > 0 ? '#448aff' : '#555570',
                    border: '2px solid #06060b',
                  }}
                />
                <div className="ml-2">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xxs font-semibold bg-info/15 text-info">
                      v{h.version}
                    </span>
                    <span className="text-xs font-mono text-text-tertiary">
                      {formatDateTimeBJT(h.timestamp_iso)}
                    </span>
                    <span className="text-xs text-text-secondary">{h.source}</span>
                  </div>
                  <div className="text-xs text-text-secondary mt-1">
                    {h.label}
                    {h.changes_count > 0 && (
                      <span className="text-bull ml-2">{h.changes_count} 个参数变更</span>
                    )}
                  </div>
                  {/* 变更详情 */}
                  {h.changes_count > 0 && Object.keys(h.changes).length > 0 && (
                    <div className="mt-2 space-y-1">
                      {Object.entries(h.changes).slice(0, 3).map(([param, change]) => (
                        <div key={param} className="flex items-center gap-2 text-xxs font-mono">
                          <span className="text-text-tertiary truncate max-w-[160px]">{param}</span>
                          <span className="text-bear">{typeof change.old === 'number' ? change.old.toFixed(4) : String(change.old)}</span>
                          <span className="text-text-tertiary">→</span>
                          <span className="text-bull">{typeof change.new === 'number' ? change.new.toFixed(4) : String(change.new)}</span>
                        </div>
                      ))}
                      {Object.keys(h.changes).length > 3 && (
                        <div className="text-xxs text-text-tertiary">
                          ...还有 {Object.keys(h.changes).length - 3} 个变更
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 版本时间线（调度器历史 + 参数匹配） */}
      <VersionTimeline />
    </div>
  );
}

/* ══════════════════════════════════════════
   Tab 2: 进化历史
   ══════════════════════════════════════════ */

function HistoryTab({
  history,
  loading,
}: {
  history: EvolutionHistoryItem[];
  loading: boolean;
}) {
  if (loading && history.length === 0) {
    return (
      <div className="rounded-xl bg-surface-1 border border-surface-3/50 text-center py-12 text-text-tertiary text-sm animate-pulse">
        加载进化历史...
      </div>
    );
  }

  if (history.length === 0) {
    return (
      <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-12 text-center">
        <div className="text-4xl mb-3 opacity-20">📜</div>
        <div className="text-sm text-text-tertiary">暂无进化记录</div>
        <div className="text-xs text-text-tertiary mt-2">
          启动调度器或手动触发一轮进化后，历史记录将在此显示
        </div>
      </div>
    );
  }

  // 统计摘要
  const totalCycles = history.length;
  const completedCount = history.filter(
    (h) => h.status === 'success' || h.status === 'completed' || h.status === 'pending_approval'
  ).length;
  const appliedCount = history.filter((h) => h.applied).length;
  const avgValidation =
    completedCount > 0
      ? (
          history
            .filter((h) => h.status === 'success' || h.status === 'completed' || h.status === 'pending_approval')
            .reduce((s, h) => s + h.validation_score, 0) / completedCount * 100
        ).toFixed(1)
      : '0';
  const avgDuration =
    totalCycles > 0
      ? (history.reduce((s, h) => s + h.elapsed_s, 0) / totalCycles).toFixed(1)
      : '0';

  return (
    <div className="space-y-4 animate-fade-in">
      {/* 统计摘要 */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <MiniStat label="总周期" value={String(totalCycles)} color="#f0f0f5" />
        <MiniStat label="完成" value={String(completedCount)} color="#00e676" />
        <MiniStat label="已应用" value={String(appliedCount)} color="#448aff" />
        <MiniStat label="平均验证得分" value={`${avgValidation}%`} color="#d500f9" />
        <MiniStat label="平均耗时" value={`${avgDuration}s`} color="#8888a0" />
      </div>

      {/* 历史表格（桌面） */}
      <div className="hidden sm:block rounded-xl bg-surface-1 border border-surface-3/50 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-surface-3/50">
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">周期 ID</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">时间</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">状态</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">AI评级</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">验证得分</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">信号数</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">应用</th>
              <th className="text-left px-4 py-3 text-xxs text-text-tertiary uppercase tracking-wider font-semibold">耗时</th>
            </tr>
          </thead>
          <tbody>
            {history.map((h, idx) => (
              <tr
                key={h.cycle_id || `h-${idx}`}
                className="border-b border-surface-3/30 hover:bg-surface-2/30 transition-colors"
              >
                <td className="px-4 py-3">
                  <span className="font-mono text-xs text-text-secondary truncate block max-w-[180px]">
                    {h.cycle_id}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-text-secondary">
                  {formatDateTimeBJT(h.started_at)}
                </td>
                <td className="px-4 py-3">
                  <CycleStatusBadge status={h.status} />
                </td>
                <td className="px-4 py-3">
                  <span className={`text-sm font-bold ${gradeColor(h.ai_grade)}`}>
                    {h.ai_grade || '--'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`font-mono font-semibold ${h.validation_passed ? 'text-info' : 'text-text-tertiary'}`}>
                    {h.validation_passed ? `${(h.validation_score * 100).toFixed(0)}%` : '--'}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="font-mono text-text-secondary">{h.total_signals}</span>
                </td>
                <td className="px-4 py-3">
                  {h.applied ? (
                    <span className="text-xs text-bull font-semibold">已应用</span>
                  ) : (
                    <span className="text-xs text-text-tertiary">未应用</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <span className="font-mono text-xs text-text-secondary">{h.elapsed_s.toFixed(1)}s</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 移动端卡片列表 */}
      <div className="sm:hidden space-y-3">
        {history.map((h, idx) => (
          <div
            key={h.cycle_id || `mc-${idx}`}
            className={`rounded-xl bg-surface-1 border border-surface-3/50 border-l-3 p-4 space-y-3 ${
              h.status === 'success'
                ? 'border-l-bull'
                : h.status === 'completed'
                  ? 'border-l-info'
                  : h.status === 'failed'
                    ? 'border-l-bear'
                    : 'border-l-surface-3'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="font-mono text-sm text-text-secondary truncate max-w-[180px]">
                {h.cycle_id}
              </span>
              <CycleStatusBadge status={h.status} />
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-text-tertiary text-xs">时间</span>
                <div className="text-text-secondary mt-0.5">{formatDateTimeBJT(h.started_at)}</div>
              </div>
              <div>
                <span className="text-text-tertiary text-xs">AI评级</span>
                <div className={`font-bold text-lg mt-0.5 ${gradeColor(h.ai_grade)}`}>{h.ai_grade || '--'}</div>
              </div>
              <div>
                <span className="text-text-tertiary text-xs">验证得分</span>
                <div className="text-text-secondary font-mono mt-0.5">
                  {h.validation_passed ? `${(h.validation_score * 100).toFixed(0)}%` : '--'}
                </div>
              </div>
              <div>
                <span className="text-text-tertiary text-xs">应用</span>
                <div className={`mt-0.5 font-medium ${h.applied ? 'text-bull' : 'text-text-tertiary'}`}>
                  {h.applied ? '已应用' : '未应用'}
                </div>
              </div>
            </div>
            {h.ai_summary && (
              <div className="text-xs text-text-tertiary bg-surface-2/50 rounded-lg px-3 py-2">
                {h.ai_summary}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════
   Tab: 参数全景
   ══════════════════════════════════════════ */

function ParamsTab() {
  const [paramsData, setParamsData] = useState<ParamsResponse | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const [p, s] = await Promise.all([
          fetchAllParams(),
          fetchParamSnapshots().catch(() => []),
        ]);
        setParamsData(p);
        setSnapshots(Array.isArray(s) ? s : []);
      } catch {
        /* 静默 */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const allParams = paramsData?.params ?? {};

  // 提取排序后的组名
  const groups = useMemo(() => {
    const groupSet = new Set<string>();
    for (const def of Object.values(allParams)) {
      groupSet.add((def as ParamDef).group);
    }
    return Array.from(groupSet).sort();
  }, [allParams]);

  // 按组分类
  const groupedParams = useMemo(() => {
    const map: Record<string, [string, ParamDef][]> = {};
    for (const [name, def] of Object.entries(allParams) as [string, ParamDef][]) {
      const g = def.group;
      if (!map[g]) map[g] = [];
      map[g].push([name, def]);
    }
    for (const g of Object.keys(map)) {
      map[g].sort((a, b) => a[0].localeCompare(b[0]));
    }
    return map;
  }, [allParams]);

  // 搜索过滤组
  const filteredGroups = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q && !activeGroup) return groups;
    if (activeGroup) return [activeGroup];
    return groups.filter((g) => {
      const meta = GROUP_META[g];
      if (g.toLowerCase().includes(q)) return true;
      if (meta?.label.toLowerCase().includes(q)) return true;
      return (groupedParams[g] || []).some(
        ([name, def]) => name.toLowerCase().includes(q) || def.description.toLowerCase().includes(q),
      );
    });
  }, [search, activeGroup, groups, groupedParams]);

  // 搜索时过滤参数
  const getFilteredParams = useCallback(
    (group: string) => {
      const params = groupedParams[group] || [];
      const q = search.toLowerCase().trim();
      if (!q) return params;
      return params.filter(
        ([name, def]) => name.toLowerCase().includes(q) || def.description.toLowerCase().includes(q),
      );
    },
    [groupedParams, search],
  );

  const totalParams = Object.keys(allParams).length;
  const optimizableCount = Object.values(allParams).filter((p) => (p as ParamDef).optimizable).length;

  if (loading) {
    return (
      <div className="rounded-xl bg-surface-1 border border-surface-3/50 text-center py-12 text-text-tertiary text-sm animate-pulse">
        加载参数数据...
      </div>
    );
  }

  return (
    <div className="space-y-5 animate-fade-in">
      {/* 顶部摘要 */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-semibold bg-info/15 text-info border border-info/30">
          {totalParams} 个参数
        </span>
        <span className="text-xs text-text-tertiary">
          {optimizableCount} 个可优化 · {groups.length} 组
        </span>
      </div>

      {/* 搜索 + 组筛选 */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1" style={{ minWidth: 200, maxWidth: 360 }}>
          <input
            type="text"
            placeholder="搜索参数名、描述..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setActiveGroup(null); }}
            className="w-full pl-9 pr-3 py-2 rounded-lg bg-surface-1 border border-surface-3/50 text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-info/50"
          />
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary text-sm">🔍</span>
        </div>
        <button
          onClick={() => { setActiveGroup(null); setSearch(''); }}
          className={`text-xs px-2.5 py-1.5 rounded-lg transition-all border ${
            !activeGroup
              ? 'bg-info/15 text-info border-info/30'
              : 'text-text-tertiary border-surface-3/50 hover:text-text-secondary'
          }`}
        >
          全部
        </button>
        {groups.map((g) => {
          const meta = GROUP_META[g];
          const isActive = activeGroup === g;
          return (
            <button
              key={g}
              onClick={() => { setActiveGroup(isActive ? null : g); setSearch(''); }}
              className={`text-xs px-2.5 py-1.5 rounded-lg transition-all border ${
                isActive
                  ? 'border-current/30'
                  : 'text-text-tertiary border-surface-3/50 hover:text-text-secondary'
              }`}
              style={isActive ? { color: meta?.color || '#448aff', background: `${meta?.color || '#448aff'}15` } : undefined}
            >
              {meta?.icon || '📦'} {meta?.label || g}
            </button>
          );
        })}
      </div>

      {/* 参数组卡片 */}
      {filteredGroups.map((group) => {
        const meta = GROUP_META[group] || { label: group, icon: '📦', color: '#8888a0', desc: '' };
        const params = getFilteredParams(group);
        const groupTotal = (groupedParams[group] || []).length;
        const groupOptimizable = (groupedParams[group] || []).filter(([, d]) => d.optimizable).length;
        const isExpanded = expandedGroup === group || !!search || !!activeGroup;

        return (
          <div key={group} className="rounded-xl bg-surface-1 border border-surface-3/50 overflow-hidden">
            {/* 组头 */}
            <button
              onClick={() => setExpandedGroup(isExpanded && !search && !activeGroup ? null : group)}
              className="w-full text-left px-4 py-3.5 flex items-center gap-3 transition-colors hover:bg-surface-2/30"
            >
              <span className="text-lg">{meta.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold" style={{ color: meta.color }}>
                    {meta.label}
                  </span>
                  <span className="font-mono text-xs text-text-tertiary">{group}</span>
                </div>
                <div className="text-xs text-text-tertiary mt-0.5">{meta.desc}</div>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs font-mono text-text-secondary">{groupTotal} 参数</span>
                <span className={`text-xs ${groupOptimizable > 0 ? 'text-bull' : 'text-text-tertiary'}`}>
                  {groupOptimizable} 可优化
                </span>
                <svg
                  width="16" height="16" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                  className="text-text-tertiary flex-shrink-0 transition-transform duration-200"
                  style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)' }}
                >
                  <path d="M6 9l6 6 6-6" />
                </svg>
              </div>
            </button>

            {/* 参数列表 */}
            {isExpanded && params.length > 0 && (
              <div className="border-t border-surface-3/50">
                {params.map(([name, def]) => (
                  <ParamRow key={name} name={name} def={def} groupColor={meta.color} />
                ))}
              </div>
            )}
            {isExpanded && params.length === 0 && (
              <div className="px-4 py-6 text-center text-xs text-text-tertiary border-t border-surface-3/50">
                无匹配参数
              </div>
            )}
          </div>
        );
      })}

      {/* 快照列表 */}
      {snapshots.length > 0 && (
        <div className="rounded-xl bg-surface-1 border border-surface-3/50 p-4">
          <div className="text-sm font-semibold text-text-primary mb-3">
            💾 参数快照（{snapshots.length} 个）
          </div>
          <div className="space-y-1.5">
            {snapshots.map((s, idx) => (
              <div
                key={idx}
                className="flex items-center gap-3 px-3 py-2 rounded-lg text-xs bg-surface-2/30 border border-surface-3/30"
              >
                <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xxs font-semibold bg-info/15 text-info">
                  v{s.version}
                </span>
                <span className="text-text-secondary">{s.label}</span>
                <div className="flex-1" />
                <span className="font-mono text-text-tertiary">
                  {formatDateTimeBJT(new Date(s.timestamp * 1000).toISOString())}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── 单个参数行（带范围可视化） ─── */
function ParamRow({ name, def, groupColor }: { name: string; def: ParamDef; groupColor: string }) {
  const range = def.max - def.min;
  const pct = range > 0 ? ((def.value - def.min) / range) * 100 : 50;
  const displayValue =
    def.type === 'int'
      ? String(Math.round(def.value))
      : def.value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 border-b border-surface-3/30 last:border-b-0 transition-colors hover:bg-surface-2/20">
      {/* 可优化标记 */}
      <div
        className="flex-shrink-0 rounded-full"
        style={{
          width: 6,
          height: 6,
          background: def.optimizable ? '#00e676' : '#555570',
          opacity: def.optimizable ? 1 : 0.3,
        }}
        title={def.optimizable ? '可优化' : '固定参数'}
      />

      {/* 参数名 + 描述 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-semibold text-text-primary">
            {name}
          </span>
          <span className="text-xs text-text-tertiary truncate">{def.description}</span>
        </div>
        {/* 范围条 */}
        <div className="flex items-center gap-2 mt-1.5">
          <span className="text-xxs font-mono text-text-tertiary" style={{ width: 48, textAlign: 'right' }}>
            {def.min}
          </span>
          <div className="relative flex-1" style={{ height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.06)', maxWidth: 200 }}>
            <div
              style={{
                height: '100%',
                borderRadius: 2,
                width: `${Math.min(Math.max(pct, 2), 100)}%`,
                background: groupColor,
                opacity: 0.5,
                transition: 'width 0.3s',
              }}
            />
            <div
              style={{
                position: 'absolute',
                top: -3,
                left: `${Math.min(Math.max(pct, 1), 99)}%`,
                width: 10,
                height: 10,
                borderRadius: '50%',
                background: groupColor,
                border: '2px solid #06060b',
                transform: 'translateX(-50%)',
              }}
            />
          </div>
          <span className="text-xxs font-mono text-text-tertiary" style={{ width: 48 }}>
            {def.max}
          </span>
        </div>
      </div>

      {/* 当前值 */}
      <div className="text-right" style={{ minWidth: 70 }}>
        <div className="font-mono text-sm font-bold" style={{ color: groupColor }}>
          {displayValue}
        </div>
        <div className="text-xxs text-text-tertiary">{def.type}</div>
      </div>
    </div>
  );
}
