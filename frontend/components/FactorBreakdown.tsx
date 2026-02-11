/**
 * 因子分解面板 — 展示 11 个评分因子的详细得分和原因。
 * 参考 KKline：text-sm 基础、标题 text-lg、数字 text-2xl，间距宽裕
 */

'use client';

import { useSignalDetail, FactorDetail, AnomalyDetail, formatScore, signalLabel, signalColor, riskColor } from '@/lib/hooks';

interface Props {
  symbol: string;
  onClose: () => void;
}

// 因子中文名映射
const FACTOR_LABELS: Record<string, string> = {
  cvd: '成交量 Delta',
  ofi: '订单流不平衡',
  book_imbalance: 'L1 盘口压力',
  large_trade: '大单资金流',
  depth_change: '深度变化',
  funding: '资金费率',
  liquidation: '清算级联',
  sentiment: '市场情绪',
  trend: '趋势一致性',
  vpin: 'VPIN 知情交易',
  oi: '持仓量变化',
};

function FactorRow({ factor }: { factor: FactorDetail }) {
  const isPositive = factor.score > 0;
  const barWidth = Math.abs(factor.score) * 50;
  const label = FACTOR_LABELS[factor.name] || factor.name;

  return (
    <div className="group py-3.5 border-b border-surface-3/20 last:border-0">
      {/* 名称 + 权重 + 得分 */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-text-primary">{label}</span>
          <span className="text-xs text-text-tertiary mono-num">
            {(factor.weight * 100).toFixed(0)}%
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-base mono-num font-bold ${
            factor.score > 0.05 ? 'text-bull' :
            factor.score < -0.05 ? 'text-bear' : 'text-text-secondary'
          }`}>
            {formatScore(factor.score)}
          </span>
          <span className="text-xs mono-num text-text-tertiary">
            ({factor.weighted_score > 0 ? '+' : ''}{(factor.weighted_score * 100).toFixed(1)})
          </span>
        </div>
      </div>

      {/* 得分条 */}
      <div className="factor-bar-container h-1.5 mb-2">
        {isPositive ? (
          <div
            className="factor-bar-positive"
            style={{ width: `${barWidth}%` }}
          />
        ) : (
          <div
            className="factor-bar-negative"
            style={{ width: `${barWidth}%` }}
          />
        )}
        <div className="factor-bar-center" />
      </div>

      {/* 原因说明 */}
      <div className="text-xs text-text-tertiary leading-relaxed">
        {factor.reason}
      </div>
    </div>
  );
}

function AnomalyItem({ anomaly }: { anomaly: AnomalyDetail }) {
  const severityColor: Record<string, string> = {
    LOW: 'text-text-secondary',
    MEDIUM: 'text-warn',
    HIGH: 'text-bear',
    CRITICAL: 'text-bear animate-pulse',
  };

  return (
    <div className="flex items-start gap-3 py-3 border-b border-surface-3/20 last:border-0">
      <span className={`text-xs font-bold mt-0.5 ${severityColor[anomaly.severity] || 'text-text-secondary'}`}>
        {anomaly.severity}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-anomaly">{anomaly.title}</div>
        <div className="text-xs text-text-tertiary mt-1 leading-relaxed">
          {anomaly.description}
        </div>
      </div>
    </div>
  );
}

export default function FactorBreakdown({ symbol, onClose }: Props) {
  const { data, error, loading } = useSignalDetail(symbol, 1000);

  if (loading) {
    return (
      <div className="card p-8 animate-pulse">
        <div className="h-5 bg-surface-3 rounded w-48 mb-6" />
        <div className="space-y-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-10 bg-surface-3/50 rounded" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="card p-8">
        <p className="text-sm text-bear">加载失败: {error}</p>
      </div>
    );
  }

  return (
    <div className="card overflow-hidden animate-slide-up">
      {/* 头部 */}
      <div className="px-6 py-5 border-b border-surface-3/50 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <span className="text-2xl font-bold">{symbol}</span>
          <span className={`badge text-sm px-3 py-1 ${
            data.signal.includes('BUY') ? 'badge-bull' :
            data.signal.includes('SELL') ? 'badge-bear' : 'badge-neutral'
          }`}>
            {signalLabel(data.signal)}
          </span>
          <span className={`text-xl mono-num font-bold ${signalColor(data.signal)}`}>
            {formatScore(data.score)}
          </span>
        </div>
        <button
          onClick={onClose}
          className="text-text-tertiary hover:text-text-primary transition-colors text-sm px-3 py-1.5 rounded-lg hover:bg-surface-3"
        >
          关闭
        </button>
      </div>

      <div className="flex flex-col lg:flex-row">
        {/* 左：因子列表 */}
        <div className="flex-1 px-6 py-4 border-r border-surface-3/30">
          <div className="text-xs text-text-tertiary uppercase tracking-wider mb-3 font-medium">
            11 因子评分
          </div>
          <div>
            {(data.factors || [])
              .sort((a, b) => Math.abs(b.weighted_score) - Math.abs(a.weighted_score))
              .map((f) => (
                <FactorRow key={f.name} factor={f} />
              ))}
          </div>
        </div>

        {/* 右：异常 + 统计 */}
        <div className="w-full lg:w-96 px-6 py-4">
          {/* 统计概览 */}
          <div className="text-xs text-text-tertiary uppercase tracking-wider mb-3 font-medium">
            信号概况
          </div>
          <div className="grid grid-cols-2 gap-4 mb-6">
            <div className="bg-surface-2 rounded-xl p-4">
              <div className="text-xs text-text-tertiary mb-1">置信度</div>
              <div className="text-2xl mono-num font-bold">
                {(data.confidence * 100).toFixed(0)}%
              </div>
            </div>
            <div className="bg-surface-2 rounded-xl p-4">
              <div className="text-xs text-text-tertiary mb-1">风险</div>
              <div className={`text-2xl font-bold ${riskColor(data.risk_level)}`}>
                {data.risk_level}
              </div>
            </div>
            <div className="bg-surface-2 rounded-xl p-4">
              <div className="text-xs text-text-tertiary mb-1">看多因子</div>
              <div className="text-2xl mono-num font-bold text-bull">
                {data.bullish_count}
              </div>
            </div>
            <div className="bg-surface-2 rounded-xl p-4">
              <div className="text-xs text-text-tertiary mb-1">看空因子</div>
              <div className="text-2xl mono-num font-bold text-bear">
                {data.bearish_count}
              </div>
            </div>
          </div>

          {/* 异常事件 */}
          {data.anomalies && data.anomalies.length > 0 && (
            <>
              <div className="text-xs text-text-tertiary uppercase tracking-wider mb-3 font-medium">
                异常告警 ({data.anomaly_count})
              </div>
              <div className="bg-surface-2 rounded-xl p-4">
                {data.anomalies.map((a, i) => (
                  <AnomalyItem key={i} anomaly={a} />
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
