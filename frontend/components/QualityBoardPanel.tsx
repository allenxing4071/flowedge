/**
 * 质量看板面板 — FlowEdge 四层门卫框架健康诊断
 *
 * 学习自 KKline 质量看板（.skills/质量看板/SKILL.md），适配为：
 *   - 四层门卫漏斗（regime → location → behavior → direction）
 *   - 方向分布（LONG/SHORT/NEUTRAL + 偏见告警）
 *   - 市场环境分布
 *   - 拒绝原因 Top N
 *   - 动态止损/止盈统计
 *   - 纸盘交易表现对比（门卫动态 vs 固定配置）
 */

'use client';

import { useCallback } from 'react';
import { usePolling } from '@/lib/hooks';
import { fetchQualityBoard } from '@/lib/api';

// ══════════════════════════════════════════
// 类型定义
// ══════════════════════════════════════════

interface GateFunnel {
  total_evaluations: number;
  regime_passed: number;
  location_passed: number;
  behavior_passed: number;
  direction_passed: number;
  final_passed: number;
  final_pass_rate: number;
  rejected_at: Record<string, number>;
}

interface DirectionDist {
  LONG: number;
  SHORT: number;
  NEUTRAL: number;
  total: number;
  long_pct: number;
  short_pct: number;
  neutral_pct: number;
  bias_warning: string | null;
}

interface TradeStats {
  count: number;
  win_rate?: number;
  avg_pnl_pct?: number;
  total_pnl?: number;
}

interface TradePerformance {
  status?: string;
  message?: string;
  all_trades?: TradeStats;
  dynamic_sl_trades?: TradeStats;
  fixed_sl_trades?: TradeStats;
  exit_reasons?: Record<string, number>;
}

interface DynamicSlTp {
  count: number;
  avg_sl_pct: number;
  avg_tp_pct: number;
  min_sl_pct: number;
  max_sl_pct: number;
  min_tp_pct: number;
  max_tp_pct: number;
}

interface QualityBoardData {
  status: string;
  message?: string;
  gate_funnel?: GateFunnel;
  direction_distribution?: DirectionDist;
  regime_distribution?: Record<string, number>;
  top_reject_reasons?: { reason: string; count: number }[];
  dynamic_sl_tp?: DynamicSlTp;
  trade_performance?: TradePerformance;
  sample_size?: number;
  confidence_note?: string;
  time_range?: {
    first_ts: number;
    last_ts: number;
    duration_hours: number;
  };
}

// ══════════════════════════════════════════
// 工具函数
// ══════════════════════════════════════════

function fmtPct(v: number, digits = 1): string {
  return `${v.toFixed(digits)}%`;
}

function fmtNum(v: number): string {
  return v.toLocaleString('en-US');
}

// ══════════════════════════════════════════
// 子组件：漏斗柱
// ══════════════════════════════════════════

function FunnelBar({ label, value, total, color }: {
  label: string;
  value: number;
  total: number;
  color: string;
}) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-secondary">{label}</span>
        <span className="text-text-primary tabular-nums font-medium">
          {fmtNum(value)} <span className="text-text-tertiary">/ {fmtNum(total)}</span>
          <span className="text-text-tertiary ml-1">({fmtPct(pct)})</span>
        </span>
      </div>
      <div className="h-3 bg-surface-2 rounded-full overflow-hidden">
        <div
          className={`h-full ${color} rounded-full transition-all duration-700`}
          style={{ width: `${Math.max(pct, 0.5)}%` }}
        />
      </div>
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：方向分布饼
// ══════════════════════════════════════════

function DirectionBar({ dist }: { dist: DirectionDist }) {
  const bars = [
    { label: 'LONG', pct: dist.long_pct, count: dist.LONG, color: 'bg-bull' },
    { label: 'NEUTRAL', pct: dist.neutral_pct, count: dist.NEUTRAL, color: 'bg-text-tertiary/50' },
    { label: 'SHORT', pct: dist.short_pct, count: dist.SHORT, color: 'bg-bear' },
  ];

  return (
    <div className="space-y-3">
      {/* 堆叠条 */}
      <div className="h-6 flex rounded-full overflow-hidden bg-surface-2">
        {bars.map(b => (
          b.pct > 0 && (
            <div
              key={b.label}
              className={`${b.color} transition-all duration-500 flex items-center justify-center`}
              style={{ width: `${b.pct}%` }}
            >
              {b.pct > 8 && (
                <span className="text-xxs text-white font-medium">{fmtPct(b.pct, 0)}</span>
              )}
            </div>
          )
        ))}
      </div>

      {/* 图例 */}
      <div className="flex items-center justify-center gap-6 text-xs">
        {bars.map(b => (
          <div key={b.label} className="flex items-center gap-1.5">
            <div className={`w-2.5 h-2.5 rounded-sm ${b.color}`} />
            <span className="text-text-secondary">{b.label}</span>
            <span className="text-text-tertiary tabular-nums">({b.count})</span>
          </div>
        ))}
      </div>

      {/* 偏见告警 */}
      {dist.bias_warning && (
        <div className="text-xs text-warn bg-warn/10 rounded-lg px-3 py-2">
          {dist.bias_warning}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：小数字卡
// ══════════════════════════════════════════

function MiniCard({ label, value, sub, color }: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <div className="bg-surface-1 rounded-lg p-3 text-center">
      <div className="text-xxs text-text-tertiary mb-0.5">{label}</div>
      <div className={`text-lg font-bold tabular-nums ${color || 'text-text-primary'}`}>
        {value}
      </div>
      {sub && <div className="text-xxs text-text-tertiary mt-0.5">{sub}</div>}
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：交易表现对比
// ══════════════════════════════════════════

function TradeCompare({ perf }: { perf: TradePerformance }) {
  if (perf.status === 'no_trades' || perf.status === 'no_paper_trader') {
    return (
      <div className="text-xs text-text-tertiary text-center py-4">
        {perf.message || '暂无纸盘交易数据'}
      </div>
    );
  }

  const rows = [
    { label: '全部交易', data: perf.all_trades, color: 'text-info' },
    { label: '门卫动态止损', data: perf.dynamic_sl_trades, color: 'text-bull' },
    { label: '固定配置止损', data: perf.fixed_sl_trades, color: 'text-text-secondary' },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-surface-2">
            <th className="text-left py-2 text-text-tertiary font-medium">类型</th>
            <th className="text-right py-2 text-text-tertiary font-medium">交易数</th>
            <th className="text-right py-2 text-text-tertiary font-medium">胜率</th>
            <th className="text-right py-2 text-text-tertiary font-medium">平均盈亏</th>
            <th className="text-right py-2 text-text-tertiary font-medium">总盈亏</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const d = r.data;
            if (!d || d.count === 0) {
              return (
                <tr key={r.label} className="border-b border-surface-2/50">
                  <td className={`py-2 font-medium ${r.color}`}>{r.label}</td>
                  <td className="py-2 text-right text-text-tertiary" colSpan={4}>暂无数据</td>
                </tr>
              );
            }
            const wrColor = (d.win_rate ?? 0) >= 50 ? 'text-bull' : 'text-bear';
            const pnlColor = (d.total_pnl ?? 0) >= 0 ? 'text-bull' : 'text-bear';
            return (
              <tr key={r.label} className="border-b border-surface-2/50">
                <td className={`py-2 font-medium ${r.color}`}>{r.label}</td>
                <td className="py-2 text-right tabular-nums text-text-primary">{d.count}</td>
                <td className={`py-2 text-right tabular-nums ${wrColor}`}>
                  {d.win_rate != null ? fmtPct(d.win_rate) : '--'}
                </td>
                <td className={`py-2 text-right tabular-nums ${pnlColor}`}>
                  {d.avg_pnl_pct != null ? `${d.avg_pnl_pct >= 0 ? '+' : ''}${d.avg_pnl_pct.toFixed(2)}%` : '--'}
                </td>
                <td className={`py-2 text-right tabular-nums ${pnlColor}`}>
                  {d.total_pnl != null ? `$${d.total_pnl >= 0 ? '+' : ''}${d.total_pnl.toFixed(2)}` : '--'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* 退出原因分布 */}
      {perf.exit_reasons && Object.keys(perf.exit_reasons).length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(perf.exit_reasons)
            .sort((a, b) => b[1] - a[1])
            .map(([reason, count]) => {
              const exitColors: Record<string, string> = {
                stop_loss: 'bg-bear/15 text-bear',
                take_profit: 'bg-bull/15 text-bull',
                trailing_stop: 'bg-bull/15 text-bull',
                signal_reverse: 'bg-info/15 text-info',
                signal_neutral: 'bg-surface-2 text-text-secondary',
              };
              const cls = exitColors[reason] || 'bg-surface-2 text-text-tertiary';
              const labels: Record<string, string> = {
                stop_loss: '止损',
                take_profit: '止盈',
                trailing_stop: '追踪止盈',
                signal_reverse: '信号反转',
                signal_neutral: '回到中性',
              };
              return (
                <span key={reason} className={`px-2 py-1 rounded text-xxs ${cls}`}>
                  {labels[reason] || reason} {count}
                </span>
              );
            })}
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：市场环境分布
// ══════════════════════════════════════════

function RegimeDistribution({ regimes }: { regimes: Record<string, number> }) {
  const total = Object.values(regimes).reduce((a, b) => a + b, 0);
  if (total === 0) return null;

  const regimeColors: Record<string, string> = {
    trending: 'bg-bull/20 text-bull',
    ranging: 'bg-info/20 text-info',
    breakout: 'bg-warn/20 text-warn',
    extreme: 'bg-bear/20 text-bear',
  };

  const regimeLabels: Record<string, string> = {
    trending: '趋势',
    ranging: '震荡',
    breakout: '突破',
    extreme: '极端',
  };

  const items = Object.entries(regimes)
    .sort((a, b) => b[1] - a[1]);

  return (
    <div className="flex flex-wrap gap-2">
      {items.map(([regime, count]) => {
        const pct = (count / total * 100).toFixed(0);
        const cls = regimeColors[regime] || 'bg-surface-2 text-text-tertiary';
        const label = regimeLabels[regime] || regime;
        return (
          <div key={regime} className={`px-3 py-2 rounded-lg ${cls} flex flex-col items-center min-w-[70px]`}>
            <span className="text-xs font-medium">{label}</span>
            <span className="text-lg font-bold tabular-nums">{pct}%</span>
            <span className="text-xxs opacity-60">{count} 次</span>
          </div>
        );
      })}
    </div>
  );
}

// ══════════════════════════════════════════
// 主组件
// ══════════════════════════════════════════

export default function QualityBoardPanel() {
  const fetcher = useCallback(() => fetchQualityBoard(), []);
  const { data, error } = usePolling<QualityBoardData>(fetcher, 10000);

  // 无数据或加载中
  if (!data) {
    return (
      <div className="card p-6">
        <h3 className="text-sm font-semibold text-text-primary mb-4">质量看板</h3>
        <div className="text-xs text-text-tertiary text-center py-4">
          {error ? '连接失败' : '加载中...'}
        </div>
      </div>
    );
  }

  // 尚无门卫数据
  if (data.status === 'no_data') {
    return (
      <div className="card p-6">
        <h3 className="text-sm font-semibold text-text-primary mb-4">质量看板</h3>
        <div className="text-xs text-text-tertiary text-center py-8">
          <div className="text-2xl opacity-30 mb-2">&#128202;</div>
          {data.message || '等待门卫评估数据...'}
          <div className="text-xxs mt-1 opacity-60">
            信号引擎运行后，门卫评估数据将自动积累
          </div>
        </div>
      </div>
    );
  }

  const funnel = data.gate_funnel!;
  const dirDist = data.direction_distribution!;
  const regimeDist = data.regime_distribution || {};
  const rejects = data.top_reject_reasons || [];
  const sltp = data.dynamic_sl_tp;
  const tradePerf = data.trade_performance;
  const timeRange = data.time_range;

  return (
    <div className="space-y-6">

      {/* ── 标题栏 ── */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-text-primary">质量看板</h2>
          <p className="text-sm text-text-tertiary mt-0.5">
            四层门卫框架健康诊断
            {timeRange && timeRange.duration_hours > 0 && (
              <span className="ml-2">
                （最近 {timeRange.duration_hours}h · {fmtNum(data.sample_size || 0)} 次评估）
              </span>
            )}
          </p>
        </div>
        <div className="text-xxs text-text-tertiary px-2 py-1 bg-surface-1 rounded">
          {data.confidence_note}
        </div>
      </div>

      {/* ── 核心指标卡片 ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MiniCard
          label="门卫通过率"
          value={fmtPct(funnel.final_pass_rate)}
          sub={`${fmtNum(funnel.final_passed)} / ${fmtNum(funnel.total_evaluations)}`}
          color={funnel.final_pass_rate > 20 ? 'text-warn' : funnel.final_pass_rate > 5 ? 'text-info' : 'text-bull'}
        />
        <MiniCard
          label="LONG 占比"
          value={fmtPct(dirDist.long_pct)}
          sub={`${dirDist.LONG} 次`}
          color="text-bull"
        />
        <MiniCard
          label="SHORT 占比"
          value={fmtPct(dirDist.short_pct)}
          sub={`${dirDist.SHORT} 次`}
          color="text-bear"
        />
        <MiniCard
          label="评估总数"
          value={fmtNum(funnel.total_evaluations)}
          sub={timeRange ? `${timeRange.duration_hours}h` : undefined}
        />
      </div>

      {/* ── 四层门卫漏斗 ── */}
      <div className="card p-6 space-y-4">
        <h3 className="text-base font-semibold text-text-primary">四层门卫漏斗</h3>
        <p className="text-xxs text-text-tertiary">
          每次信号评估都经过四层过滤。以下展示各层的独立通过率和拒绝分布。
        </p>
        <FunnelBar
          label="L1 环境分类 (MarketRegime)"
          value={funnel.regime_passed}
          total={funnel.total_evaluations}
          color="bg-info"
        />
        <FunnelBar
          label="L2 位置过滤 (LocationFilter)"
          value={funnel.location_passed}
          total={funnel.total_evaluations}
          color="bg-bull/70"
        />
        <FunnelBar
          label="L3 行为确认 (BehaviorConfirm)"
          value={funnel.behavior_passed}
          total={funnel.total_evaluations}
          color="bg-warn"
        />
        <FunnelBar
          label="L4 方向确认 (DirectionConfirm)"
          value={funnel.direction_passed}
          total={funnel.total_evaluations}
          color="bg-bull"
        />
        <div className="border-t border-surface-2 pt-3">
          <FunnelBar
            label="最终通过（全部四层）"
            value={funnel.final_passed}
            total={funnel.total_evaluations}
            color="bg-gradient-to-r from-bull to-info"
          />
        </div>

        {/* 各层拒绝计数 */}
        {Object.values(funnel.rejected_at).some(v => v > 0) && (
          <div className="flex flex-wrap gap-2 pt-2">
            <span className="text-xxs text-text-tertiary">拒绝在:</span>
            {Object.entries(funnel.rejected_at)
              .filter(([, v]) => v > 0)
              .sort((a, b) => b[1] - a[1])
              .map(([layer, count]) => {
                const layerLabels: Record<string, string> = {
                  regime: 'L1环境', location: 'L2位置', behavior: 'L3行为', direction: 'L4方向',
                };
                return (
                  <span key={layer} className="px-2 py-0.5 rounded text-xxs bg-bear/10 text-bear">
                    {layerLabels[layer] || layer}: {count}
                  </span>
                );
              })}
          </div>
        )}
      </div>

      {/* ── 方向分布 ── */}
      <div className="card p-6 space-y-3">
        <h3 className="text-base font-semibold text-text-primary">方向分布</h3>
        <p className="text-xxs text-text-tertiary">
          LONG/SHORT 应基本均衡。单方向 &gt;70% 提示方向偏见。NEUTRAL &gt;95% 提示门卫过严。
        </p>
        <DirectionBar dist={dirDist} />
      </div>

      {/* ── 两列区域：环境分布 + 拒绝原因 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* 市场环境分布 */}
        <div className="card p-6 space-y-3">
          <h3 className="text-sm font-semibold text-text-primary">市场环境分布</h3>
          <p className="text-xxs text-text-tertiary">
            门卫 L1 层识别的市场状态分布
          </p>
          <RegimeDistribution regimes={regimeDist} />
        </div>

        {/* 拒绝原因 Top N */}
        <div className="card p-6 space-y-3">
          <h3 className="text-sm font-semibold text-text-primary">拒绝原因 Top</h3>
          {rejects.length > 0 ? (
            <div className="space-y-2">
              {rejects.slice(0, 8).map((r, i) => {
                const maxCount = rejects[0].count;
                const pct = maxCount > 0 ? (r.count / maxCount) * 100 : 0;
                return (
                  <div key={i} className="space-y-0.5">
                    <div className="flex items-center justify-between text-xxs">
                      <span className="text-text-secondary truncate max-w-[70%]">{r.reason}</span>
                      <span className="text-text-tertiary tabular-nums">{r.count}</span>
                    </div>
                    <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-bear/40 rounded-full"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-xs text-text-tertiary text-center py-4">暂无拒绝记录</div>
          )}
        </div>
      </div>

      {/* ── 动态止损/止盈统计 ── */}
      {sltp && sltp.count > 0 && (
        <div className="card p-6 space-y-3">
          <h3 className="text-sm font-semibold text-text-primary">门卫动态止损/止盈</h3>
          <p className="text-xxs text-text-tertiary">
            门卫通过的信号自动计算结构性止损/止盈（基于 VAH/VAL/HVN）
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <MiniCard
              label="平均止损"
              value={`${sltp.avg_sl_pct}%`}
              sub={`${sltp.min_sl_pct}% ~ ${sltp.max_sl_pct}%`}
              color="text-bear"
            />
            <MiniCard
              label="平均止盈"
              value={`${sltp.avg_tp_pct}%`}
              sub={`${sltp.min_tp_pct}% ~ ${sltp.max_tp_pct}%`}
              color="text-bull"
            />
            <MiniCard
              label="盈亏比"
              value={sltp.avg_sl_pct > 0 ? (sltp.avg_tp_pct / sltp.avg_sl_pct).toFixed(2) : '--'}
              sub="TP / SL"
              color="text-info"
            />
            <MiniCard
              label="通过信号数"
              value={fmtNum(sltp.count)}
            />
          </div>
        </div>
      )}

      {/* ── 纸盘交易表现对比 ── */}
      {tradePerf && (
        <div className="card p-6 space-y-3">
          <h3 className="text-sm font-semibold text-text-primary">交易表现对比</h3>
          <p className="text-xxs text-text-tertiary">
            门卫动态止损 vs 固定配置止损的实际表现
          </p>
          <TradeCompare perf={tradePerf} />
        </div>
      )}
    </div>
  );
}
