/**
 * 深度盘口面板（Depth of Market）— ATAS 梯子盘布局
 *
 * 核心改造：从“上卖下买”改为“同一行左买右卖”。
 * 每行结构：
 *   买金额 | 买数量 | 买价 || 卖价 | 卖数量 | 卖金额
 * 这样买卖严格左右对照，不再上下分区。
 */

'use client';

import { useState, useEffect, useMemo } from 'react';
import { subscribeOrderflow } from '@/lib/api';

interface DOMLevel {
  price: number;
  qty: number;
  usdt: number;
}

interface FakeWall {
  timestamp_ms: number;
  side: string;
  price: number;
  appeared_qty_usdt: number;
  disappeared_ms: number;
}

interface DOMData {
  bid_price: number;
  ask_price: number;
  mid_price: number;
  spread_pct: number;
  bid_depth_usdt: number;
  ask_depth_usdt: number;
  imbalance: number;
  bids: DOMLevel[];
  asks: DOMLevel[];
  wall_events: number;
  fake_walls: FakeWall[];
}

function formatUSD(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return `${v.toFixed(0)}`;
}

function SideCell({
  side,
  level,
  maxUsdt,
  isFakeWall,
}: {
  side: 'bid' | 'ask';
  level: DOMLevel | null;
  maxUsdt: number;
  isFakeWall: boolean;
}) {
  if (!level) {
    return (
      <>
        <span className="text-right pr-2 text-text-tertiary/30">-</span>
        <span className="text-right pr-2 text-text-tertiary/30">-</span>
        <span className="text-right pr-2 text-text-tertiary/30">-</span>
      </>
    );
  }

  const pct = maxUsdt > 0 ? Math.min((level.usdt / maxUsdt) * 100, 100) : 0;
  const intensity = Math.min(pct / 100, 1);
  const bgColor = side === 'bid'
    ? `rgba(0, 230, 118, ${0.04 + intensity * 0.35})`
    : `rgba(255, 23, 68, ${0.04 + intensity * 0.35})`;
  const priceColor = side === 'bid' ? 'text-bull' : 'text-bear';

  return (
    <>
      <span
        className={`text-right pr-2 tabular-nums ${isFakeWall ? 'ring-1 ring-warn/50' : ''}`}
        style={{ background: bgColor }}
      >
        ${formatUSD(level.usdt)}
      </span>
      <span
        className={`text-right pr-2 tabular-nums ${isFakeWall ? 'ring-1 ring-warn/50' : ''}`}
        style={{ background: bgColor }}
      >
        {level.qty.toFixed(4)}
      </span>
      <span
        className={`text-right pr-2 tabular-nums ${priceColor} ${isFakeWall ? 'ring-1 ring-warn/50' : ''}`}
        style={{ background: bgColor }}
      >
        {level.price.toLocaleString()}
      </span>
    </>
  );
}

export default function DOMHeatmap({ symbol }: { symbol: string }) {
  const [dom, setDom] = useState<DOMData | null>(null);

  useEffect(() => {
    const unsub = subscribeOrderflow(symbol, (data: Record<string, unknown>) => {
      const domData = data.dom as DOMData | undefined;
      if (domData) setDom(domData);
    });
    return unsub;
  }, [symbol]);

  const maxUsdt = useMemo(() => {
    if (!dom) return 0;
    const allUsdt = [...dom.bids.map((l) => l.usdt), ...dom.asks.map((l) => l.usdt)];
    return Math.max(...allUsdt, 1);
  }, [dom]);

  const fakeWallPrices = useMemo(() => {
    if (!dom) return new Set<number>();
    return new Set(dom.fake_walls.map((w) => w.price));
  }, [dom]);

  const ladderRows = useMemo(() => {
    if (!dom) return [];
    const depth = Math.max(dom.bids.length, dom.asks.length);
    return Array.from({ length: depth }, (_, i) => ({
      bid: dom.bids[i] ?? null,
      ask: dom.asks[i] ?? null,
    }));
  }, [dom]);

  if (!dom || !dom.bids.length) {
    return (
      <div className="bg-surface-1 rounded-xl p-6 border border-surface-3/30 flex items-center justify-center">
        <div className="text-sm text-text-tertiary animate-pulse">等待深度盘口数据...</div>
      </div>
    );
  }

  const totalDepth = dom.bid_depth_usdt + dom.ask_depth_usdt;
  const bidPct = totalDepth > 0 ? (dom.bid_depth_usdt / totalDepth) * 100 : 50;

  return (
    <div className="bg-surface-1 rounded-xl border border-surface-3/30 overflow-hidden h-full flex flex-col">
      {/* 标题栏 */}
      <div className="px-3 py-2 border-b border-surface-3/30 flex items-center justify-between shrink-0">
        <span className="text-sm font-medium text-text-primary">深度盘口</span>
        <div className="flex items-center gap-3 text-xxs">
          <span className="text-text-tertiary font-mono">
            价差 {dom.spread_pct.toFixed(3)}%
          </span>
          {dom.wall_events > 0 && (
            <span className="text-warn font-medium">假墙 {dom.wall_events}</span>
          )}
        </div>
      </div>

      {/* 深度不平衡条 */}
      <div className="px-3 py-1.5 border-b border-surface-3/20 shrink-0">
        <div className="flex justify-between text-xxs mb-1">
          <span className="text-bull font-medium">${formatUSD(dom.bid_depth_usdt)}</span>
          <span className={`font-mono font-medium ${dom.imbalance > 0 ? 'text-bull' : dom.imbalance < 0 ? 'text-bear' : 'text-text-tertiary'}`}>
            {dom.imbalance > 0 ? '+' : ''}{dom.imbalance.toFixed(1)}%
          </span>
          <span className="text-bear font-medium">${formatUSD(dom.ask_depth_usdt)}</span>
        </div>
        <div className="h-1.5 rounded-full bg-surface-3/50 overflow-hidden flex">
          <div className="h-full bg-bull/60 transition-all duration-300" style={{ width: `${bidPct}%` }} />
          <div className="h-full bg-bear/60 transition-all duration-300" style={{ width: `${100 - bidPct}%` }} />
        </div>
      </div>

      {/* 梯子盘（同一行左买右卖） */}
      <div className="flex-1">
        <div className="grid grid-cols-6 px-2 py-0.5 text-xxs text-text-tertiary border-b border-surface-3/10 bg-surface-2/20">
          <span className="text-right pr-2">买金额</span>
          <span className="text-right pr-2">买数量</span>
          <span className="text-right pr-2">买价</span>
          <span className="text-left pl-2">卖价</span>
          <span className="text-left pl-2">卖数量</span>
          <span className="text-left pl-2">卖金额</span>
        </div>

        <div className="px-3 py-1.5 bg-surface-2/50 border-y border-info/20 flex items-center justify-center gap-3 shrink-0">
          <span className="text-base font-bold text-info font-mono">
            {dom.mid_price.toLocaleString()}
          </span>
        </div>

        <div className="divide-y divide-surface-3/10">
          {ladderRows.map((row, idx) => {
            const bidFake = row.bid ? fakeWallPrices.has(row.bid.price) : false;
            const askFake = row.ask ? fakeWallPrices.has(row.ask.price) : false;
            return (
              <div key={idx} className="grid grid-cols-6 py-0.5 text-xxs font-mono items-center hover:brightness-125 transition-all">
                <SideCell side="bid" level={row.bid} maxUsdt={maxUsdt} isFakeWall={bidFake} />
                {row.ask ? (
                  <>
                    <span
                      className={`text-left pl-2 tabular-nums text-bear ${askFake ? 'ring-1 ring-warn/50' : ''}`}
                      style={{
                        background: `rgba(255, 23, 68, ${
                          0.04 + (Math.min((row.ask.usdt / Math.max(maxUsdt, 1)) * 100, 100) / 100) * 0.35
                        })`,
                      }}
                    >
                      {row.ask.price.toLocaleString()}
                    </span>
                    <span
                      className={`text-left pl-2 tabular-nums ${askFake ? 'ring-1 ring-warn/50' : ''}`}
                      style={{
                        background: `rgba(255, 23, 68, ${
                          0.04 + (Math.min((row.ask.usdt / Math.max(maxUsdt, 1)) * 100, 100) / 100) * 0.35
                        })`,
                      }}
                    >
                      {row.ask.qty.toFixed(4)}
                    </span>
                    <span
                      className={`text-left pl-2 tabular-nums ${askFake ? 'ring-1 ring-warn/50' : ''}`}
                      style={{
                        background: `rgba(255, 23, 68, ${
                          0.04 + (Math.min((row.ask.usdt / Math.max(maxUsdt, 1)) * 100, 100) / 100) * 0.35
                        })`,
                      }}
                    >
                      ${formatUSD(row.ask.usdt)}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="text-left pl-2 text-text-tertiary/30">-</span>
                    <span className="text-left pl-2 text-text-tertiary/30">-</span>
                    <span className="text-left pl-2 text-text-tertiary/30">-</span>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
