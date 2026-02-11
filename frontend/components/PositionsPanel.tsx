/**
 * 持仓面板 — 显示当前所有持仓 + 一键平仓。
 *
 * 数据来源：KKline API /api/positions + /api/balance
 * 刷新间隔：5 秒
 */

'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  fetchPositions,
  fetchBalance,
  closeTrade,
  Position,
  BalanceInfo,
} from '@/lib/kkline-api';

/** null-safe 数值格式化 */
function safeNum(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(v)) return '--';
  return v.toFixed(digits);
}
function safeLoc(v: number | null | undefined, opts?: Intl.NumberFormatOptions): string {
  if (v == null || isNaN(v)) return '--';
  return v.toLocaleString(undefined, opts);
}

function PnlDisplay({ value, pct }: { value: number | null | undefined; pct?: number | null }) {
  if (value == null || isNaN(value)) return <span className="mono-num text-text-tertiary">--</span>;
  const color = value >= 0 ? 'text-bull' : 'text-bear';
  const sign = value >= 0 ? '+' : '';
  return (
    <span className={`mono-num font-bold ${color}`}>
      {sign}${value.toFixed(2)}
      {pct != null && !isNaN(pct) && (
        <span className="text-xs ml-1">({sign}{pct.toFixed(2)}%)</span>
      )}
    </span>
  );
}

function PositionRow({ pos, onClose }: { pos: Position; onClose: (symbol: string) => void }) {
  const isBull = pos.side === 'LONG' || pos.side === 'BUY';
  const sideColor = isBull ? 'text-bull' : 'text-bear';
  const sideBg = isBull ? 'bg-bull/10' : 'bg-bear/10';

  return (
    <div className="flex items-center gap-4 py-3 border-b border-surface-2/50 last:border-0">
      {/* 币种 + 方向 */}
      <div className="flex-shrink-0 w-28">
        <div className="font-bold text-text-primary">{pos.symbol.replace('USDT', '')}</div>
        <div className="flex items-center gap-1.5 mt-0.5">
          <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${sideBg} ${sideColor}`}>
            {pos.side}
          </span>
          <span className="text-xs text-text-tertiary">{pos.leverage}x</span>
        </div>
      </div>

      {/* 入场/标记价 */}
      <div className="flex-1 text-sm">
        <div className="text-text-tertiary text-xs">入场</div>
        <div className="mono-num text-text-secondary">
          ${safeLoc(pos.entry_price, { minimumFractionDigits: 2 })}
        </div>
      </div>
      <div className="flex-1 text-sm">
        <div className="text-text-tertiary text-xs">标记</div>
        <div className="mono-num text-text-primary">
          ${safeLoc(pos.mark_price, { minimumFractionDigits: 2 })}
        </div>
      </div>

      {/* 名义价值 */}
      <div className="flex-1 text-sm">
        <div className="text-text-tertiary text-xs">头寸</div>
        <div className="mono-num text-text-secondary">
          ${safeLoc(pos.notional, { maximumFractionDigits: 0 })}
        </div>
      </div>

      {/* 未实现盈亏 */}
      <div className="flex-1 text-sm text-right">
        <div className="text-text-tertiary text-xs">盈亏</div>
        <PnlDisplay value={pos.unrealized_pnl} pct={pos.unrealized_pnl_pct} />
      </div>

      {/* 平仓按钮 */}
      <button
        onClick={() => onClose(pos.symbol)}
        className="flex-shrink-0 px-3 py-1.5 bg-surface-2 hover:bg-bear hover:text-white text-text-secondary text-xs font-medium rounded-lg transition-colors"
      >
        平仓
      </button>
    </div>
  );
}

export default function PositionsPanel() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [balance, setBalance] = useState<BalanceInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [closing, setClosing] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const [posData, balData] = await Promise.all([
        fetchPositions(),
        fetchBalance(),
      ]);
      if (mountedRef.current) {
        setPositions(posData);
        setBalance(balData);
        setLoading(false);
      }
    } catch {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [refresh]);

  const handleClose = async (symbol: string) => {
    if (!confirm(`确认平仓 ${symbol}？`)) return;
    setClosing(symbol);
    try {
      await closeTrade({ symbol, ratio: 1.0, reason: 'FlowEdge 驾驶舱手动平仓' });
      await refresh();
    } catch (e) {
      alert(`平仓失败: ${e instanceof Error ? e.message : '未知错误'}`);
    }
    setClosing(null);
  };

  const totalPnl = positions.reduce((sum, p) => sum + (p.unrealized_pnl || 0), 0);

  return (
    <div className="space-y-6">
      {/* 账户概览 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">总余额</div>
          <div className="text-xl font-bold mono-num text-text-primary">
            {balance ? `$${safeLoc(balance.total_balance, { minimumFractionDigits: 2 })}` : '--'}
          </div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">可用余额</div>
          <div className="text-xl font-bold mono-num text-text-primary">
            {balance ? `$${safeLoc(balance.available_balance, { minimumFractionDigits: 2 })}` : '--'}
          </div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">未实现盈亏</div>
          <div className={`text-xl font-bold mono-num ${totalPnl >= 0 ? 'text-bull' : 'text-bear'}`}>
            {positions.length > 0
              ? `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`
              : '--'
            }
          </div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">持仓数</div>
          <div className="text-xl font-bold mono-num text-info">
            {positions.length}
          </div>
        </div>
      </div>

      {/* 持仓列表 */}
      <div className="card p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold text-text-primary">当前持仓</h3>
          <button
            onClick={refresh}
            className="text-xs text-text-tertiary hover:text-text-secondary transition-colors"
          >
            刷新
          </button>
        </div>

        {loading ? (
          <div className="py-8 text-center text-text-tertiary text-sm animate-pulse">
            加载持仓数据...
          </div>
        ) : positions.length === 0 ? (
          <div className="py-8 text-center text-text-tertiary text-sm">
            暂无持仓
          </div>
        ) : (
          <div>
            {positions.map((pos) => (
              <PositionRow
                key={pos.symbol}
                pos={pos}
                onClose={handleClose}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
