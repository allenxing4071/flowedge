/**
 * Footprint Chart 面板 — ATAS Cluster 风格
 *
 * 严格参照 ATAS Order Flow 的 Footprint/Cluster Chart：
 *   每行 = 一个价格档位，分成左右两个等宽色块格子：
 *     左格 = Bid（买），蓝/绿色填满整格，深浅 = 量大小，数字在格内
 *     右格 = Ask（卖），红色填满整格，深浅 = 量大小，数字在格内
 *   POC 行用黄色边框高亮
 *   Delta 失衡行加粗
 */

'use client';

import { useState, useEffect, useMemo } from 'react';
import { subscribeOrderflow } from '@/lib/api';

interface FootprintLevel {
  price: number;
  buy: number;
  sell: number;
  delta: number;
  count: number;
}

interface FootprintBar {
  open_ms: number;
  close_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  buy_total: number;
  sell_total: number;
  delta: number;
  trades: number;
  poc_price: number;
  poc_volume: number;
  levels: FootprintLevel[];
}

interface IcebergCluster {
  start_ms: number;
  end_ms: number;
  price_avg: number;
  total_qty_usdt: number;
  trade_count: number;
  side: string;
  pattern: string;
  confidence: number;
}

interface FootprintData {
  current_bar: FootprintBar | null;
  recent_bars: FootprintBar[];
  tick_size: number;
}

interface IcebergData {
  active_clusters: IcebergCluster[];
  recent_clusters: IcebergCluster[];
  buy_hidden_usdt: number;
  sell_hidden_usdt: number;
  net_hidden_usdt: number;
  cluster_count_60s: number;
}

function fmtVol(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return v.toFixed(0);
}

function fmtTime(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', {
    hour12: false, hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai',
  });
}

/**
 * ATAS 风格色块行
 *
 * 参照 ATAS：
 *   [价格] [■■■ 买量 ■■■] [■■■ 卖量 ■■■]
 *   色块填满整格，颜色深浅 = 量 / 最大量
 *   数字居中在色块内
 */
function ClusterRow({
  level, maxVol, isPOC, isBullCandle, inCandle,
}: {
  level: FootprintLevel;
  maxVol: number;
  isPOC: boolean;
  isBullCandle: boolean;
  inCandle: boolean;
}) {
  // 量归一化 0~1
  const buyRatio = maxVol > 0 ? level.buy / maxVol : 0;
  const sellRatio = maxVol > 0 ? level.sell / maxVol : 0;

  // ATAS 色块：整格填充，alpha 随量变化（量越大越深）
  const buyAlpha = level.buy > 0 ? 0.12 + buyRatio * 0.58 : 0;
  const sellAlpha = level.sell > 0 ? 0.12 + sellRatio * 0.58 : 0;

  const delta = level.buy - level.sell;
  const imbalance = level.buy > 0 && level.sell > 0
    ? Math.abs(delta) / Math.min(level.buy, level.sell)
    : 0;
  // 失衡 > 3x 时加粗
  const isImbalanced = imbalance > 3;

  return (
    <div
      className={`grid grid-cols-[52px_1fr_1fr] border-b border-surface-3/5
        ${isPOC ? 'ring-1 ring-inset ring-yellow-400/60' : ''}`}
      style={{ height: '22px' }}
    >
      {/* 价格 */}
      <div className={`flex items-center justify-end pr-1.5 text-[10px] font-mono tabular-nums border-r border-surface-3/10
        ${isPOC ? 'bg-yellow-400/10 text-yellow-300 font-bold' : inCandle ? (isBullCandle ? 'bg-bull/[0.04]' : 'bg-bear/[0.04]') : ''}
        text-text-tertiary`}>
        {level.price.toLocaleString()}
      </div>

      {/* Bid 色块 — 整格填充蓝/绿 */}
      <div
        className="flex items-center justify-center border-r border-surface-3/5"
        style={{ backgroundColor: buyAlpha > 0 ? `rgba(0, 180, 110, ${buyAlpha})` : 'transparent' }}
      >
        {level.buy > 0 && (
          <span className={`text-[10px] font-mono tabular-nums
            ${isImbalanced && delta > 0 ? 'text-white font-bold' : 'text-green-200/90'}`}>
            {fmtVol(level.buy)}
          </span>
        )}
      </div>

      {/* Ask 色块 — 整格填充红 */}
      <div
        className="flex items-center justify-center"
        style={{ backgroundColor: sellAlpha > 0 ? `rgba(220, 40, 60, ${sellAlpha})` : 'transparent' }}
      >
        {level.sell > 0 && (
          <span className={`text-[10px] font-mono tabular-nums
            ${isImbalanced && delta < 0 ? 'text-white font-bold' : 'text-red-200/90'}`}>
            {fmtVol(level.sell)}
          </span>
        )}
      </div>
    </div>
  );
}

function BarView({
  bar, maxVol, isActive,
}: {
  bar: FootprintBar;
  maxVol: number;
  isActive: boolean;
}) {
  const isBull = bar.close >= bar.open;
  const candleLo = Math.min(bar.open, bar.close);
  const candleHi = Math.max(bar.open, bar.close);

  return (
    <div className={`border border-surface-3/20 rounded overflow-hidden
      ${isActive ? 'ring-1 ring-info/40' : ''}`}>

      {/* 头部 */}
      <div className="px-2 py-1 bg-surface-2/30 flex items-center justify-between text-[11px] font-mono">
        <div className="flex items-center gap-1.5">
          <span className="text-text-tertiary">{fmtTime(bar.open_ms)}</span>
          {isActive && <span className="px-1 py-px rounded bg-info/20 text-info text-[9px] font-semibold">实时</span>}
        </div>
        <div className="flex items-center gap-2">
          <span className={isBull ? 'text-bull' : 'text-bear'}>{bar.close.toLocaleString()}</span>
          <span className={`font-semibold ${bar.delta > 0 ? 'text-bull' : bar.delta < 0 ? 'text-bear' : 'text-text-tertiary'}`}>
            Δ{bar.delta > 0 ? '+' : ''}{fmtVol(bar.delta)}
          </span>
        </div>
      </div>

      {/* 列头 */}
      <div className="grid grid-cols-[52px_1fr_1fr] text-[9px] text-text-tertiary border-b border-surface-3/10 bg-surface-2/10">
        <div className="text-center py-px border-r border-surface-3/10">价格</div>
        <div className="text-center py-px border-r border-surface-3/5 text-green-400/70">买量</div>
        <div className="text-center py-px text-red-400/70">卖量</div>
      </div>

      {/* 色块行 */}
      <div>
        {bar.levels.map((lv) => (
          <ClusterRow
            key={lv.price}
            level={lv}
            maxVol={maxVol}
            isPOC={lv.price === bar.poc_price}
            isBullCandle={isBull}
            inCandle={lv.price >= candleLo && lv.price <= candleHi}
          />
        ))}
      </div>

      {/* 底部统计 */}
      <div className="px-2 py-1 bg-surface-2/20 flex justify-between text-[10px] font-mono border-t border-surface-3/20">
        <span className="text-bull font-semibold">B {fmtVol(bar.buy_total)}</span>
        <span className="text-text-tertiary">{bar.trades} 笔</span>
        <span className="text-bear font-semibold">S {fmtVol(bar.sell_total)}</span>
      </div>
    </div>
  );
}

function IcebergBanner({ ice }: { ice: IcebergData }) {
  if (ice.cluster_count_60s === 0 && ice.active_clusters.length === 0) return null;
  return (
    <div className="px-2 py-1.5 border-b border-surface-3/20 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]">
      <span className="text-anomaly font-semibold">🧊 冰山 {ice.cluster_count_60s}</span>
      <span className="text-bull">买隐 ${fmtVol(ice.buy_hidden_usdt)}</span>
      <span className="text-bear">卖隐 ${fmtVol(ice.sell_hidden_usdt)}</span>
      <span className={ice.net_hidden_usdt > 0 ? 'text-bull font-semibold' : 'text-bear font-semibold'}>
        净{ice.net_hidden_usdt > 0 ? '+' : ''}{fmtVol(ice.net_hidden_usdt)}
      </span>
      {ice.active_clusters.slice(0, 2).map((c, i) => (
        <span key={i} className={`${c.side === 'BUY' ? 'text-bull' : 'text-bear'}`}>
          {c.side === 'BUY' ? '买' : '卖'} ${fmtVol(c.total_qty_usdt)} {c.trade_count}笔
        </span>
      ))}
    </div>
  );
}

export default function FootprintPanel({ symbol }: { symbol: string }) {
  const [fp, setFp] = useState<FootprintData | null>(null);
  const [ice, setIce] = useState<IcebergData | null>(null);
  const [mode, setMode] = useState<'live' | 'hist'>('live');

  useEffect(() => {
    const unsub = subscribeOrderflow(symbol, (d: Record<string, unknown>) => {
      if (d.footprint) setFp(d.footprint as FootprintData);
      if (d.iceberg) setIce(d.iceberg as IcebergData);
    });
    return unsub;
  }, [symbol]);

  // 最大单边量（买或卖），用于色块深浅归一化
  const maxVol = useMemo(() => {
    if (!fp) return 0;
    let mx = 0;
    const bars = [...(fp.current_bar ? [fp.current_bar] : []), ...fp.recent_bars];
    for (const b of bars) for (const l of b.levels) {
      if (l.buy > mx) mx = l.buy;
      if (l.sell > mx) mx = l.sell;
    }
    return mx;
  }, [fp]);

  if (!fp) {
    return (
      <div className="bg-surface-1 rounded-xl p-6 border border-surface-3/30 flex items-center justify-center">
        <span className="text-xs text-text-tertiary animate-pulse">等待足迹图数据...</span>
      </div>
    );
  }

  const bars = mode === 'live'
    ? (fp.current_bar ? [fp.current_bar] : [])
    : fp.recent_bars.slice(-5);

  return (
    <div className="bg-surface-1 rounded-xl border border-surface-3/30 overflow-hidden h-full flex flex-col">
      {/* 标题 */}
      <div className="px-2 py-1.5 border-b border-surface-3/30 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold text-text-primary">足迹图</span>
          <span className="text-[9px] text-text-tertiary font-mono">tick={fp.tick_size}</span>
        </div>
        <div className="flex gap-0.5">
          {(['live', 'hist'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors
                ${mode === m ? 'bg-info/20 text-info font-semibold' : 'text-text-tertiary hover:text-text-secondary'}`}
            >
              {m === 'live' ? '实时' : '历史'}
            </button>
          ))}
        </div>
      </div>

      {ice && <IcebergBanner ice={ice} />}

      <div className="p-1.5 space-y-1.5 flex-1">
        {bars.length === 0 ? (
          <div className="text-[10px] text-text-tertiary text-center py-6">等待蜡烛数据...</div>
        ) : bars.map((b, i) => (
          <BarView key={b.open_ms} bar={b} maxVol={maxVol} isActive={mode === 'live' && i === 0} />
        ))}
      </div>
    </div>
  );
}
