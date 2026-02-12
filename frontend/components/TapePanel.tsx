/**
 * 逐笔成交面板（Time & Sales）— 优化版
 *
 * 实时显示最近成交流，参考 ATAS/Bookmap/Flowsurface 的 Tape 窗口。
 * 功能：
 *   - 实时滚动成交列表（颜色区分买卖）
 *   - 大单高亮 + 闪烁
 *   - 10s/60s 买卖统计 + 比例条
 *   - 成交速率指示器
 *   - 宽度自适应
 */

'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { subscribeOrderflow } from '@/lib/api';

interface TapeTrade {
  ts: number;
  p: number;
  q: number;
  v: number;
  s: 'B' | 'S';
  lg: boolean;
}

interface TapeStats {
  buy_count: number;
  sell_count: number;
  buy_usdt: number;
  sell_usdt: number;
  large_count: number;
  large_usdt: number;
  trades_per_sec: number;
  avg_trade_usdt: number;
}

interface TapeData {
  trades: TapeTrade[];
  stats_10s: TapeStats;
  stats_60s: TapeStats;
}

function formatTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString('zh-CN', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Shanghai',
  });
}

function formatUSD(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

function CompactStats({ s10, s60 }: { s10: TapeStats; s60: TapeStats }) {
  const total10 = s10.buy_usdt + s10.sell_usdt;
  const buyPct10 = total10 > 0 ? (s10.buy_usdt / total10) * 100 : 50;
  const total60 = s60.buy_usdt + s60.sell_usdt;

  return (
    <div className="px-3 py-1.5 space-y-1 border-b border-surface-3/20">
      <div className="grid grid-cols-2 gap-2 text-xxs">
        <div className="min-w-0">
          <div className="flex items-center justify-between text-text-tertiary">
            <span>10秒</span>
            <span>{s10.trades_per_sec.toFixed(1)} 笔/s</span>
          </div>
          <div className="flex justify-between">
            <span className="text-bull">{formatUSD(s10.buy_usdt)}</span>
            <span className="text-bear">{formatUSD(s10.sell_usdt)}</span>
          </div>
        </div>
        <div className="min-w-0">
          <div className="flex items-center justify-between text-text-tertiary">
            <span>60秒</span>
            <span>均单 {formatUSD(s60.avg_trade_usdt)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-bull">{s60.buy_count} 笔</span>
            <span className="text-bear">{s60.sell_count} 笔</span>
          </div>
        </div>
      </div>
      <div className="h-1.5 rounded-full bg-surface-3/50 overflow-hidden flex">
        <div className="h-full bg-bull transition-all duration-300" style={{ width: `${buyPct10}%` }} />
        <div className="h-full bg-bear transition-all duration-300" style={{ width: `${100 - buyPct10}%` }} />
      </div>
    </div>
  );
}

export default function TapePanel({ symbol }: { symbol: string }) {
  const VISIBLE_ROWS = 30;
  const ROW_HEIGHT_PX = 14;
  const LIST_MIN_HEIGHT = VISIBLE_ROWS * ROW_HEIGHT_PX;
  const PANEL_MAX_HEIGHT = LIST_MIN_HEIGHT + 96;
  const [tape, setTape] = useState<TapeData | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);

  useEffect(() => {
    const unsub = subscribeOrderflow(symbol, (data: Record<string, unknown>) => {
      const tapeData = data.tape as TapeData | undefined;
      if (tapeData) setTape(tapeData);
    });
    return unsub;
  }, [symbol]);

  useEffect(() => {
    if (autoScrollRef.current && listRef.current) {
      listRef.current.scrollTop = 0;
    }
  }, [tape?.trades]);

  const handleScroll = useCallback(() => {
    if (listRef.current) {
      autoScrollRef.current = listRef.current.scrollTop < 10;
    }
  }, []);

  if (!tape) {
    return (
      <div className="bg-surface-1 rounded-xl p-6 border border-surface-3/30 flex items-center justify-center">
        <div className="text-sm text-text-tertiary animate-pulse">等待逐笔成交数据...</div>
      </div>
    );
  }

  return (
    <div
      className="bg-surface-1 rounded-xl border border-surface-3/30 overflow-hidden h-full flex flex-col"
      style={{ maxHeight: `${PANEL_MAX_HEIGHT}px` }}
    >
      {/* 标题栏 */}
      <div className="px-3 py-2 border-b border-surface-3/30 flex items-center justify-between shrink-0">
        <span className="text-sm font-medium text-text-primary">逐笔成交</span>
        <span className="text-xxs text-text-tertiary font-mono">
          {tape.stats_10s.trades_per_sec.toFixed(1)} 笔/秒
        </span>
      </div>

      {/* 统计条 */}
      <CompactStats s10={tape.stats_10s} s60={tape.stats_60s} />

      {/* 表头 */}
      <div className="grid grid-cols-4 px-3 py-1 text-xxs text-text-tertiary border-b border-surface-3/10 shrink-0">
        <span>时间</span>
        <span className="text-right">价格</span>
        <span className="text-right">数量</span>
        <span className="text-right">金额</span>
      </div>

      {/* 成交列表 */}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto scrollbar-thin"
        style={{ minHeight: `${LIST_MIN_HEIGHT}px` }}
      >
        {tape.trades.map((t, i) => {
          const isBuy = t.s === 'B';
          return (
            <div
              key={`${t.ts}-${i}`}
              className={`grid grid-cols-4 px-3 py-[1px] text-[10px] leading-[12px] font-mono transition-colors
                ${t.lg ? 'bg-warn/10 font-semibold' : 'hover:bg-surface-2/30'}
                ${isBuy ? 'text-bull' : 'text-bear'}`}
            >
              <span className="text-text-tertiary">{formatTime(t.ts)}</span>
              <span className="text-right tabular-nums">{t.p.toLocaleString()}</span>
              <span className="text-right tabular-nums">{t.q}</span>
              <span className="text-right tabular-nums">
                {formatUSD(t.v)}
                {t.lg && <span className="ml-0.5 text-warn">●</span>}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
