/**
 * 信号卡片 — 单币种信号概览 + 门卫状态
 *
 * v3.1 更新：内嵌四层门卫状态徽章，一眼看懂系统决策
 * - 顶部：币种 + 信号徽章
 * - 中部：大数字得分 + 门卫状态条
 * - 底部：关键指标 + 交易按钮
 */

'use client';

import {
  SymbolSignal,
  GateStatusItem,
  signalColor,
  signalLabel,
  riskColor,
  riskLabel,
  sideLabel,
  formatScore,
  formatTimestamp,
} from '@/lib/hooks';

interface Props {
  symbol: string;
  data: SymbolSignal;
  gate?: GateStatusItem | null;
  onClick?: (symbol: string) => void;
  onTrade?: (symbol: string, side: 'LONG' | 'SHORT') => void;
}

// 门卫层名称中文映射
const LAYER_LABELS: Record<string, string> = {
  MarketRegime: '环境',
  LocationFilter: '位置',
  BehaviorConfirm: '行为',
  DirectionConfirm: '方向',
};

// 从 regime detail 提取环境类型中文
function regimeLabel(detail: string): string {
  if (!detail) return '--';
  if (detail.includes('trending')) return '趋势';
  if (detail.includes('ranging')) return '震荡';
  if (detail.includes('breakout')) return '突破';
  if (detail.includes('extreme')) return '极端';
  return detail.split('_')[0] || '--';
}

function GateBadge({ gate }: { gate: GateStatusItem }) {
  const layers = [
    { key: 'regime', label: 'L1', result: gate.regime },
    { key: 'location', label: 'L2', result: gate.location },
    { key: 'behavior', label: 'L3', result: gate.behavior },
    { key: 'direction', label: 'L4', result: gate.direction },
  ];

  return (
    <div className="mt-2.5 sm:mt-3 space-y-1.5 sm:space-y-2">
      {/* 门卫总状态 */}
      <div className="flex items-center justify-between flex-wrap gap-1">
        <div className="flex items-center gap-1.5 sm:gap-2">
          <span className={`inline-flex items-center gap-1 px-1.5 sm:px-2 py-0.5 rounded text-xxs font-bold ${
            gate.passed
              ? 'bg-bull/15 text-bull'
              : 'bg-surface-2 text-text-tertiary'
          }`}>
            <span className="text-xxs sm:text-xs">{gate.passed ? '▶' : '■'}</span>
            {gate.passed ? `通过 -> ${sideLabel(gate.side)}` : '拒绝'}
          </span>
          {/* 环境标签 */}
          <span className="text-xxs text-text-tertiary px-1 sm:px-1.5 py-0.5 bg-surface-2 rounded">
            {regimeLabel(gate.regime.detail)}
          </span>
        </div>
        {/* 动态止损/止盈 */}
        {gate.passed && (
          <div className="flex items-center gap-1 sm:gap-1.5 text-xxs">
            <span className="text-bear tabular-nums">止损 {gate.suggested_stop_loss_pct}%</span>
            <span className="text-text-tertiary">/</span>
            <span className="text-bull tabular-nums">止盈 {gate.suggested_take_profit_pct}%</span>
          </div>
        )}
      </div>

      {/* 四层进度条 */}
      <div className="flex items-center gap-1">
        {layers.map((l) => (
          <div key={l.key} className="flex-1 group/layer relative">
            <div
              className={`h-1.5 rounded-full transition-all ${
                l.result.passed ? 'bg-bull/60' : 'bg-surface-3'
              }`}
            />
            <div className="flex items-center justify-center mt-0.5">
              <span className={`text-[9px] tabular-nums ${
                l.result.passed ? 'text-bull/70' : 'text-text-tertiary/50'
              }`}>
                {l.label}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* 拒绝原因（仅拒绝时显示） */}
      {!gate.passed && gate.reject_reason && (
        <div className="text-xxs text-text-tertiary truncate leading-relaxed">
          <span className="text-bear/70">{LAYER_LABELS[gate.reject_layer || ''] || gate.reject_layer}</span>
          <span className="mx-1">·</span>
          {gate.reject_reason}
        </div>
      )}
    </div>
  );
}

export default function SignalCard({ symbol, data, gate, onClick, onTrade }: Props) {
  const isPositive = data.score > 0;
  const scoreAbs = Math.abs(data.score);

  return (
    <div
      className={`card-hover p-4 sm:p-6 cursor-pointer group ${
        data.signal_changed ? 'animate-slide-up ring-1 ring-info/30' : ''
      }`}
      onClick={() => onClick?.(symbol)}
    >
      {/* 行 1：币种 + 信号徽章 */}
      <div className="flex items-center justify-between mb-3 sm:mb-4">
        <div className="flex items-center gap-2 sm:gap-3">
          <span className="text-lg sm:text-xl font-bold tracking-tight">{symbol}</span>
          {data.signal_changed && (
            <span className="text-xxs sm:text-xs text-info font-mono animate-fade-in">新信号</span>
          )}
        </div>
        <span className={`badge text-xs sm:text-sm px-2 sm:px-3 py-0.5 sm:py-1 ${
          data.signal.includes('BUY') ? 'badge-bull' :
          data.signal.includes('SELL') ? 'badge-bear' : 'badge-neutral'
        }`}>
          {signalLabel(data.signal)}
        </span>
      </div>

      {/* 行 2：得分 — 大数字核心视觉 */}
      <div className="mb-2 sm:mb-3">
        <div className={`text-2xl sm:text-3xl font-bold mono-num mb-1.5 sm:mb-2 ${signalColor(data.signal)}`}>
          {formatScore(data.score)}
        </div>
        <div className="factor-bar-container h-2.5 rounded-full">
          {isPositive ? (
            <div
              className="factor-bar-positive h-full rounded-r-full"
              style={{ width: `${scoreAbs * 50}%` }}
            />
          ) : (
            <div
              className="factor-bar-negative h-full rounded-l-full"
              style={{ width: `${scoreAbs * 50}%` }}
            />
          )}
          <div className="factor-bar-center" />
        </div>
      </div>

      {/* 行 2.5：门卫状态（核心新增） */}
      {gate && <GateBadge gate={gate} />}

      {/* 行 3：关键指标网格 */}
      <div className="grid grid-cols-3 gap-2 sm:gap-4 mt-3 sm:mt-4">
        {/* 置信度 */}
        <div>
          <div className="text-xxs sm:text-xs text-text-tertiary mb-0.5 sm:mb-1">置信度</div>
          <div className="mono-num text-base sm:text-lg font-semibold">
            {(data.confidence * 100).toFixed(0)}%
          </div>
        </div>

        {/* 因子分布 */}
        <div>
          <div className="text-xxs sm:text-xs text-text-tertiary mb-0.5 sm:mb-1">多/空/中</div>
          <div className="flex items-center gap-1 sm:gap-1.5 text-base sm:text-lg mono-num">
            <span className="text-bull font-semibold">{data.bullish_factors}</span>
            <span className="text-text-tertiary">/</span>
            <span className="text-bear font-semibold">{data.bearish_factors}</span>
            <span className="text-text-tertiary">/</span>
            <span className="text-text-secondary">
              {11 - data.bullish_factors - data.bearish_factors}
            </span>
          </div>
        </div>

        {/* 风险等级 */}
        <div>
          <div className="text-xxs sm:text-xs text-text-tertiary mb-0.5 sm:mb-1">风险</div>
          <div className={`text-base sm:text-lg font-semibold ${riskColor(data.risk_level)}`}>
            {riskLabel(data.risk_level)}
            {data.anomaly_count > 0 && (
              <span className="text-xxs sm:text-xs text-anomaly ml-1">
                ({data.anomaly_count})
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 底部：更新时间 + 交易按钮 */}
      <div className="mt-3 sm:mt-4 pt-2.5 sm:pt-3 border-t border-surface-3/30 flex items-center justify-between">
        <span className="text-xxs sm:text-xs text-text-tertiary mono-num">
          {formatTimestamp(data.last_update_ms)}
        </span>
        {onTrade && (data.signal.includes('BUY') || data.signal.includes('SELL')) && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onTrade(symbol, data.signal.includes('BUY') ? 'LONG' : 'SHORT');
            }}
            className={`px-3 sm:px-4 py-1.5 rounded-lg text-xs font-bold text-white transition-all hover:opacity-90 active:scale-95 min-h-[36px] ${
              data.signal.includes('BUY') ? 'bg-bull' : 'bg-bear'
            }`}
          >
            {data.signal.includes('BUY') ? '做多' : '做空'}
          </button>
        )}
      </div>
    </div>
  );
}
