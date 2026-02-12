/**
 * 交易确认对话框 — 一键交易的核心交互组件。
 *
 * 流程：用户在信号卡片点击"做多/做空" → 弹出此面板 → 填写金额/杠杆/止损 → 确认 → 调用 KKline API
 */

'use client';

import { useState } from 'react';
import { openTrade, OpenTradeParams, TradeResult } from '@/lib/kkline-api';

interface TradeDialogProps {
  symbol: string;
  side: 'LONG' | 'SHORT';
  score: number;
  confidence: number;
  onClose: () => void;
  onSuccess?: (result: TradeResult) => void;
}

export default function TradeDialog({
  symbol,
  side,
  score,
  confidence,
  onClose,
  onSuccess,
}: TradeDialogProps) {
  const [amount, setAmount] = useState(100);
  const [leverage, setLeverage] = useState(10);
  const [stopLoss, setStopLoss] = useState(2.0);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<TradeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isBull = side === 'LONG';
  const colorClass = isBull ? 'text-bull' : 'text-bear';
  const bgClass = isBull ? 'bg-bull' : 'bg-bear';
  const sideLabel = isBull ? '做多（多头）' : '做空（空头）';

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const params: OpenTradeParams = {
        symbol,
        side,
        amount_usdt: amount,
        leverage,
        stop_loss_pct: stopLoss,
      };
      const res = await openTrade(params);
      setResult(res);
      if (res.success) {
        onSuccess?.(res);
      } else {
        setError(res.error || res.message || '交易执行失败');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '网络错误');
    }
    setSubmitting(false);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="card w-full max-w-md mx-4 p-0 overflow-hidden animate-slide-up">
        {/* 标题栏 */}
        <div className={`${bgClass} px-6 py-4 flex items-center justify-between`}>
          <div>
            <div className="text-white font-bold text-lg">{sideLabel}</div>
            <div className="text-white/80 text-sm">{symbol}</div>
          </div>
          <button
            onClick={onClose}
            className="text-white/60 hover:text-white text-xl font-bold w-8 h-8 flex items-center justify-center rounded-lg hover:bg-white/10"
          >
            ×
          </button>
        </div>

        {/* 信号信息 */}
        <div className="px-6 py-3 bg-surface-1 border-b border-surface-2 flex items-center gap-4 text-sm">
          <div>
            <span className="text-text-tertiary">评分 </span>
            <span className={`font-bold mono-num ${colorClass}`}>
              {score > 0 ? '+' : ''}{(score * 100).toFixed(1)}
            </span>
          </div>
          <div>
            <span className="text-text-tertiary">置信度 </span>
            <span className="font-bold mono-num text-text-primary">
              {(confidence * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        {result?.success ? (
          /* 交易成功 */
          <div className="px-6 py-8 text-center">
            <div className="text-3xl mb-3">✓</div>
            <div className="text-lg font-bold text-bull mb-2">交易已提交</div>
            <div className="text-sm text-text-secondary space-y-1">
              {result.entry_price && (
                <div>入场价: ${result.entry_price.toLocaleString()}</div>
              )}
              {result.quantity && (
                <div>数量: {result.quantity}</div>
              )}
            </div>
            <button
              onClick={onClose}
              className="mt-6 px-6 py-2 bg-surface-2 hover:bg-surface-3 rounded-lg text-sm font-medium text-text-primary transition-colors"
            >
              关闭
            </button>
          </div>
        ) : (
          /* 交易表单 */
          <div className="px-6 py-5 space-y-5">
            {/* 金额 */}
            <div>
              <label className="text-sm text-text-secondary mb-2 block">金额 (USDT)</label>
              <div className="flex gap-2">
                {[50, 100, 200, 500].map((v) => (
                  <button
                    key={v}
                    onClick={() => setAmount(v)}
                    className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
                      amount === v
                        ? `${bgClass} text-white`
                        : 'bg-surface-2 text-text-secondary hover:bg-surface-3'
                    }`}
                  >
                    ${v}
                  </button>
                ))}
              </div>
              <input
                type="number"
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value))}
                title="交易金额 (USDT)"
                placeholder="输入金额"
                className="mt-2 w-full px-3 py-2 bg-surface-2 border border-surface-3 rounded-lg text-text-primary mono-num text-sm focus:outline-none focus:border-info"
                min={10}
                step={10}
              />
            </div>

            {/* 杠杆 */}
            <div>
              <label className="text-sm text-text-secondary mb-2 block">
                杠杆: <span className="text-text-primary font-bold">{leverage}x</span>
              </label>
              <input
                type="range"
                min={1}
                max={20}
                value={leverage}
                onChange={(e) => setLeverage(Number(e.target.value))}
                title="杠杆倍数"
                className="w-full accent-info"
              />
              <div className="flex justify-between text-xs text-text-tertiary mt-1">
                <span>1倍</span>
                <span>10倍</span>
                <span>20倍</span>
              </div>
            </div>

            {/* 止损 */}
            <div>
              <label className="text-sm text-text-secondary mb-2 block">
                止损: <span className="text-bear font-bold">-{stopLoss.toFixed(1)}%</span>
              </label>
              <input
                type="range"
                min={0.5}
                max={10}
                step={0.5}
                value={stopLoss}
                onChange={(e) => setStopLoss(Number(e.target.value))}
                title="止损百分比"
                className="w-full accent-bear"
              />
              <div className="flex justify-between text-xs text-text-tertiary mt-1">
                <span>0.5%</span>
                <span>5%</span>
                <span>10%</span>
              </div>
            </div>

            {/* 预估信息 */}
            <div className="bg-surface-1 rounded-lg p-3 space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-text-tertiary">实际头寸</span>
                <span className="mono-num text-text-primary">
                  ${(amount * leverage).toLocaleString()}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-text-tertiary">最大亏损</span>
                <span className="mono-num text-bear">
                  -${(amount * stopLoss / 100).toFixed(2)}
                </span>
              </div>
            </div>

            {/* 错误提示 */}
            {error && (
              <div className="bg-bear/10 border border-bear/30 rounded-lg p-3 text-sm text-bear">
                {error}
              </div>
            )}

            {/* 提交按钮 */}
            <button
              onClick={handleSubmit}
              disabled={submitting || amount <= 0}
              className={`w-full py-3 rounded-lg font-bold text-white text-base transition-all ${
                submitting
                  ? 'opacity-50 cursor-not-allowed'
                  : `${bgClass} hover:opacity-90 active:scale-[0.98]`
              }`}
            >
              {submitting ? '执行中...' : `确认${isBull ? '做多' : '做空'} ${symbol}`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
