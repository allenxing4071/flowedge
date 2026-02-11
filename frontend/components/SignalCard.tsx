/**
 * 信号卡片 — 单币种信号概览
 * 参考 KKline 仪表盘：大数字突出、层级分明、宽裕间距
 */

'use client';

import {
  SymbolSignal,
  signalColor,
  signalLabel,
  riskColor,
  formatScore,
  formatTimestamp,
} from '@/lib/hooks';

interface Props {
  symbol: string;
  data: SymbolSignal;
  onClick?: (symbol: string) => void;
  onTrade?: (symbol: string, side: 'LONG' | 'SHORT') => void;
}

export default function SignalCard({ symbol, data, onClick, onTrade }: Props) {
  const isPositive = data.score > 0;
  const scoreAbs = Math.abs(data.score);

  return (
    <div
      className={`card-hover p-6 cursor-pointer group ${
        data.signal_changed ? 'animate-slide-up ring-1 ring-info/30' : ''
      }`}
      onClick={() => onClick?.(symbol)}
    >
      {/* 行 1：币种 + 信号徽章 */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <span className="text-xl font-bold tracking-tight">{symbol}</span>
          {data.signal_changed && (
            <span className="text-xs text-info font-mono animate-fade-in">NEW</span>
          )}
        </div>
        <span className={`badge text-sm px-3 py-1 ${
          data.signal.includes('BUY') ? 'badge-bull' :
          data.signal.includes('SELL') ? 'badge-bear' : 'badge-neutral'
        }`}>
          {signalLabel(data.signal)}
        </span>
      </div>

      {/* 行 2：得分 — 大数字核心视觉 */}
      <div className="mb-5">
        <div className={`text-3xl font-bold mono-num mb-2 ${signalColor(data.signal)}`}>
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

      {/* 行 3：关键指标网格 */}
      <div className="grid grid-cols-3 gap-4">
        {/* 置信度 */}
        <div>
          <div className="text-xs text-text-tertiary mb-1">置信度</div>
          <div className="mono-num text-lg font-semibold">
            {(data.confidence * 100).toFixed(0)}%
          </div>
        </div>

        {/* 因子分布 */}
        <div>
          <div className="text-xs text-text-tertiary mb-1">多 / 空 / 中</div>
          <div className="flex items-center gap-1.5 text-lg mono-num">
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
          <div className="text-xs text-text-tertiary mb-1">风险</div>
          <div className={`text-lg font-semibold ${riskColor(data.risk_level)}`}>
            {data.risk_level}
            {data.anomaly_count > 0 && (
              <span className="text-xs text-anomaly ml-1.5">
                ({data.anomaly_count})
              </span>
            )}
          </div>
        </div>
      </div>

      {/* 底部：更新时间 + 交易按钮 */}
      <div className="mt-4 pt-3 border-t border-surface-3/30 flex items-center justify-between">
        <span className="text-xs text-text-tertiary mono-num">
          更新于 {formatTimestamp(data.last_update_ms)}
        </span>
        {onTrade && (data.signal.includes('BUY') || data.signal.includes('SELL')) && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onTrade(symbol, data.signal.includes('BUY') ? 'LONG' : 'SHORT');
            }}
            className={`px-4 py-1.5 rounded-lg text-xs font-bold text-white transition-all hover:opacity-90 active:scale-95 ${
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
