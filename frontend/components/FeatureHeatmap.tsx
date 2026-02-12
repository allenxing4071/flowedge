/**
 * 特征热力图 — 所有币种 × 所有特征的矩阵视图
 * 参考 KKline 表格：text-sm 基础字号，宽裕 padding，清晰可读
 */

'use client';

import { SignalDetail, FactorDetail } from '@/lib/hooks';
import { useState, useEffect } from 'react';
import { fetchSignals } from '@/lib/api';

// 因子简称
const FACTOR_SHORT: Record<string, string> = {
  cvd: 'CVD',
  ofi: 'OFI',
  book_imbalance: 'Book',
  large_trade: '大单',
  depth_change: '深度',
  funding: '费率',
  liquidation: '清算',
  sentiment: '情绪',
  trend: '趋势',
  vpin: 'VPIN',
  oi: 'OI',
};

// 因子顺序
const FACTOR_ORDER = [
  'cvd', 'ofi', 'book_imbalance', 'large_trade', 'depth_change',
  'funding', 'liquidation', 'sentiment', 'trend', 'vpin', 'oi',
];

function scoreToColor(score: number): string {
  if (score > 0.5) return 'bg-bull text-white';
  if (score > 0.2) return 'bg-bull/60 text-white';
  if (score > 0.05) return 'bg-bull/25 text-bull';
  if (score < -0.5) return 'bg-bear text-white';
  if (score < -0.2) return 'bg-bear/60 text-white';
  if (score < -0.05) return 'bg-bear/25 text-bear';
  return 'bg-surface-3/50 text-text-tertiary';
}

export default function FeatureHeatmap() {
  const [allSignals, setAllSignals] = useState<Record<string, SignalDetail>>({});

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      try {
        const data = await fetchSignals();
        if (mounted) setAllSignals(data);
      } catch {
        /* 静默 */
      }
      if (mounted) setTimeout(poll, 2000);
    };
    poll();
    return () => { mounted = false; };
  }, []);

  const symbols = Object.keys(allSignals);
  if (symbols.length === 0) {
    return (
      <div className="card p-6">
        <div className="text-sm text-text-secondary font-medium mb-4">
          特征热力图
        </div>
        <div className="text-sm text-text-tertiary">等待数据...</div>
      </div>
    );
  }

  return (
    <div className="card p-3 sm:p-6">
      <div className="text-xs sm:text-sm text-text-secondary font-medium mb-2 sm:mb-4">
        特征热力图
      </div>

      {/* 手机端滚动提示 */}
      <div className="sm:hidden text-xxs text-text-tertiary mb-1.5 flex items-center gap-1">
        <span>←</span> 左右滑动查看
      </div>

      <div className="overflow-x-auto -mx-3 px-3 sm:mx-0 sm:px-0">
        <table className="w-full text-center" style={{ minWidth: '600px' }}>
          <thead>
            <tr>
              <th className="text-xxs sm:text-sm text-text-tertiary font-medium px-2 sm:px-4 py-2 sm:py-3 text-left sticky left-0 bg-surface-1 z-10">
                币种
              </th>
              {FACTOR_ORDER.map(name => (
                <th key={name} className="text-xxs sm:text-xs text-text-tertiary font-medium px-0.5 sm:px-2 py-2 sm:py-3">
                  {FACTOR_SHORT[name]}
                </th>
              ))}
              <th className="text-xxs sm:text-sm text-text-tertiary font-medium px-1 sm:px-4 py-2 sm:py-3">
                合
              </th>
            </tr>
          </thead>
          <tbody>
            {symbols.map(symbol => {
              const detail = allSignals[symbol];
              if (!detail || !detail.factors) return null;

              const factorMap: Record<string, FactorDetail> = {};
              detail.factors.forEach(f => { factorMap[f.name] = f; });

              return (
                <tr key={symbol} className="border-t border-surface-3/20 hover:bg-surface-2/50 transition-colors">
                  <td className="text-xxs sm:text-sm font-semibold px-2 sm:px-4 py-1.5 sm:py-3 text-left sticky left-0 bg-surface-1 z-10">
                    {symbol.replace('USDT', '')}
                  </td>
                  {FACTOR_ORDER.map(name => {
                    const f = factorMap[name];
                    const score = f?.score || 0;
                    return (
                      <td key={name} className="px-0.5 sm:px-1.5 py-1 sm:py-2">
                        <div
                          className={`rounded px-1 sm:px-2 py-0.5 sm:py-1.5 mono-num text-xxs sm:text-sm font-semibold transition-colors ${scoreToColor(score)}`}
                          title={f?.reason || ''}
                        >
                          {score > 0 ? '+' : ''}{(score * 100).toFixed(0)}
                        </div>
                      </td>
                    );
                  })}
                  <td className="px-1 sm:px-2 py-1 sm:py-2">
                    <div className={`rounded px-1.5 sm:px-3 py-0.5 sm:py-1.5 mono-num text-xs sm:text-base font-bold transition-colors ${scoreToColor(detail.score)}`}>
                      {detail.score > 0 ? '+' : ''}{(detail.score * 100).toFixed(0)}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
