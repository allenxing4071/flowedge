/**
 * 纸盘交易面板 — 专业量化交易驾驶舱
 *
 * 设计参考：KKline Admin Dashboard + TradingView Strategy Tester
 * 布局（左右分栏）：
 *   顶部：核心指标栏（6 格）
 *   左栏(60%)：实时持仓（详细版）+ 条件委托 TP/SL + 交易历史
 *   右栏(40%)：信号决策记录（DecisionCard 风格）
 */

'use client';

import { useState, useCallback } from 'react';
import { usePolling } from '@/lib/hooks';
import {
  fetchPaperStatus,
  fetchPaperTrades,
  fetchPaperSignalLog,
  resetPaperAccount,
} from '@/lib/api';

// ══════════════════════════════════════════
// 类型定义
// ══════════════════════════════════════════

interface PaperPosition {
  symbol: string;
  side: string;
  entry_price: number;
  mark_price: number;
  quantity: number;
  notional: number;
  leverage: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss_price: number;
  signal: string;
  confidence: number;
  duration_s: number;
}

interface PaperAccount {
  initial_balance: number;
  balance: number;
  equity: number;
  unrealized_pnl: number;
  return_pct: number;
}

interface PaperStats {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_net_pnl: number;
  total_fee_cost: number;
  avg_pnl_pct: number;
  avg_win_pct: number;
  avg_loss_pct: number;
  profit_factor: number;
  max_drawdown_pct: number;
  sharpe_ratio: number;
}

interface PaperStatus {
  config: Record<string, unknown>;
  account: PaperAccount;
  positions: PaperPosition[];
  stats: PaperStats;
}

interface PaperTrade {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  net_pnl: number;
  net_pnl_pct: number;
  gross_pnl: number;
  fee_cost: number;
  duration_s: number;
  exit_reason: string;
  signal_entry: string;
  signal_exit: string;
  confidence_entry: number;
  score_entry: number;
  entry_time: number;
  exit_time: number;
  leverage: number;
  notional: number;
  max_pnl_pct: number;
  min_pnl_pct: number;
}

interface SignalLogEntry {
  ts: number;
  symbol: string;
  signal: string;
  score: number;
  confidence: number;
  action: string;
  reason: string;
  detail?: {
    side?: string;
    entry_price?: number;
    stop_loss?: number;
    take_profit_pct?: number;
    notional?: number;
    margin?: number;
    leverage?: number;
  };
}

// ══════════════════════════════════════════
// 工具函数
// ══════════════════════════════════════════

function fmtDur(s: number): string {
  if (s < 5) return '<5秒';
  if (s < 60) return `${Math.round(s)}秒`;
  if (s < 3600) return `${Math.round(s / 60)}分钟`;
  if (s < 86400) {
    const h = Math.floor(s / 3600);
    const m = Math.round((s % 3600) / 60);
    return m > 0 ? `${h}小时${m}分` : `${h}小时`;
  }
  return `${(s / 86400).toFixed(1)}天`;
}

function fmtTime(ts: number): string {
  if (!ts) return '--:--';
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', {
    hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
    timeZone: 'Asia/Shanghai',
  });
}

function fmtPrice(p: number | null | undefined): string {
  if (p == null || isNaN(p)) return '--';
  if (p >= 1000) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(6);
}

function safe(v: number | null | undefined, digits = 2, fallback = '--'): string {
  if (v == null || isNaN(v)) return fallback;
  return v.toFixed(digits);
}

function safePct(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(v)) return '--';
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`;
}

function safeDollar(v: number | null | undefined, digits = 2): string {
  if (v == null || isNaN(v)) return '--';
  return `$${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;
}

function sideText(side: string): string {
  if (side === 'LONG' || side === 'BUY') return '多头';
  if (side === 'SHORT' || side === 'SELL') return '空头';
  return side || '--';
}

const EXIT_LABELS: Record<string, { text: string; color: string }> = {
  signal_reverse: { text: '信号反转', color: 'text-info' },
  signal_neutral: { text: '回到中性', color: 'text-text-secondary' },
  stop_loss: { text: '触发止损', color: 'text-bear' },
  take_profit: { text: '触发止盈', color: 'text-bull' },
  trailing_stop: { text: '追踪止盈', color: 'text-bull' },
  manual: { text: '手动平仓', color: 'text-warn' },
};

const SIGNAL_LABELS: Record<string, { text: string; color: string }> = {
  STRONG_BUY: { text: '强烈看多', color: 'text-bull' },
  BUY: { text: '看多', color: 'text-bull/70' },
  SELL: { text: '看空', color: 'text-bear/70' },
  STRONG_SELL: { text: '强烈看空', color: 'text-bear' },
  NEUTRAL: { text: '中性', color: 'text-text-tertiary' },
};

/** 信号动作的颜色和图标映射 */
const ACTION_META: Record<string, { label: string; color: string; borderColor: string; icon: string }> = {
  open:  { label: '开仓', color: 'text-bull',           borderColor: 'border-l-bull',      icon: '▶' },
  close: { label: '平仓', color: 'text-warn',           borderColor: 'border-l-warn',      icon: '■' },
  hold:  { label: '持有', color: 'text-info',            borderColor: 'border-l-info/50',   icon: '⏸' },
  skip:  { label: '跳过', color: 'text-text-tertiary',   borderColor: 'border-l-surface-3', icon: '○' },
};

// ══════════════════════════════════════════
// 子组件：核心指标卡
// ══════════════════════════════════════════

function MetricCard({ label, value, sub, color, large }: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  large?: boolean;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-xxs text-text-tertiary uppercase tracking-wider">{label}</span>
      <span className={`${large ? 'text-xl' : 'text-base'} font-bold tabular-nums leading-tight ${color || 'text-text-primary'}`}>
        {value}
      </span>
      {sub && <span className="text-xxs text-text-tertiary">{sub}</span>}
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：胜负序列图
// ══════════════════════════════════════════

function WinLossStreak({ trades }: { trades: PaperTrade[] }) {
  if (!trades.length) return null;
  const recent = [...trades].reverse().slice(-20);
  return (
    <div className="flex items-center gap-1">
      <span className="text-xxs text-text-tertiary mr-1 flex-shrink-0">近{recent.length}笔</span>
      {recent.map((t, i) => {
        const w = t.net_pnl > 0;
        return (
          <div
            key={i}
            title={`${t.symbol} ${t.side} ${w ? '盈' : '亏'} ${safePct(t.net_pnl_pct, 1)}`}
            className={`w-2.5 h-2.5 rounded-sm transition-all ${w ? 'bg-bull' : 'bg-bear/70'}`}
          />
        );
      })}
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：增强版持仓卡片（学习 KKline）
// ══════════════════════════════════════════

function ActivePosition({ pos, config }: { pos: PaperPosition; config: Record<string, unknown> }) {
  const isLong = pos.side === 'LONG';
  const pnl = pos.unrealized_pnl;
  const pnlPct = pos.unrealized_pnl_pct;
  const pnlColor = pnl >= 0 ? 'text-bull' : 'text-bear';
  const bgColor = pnl >= 0 ? 'border-bull/20 bg-bull/5' : 'border-bear/20 bg-bear/5';
  const sig = SIGNAL_LABELS[pos.signal] || { text: pos.signal, color: 'text-text-secondary' };

  // 计算止盈价（基于 config）
  const tpPct = (config?.take_profit_pct as number) || 0;
  const tpPrice = tpPct > 0
    ? (isLong ? pos.entry_price * (1 + tpPct / 100) : pos.entry_price * (1 - tpPct / 100))
    : null;

  // 保证金 = notional / leverage
  const margin = pos.notional / pos.leverage;

  return (
    <div className={`border rounded-lg ${bgColor}`}>
      {/* 顶部行：币种 + 方向 + 盈亏 */}
      <div className="flex items-center justify-between px-3 sm:px-4 pt-2.5 sm:pt-3 pb-1.5 sm:pb-2">
        <div className="flex items-center gap-1.5 sm:gap-2">
          <span className={`px-1.5 sm:px-2 py-0.5 rounded text-xxs font-bold tracking-wider ${
            isLong ? 'bg-bull/20 text-bull' : 'bg-bear/20 text-bear'
          }`}>
            {isLong ? '多头' : '空头'}
          </span>
          <span className="text-xs sm:text-sm font-semibold text-text-primary">{pos.symbol}</span>
          <span className="text-xxs text-text-tertiary">{pos.leverage}x</span>
        </div>
        <div className="text-right">
          <div className={`text-base sm:text-lg font-bold tabular-nums ${pnlColor}`}>
            {safePct(pnlPct)}
          </div>
          <div className={`text-xxs sm:text-xs tabular-nums ${pnlColor}`}>
            {safe(pnl)} USDT
          </div>
        </div>
      </div>

      {/* 详情网格：2行4列 */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 sm:gap-x-4 gap-y-1.5 sm:gap-y-2 px-3 sm:px-4 pb-2 text-xxs sm:text-xs">
        <div>
          <div className="text-text-tertiary text-xxs">持仓数量</div>
          <div className="text-text-secondary tabular-nums">{safe(pos.quantity, 4)}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">入场价</div>
          <div className="text-text-secondary tabular-nums">${fmtPrice(pos.entry_price)}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">标记价</div>
          <div className="text-text-primary tabular-nums font-medium">${fmtPrice(pos.mark_price)}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">止损价</div>
          <div className="text-bear tabular-nums">${fmtPrice(pos.stop_loss_price)}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">名义价值</div>
          <div className="text-text-secondary tabular-nums">${pos.notional != null ? Math.round(pos.notional).toLocaleString() : '--'}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">保证金</div>
          <div className="text-text-secondary tabular-nums">${safe(margin, 2)}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">止盈价</div>
          <div className="text-bull tabular-nums">{tpPrice ? `$${fmtPrice(tpPrice)}` : '--'}</div>
        </div>
        <div>
          <div className="text-text-tertiary text-xxs">持仓时长</div>
          <div className="text-text-secondary">{fmtDur(pos.duration_s)}</div>
        </div>
      </div>

      {/* 底部：入场信号 + confidence */}
      <div className="flex items-center justify-between px-3 sm:px-4 py-1.5 sm:py-2 border-t border-surface-3/30 text-xxs text-text-tertiary">
        <span>信号: <span className={sig.color}>{sig.text}</span></span>
        <span>置信度 <span className="text-text-secondary">{safe((pos.confidence ?? 0) * 100, 0)}%</span></span>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：条件委托 TP/SL 列表
// ══════════════════════════════════════════

function TPSLTable({ positions, config }: { positions: PaperPosition[]; config: Record<string, unknown> }) {
  if (!positions.length) return null;

  const tpPct = (config?.take_profit_pct as number) || 0;
  const trailingAct = (config?.trailing_activate_pct as number) || 0;

  // 从每个持仓生成委托条目
  const orders: { symbol: string; type: string; typeColor: string; price: string; status: string }[] = [];
  for (const pos of positions) {
    const isLong = pos.side === 'LONG';
    // 止损
    orders.push({
      symbol: pos.symbol,
      type: '止损',
      typeColor: 'text-bear',
      price: `$${fmtPrice(pos.stop_loss_price)}`,
      status: '待触发',
    });
    // 固定止盈
    if (tpPct > 0) {
      const tp = isLong ? pos.entry_price * (1 + tpPct / 100) : pos.entry_price * (1 - tpPct / 100);
      orders.push({
        symbol: pos.symbol,
        type: '止盈',
        typeColor: 'text-bull',
        price: `$${fmtPrice(tp)}`,
        status: '待触发',
      });
    }
    // 追踪止盈
    if (trailingAct > 0) {
      orders.push({
        symbol: pos.symbol,
        type: '追踪止盈',
        typeColor: 'text-bull',
        price: `激活于 +${trailingAct}%`,
        status: pos.unrealized_pnl_pct >= trailingAct ? '已激活' : '待激活',
      });
    }
  }

  return (
    <div>
      <div className="text-xxs font-medium text-text-tertiary uppercase tracking-wider mb-2">
        条件委托 (TP/SL)
        <span className="ml-2 text-text-tertiary/60">{orders.length} 条</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-text-tertiary text-xxs border-b border-surface-3/50">
              <th className="text-left py-1.5 font-normal">交易对</th>
              <th className="text-left py-1.5 font-normal">类型</th>
              <th className="text-right py-1.5 font-normal">触发价格</th>
              <th className="text-right py-1.5 font-normal">状态</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o, i) => (
              <tr key={i} className="border-b border-surface-3/30 last:border-0">
                <td className="py-1.5 text-text-primary font-medium">{o.symbol}</td>
                <td className={`py-1.5 ${o.typeColor}`}>{o.type}</td>
                <td className="py-1.5 text-right tabular-nums text-text-secondary">{o.price}</td>
                <td className="py-1.5 text-right text-text-tertiary">{o.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：信号决策卡片（学习 KKline DecisionCard）
// ══════════════════════════════════════════

function SignalLogCard({ entry }: { entry: SignalLogEntry }) {
  const meta = ACTION_META[entry.action] || ACTION_META.skip;
  const sig = SIGNAL_LABELS[entry.signal] || { text: entry.signal, color: 'text-text-tertiary' };

  return (
    <div className={`border-l-2 ${meta.borderColor} bg-surface-1 rounded-r-lg px-3 py-2.5 animate-fade-in`}>
      {/* 顶部行：时间 + 动作标签 + score/confidence */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-xs opacity-60">{meta.icon}</span>
          <span className={`text-xxs font-bold ${meta.color}`}>{meta.label}</span>
          <span className={`px-1.5 py-0.5 rounded text-xxs font-medium ${sig.color} bg-surface-2`}>
            {sig.text}
          </span>
          <span className="text-xxs text-text-tertiary tabular-nums">{fmtTime(entry.ts)}</span>
        </div>
        <div className="flex items-center gap-2 text-xxs tabular-nums">
          <span className="text-text-tertiary">评分</span>
          <span className={entry.score >= 0 ? 'text-bull' : 'text-bear'}>{safe(entry.score, 3)}</span>
          <span className="text-text-tertiary">|</span>
          <span className="text-text-tertiary">置信</span>
          <span className={entry.confidence >= 0.4 ? 'text-text-primary' : 'text-text-tertiary'}>
            {safe(entry.confidence * 100, 0)}%
          </span>
        </div>
      </div>

      {/* 币种 */}
      <div className="flex items-center gap-2 mb-1">
        <span className="text-sm font-semibold text-text-primary">{entry.symbol}</span>
      </div>

      {/* 开仓详情（仅 action=open 时显示） */}
      {entry.detail && entry.action === 'open' && (
        <div className="grid grid-cols-3 gap-2 text-xxs mt-1 mb-1">
          <div>
            <span className="text-text-tertiary">入场价 </span>
            <span className="text-text-primary tabular-nums">${fmtPrice(entry.detail.entry_price)}</span>
          </div>
          <div>
            <span className="text-text-tertiary">止损 </span>
            <span className="text-bear tabular-nums">${fmtPrice(entry.detail.stop_loss)}</span>
          </div>
          <div>
            <span className="text-text-tertiary">仓位 </span>
            <span className="text-text-secondary tabular-nums">
              ${entry.detail.notional != null ? Math.round(entry.detail.notional).toLocaleString() : '--'}
            </span>
          </div>
        </div>
      )}

      {/* 决策理由 */}
      <div className="text-xxs text-text-tertiary leading-relaxed">
        {entry.reason}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════
// 子组件：交易记录卡片
// ══════════════════════════════════════════

function TradeCard({ trade }: { trade: PaperTrade }) {
  const isWin = trade.net_pnl > 0;
  const isLong = trade.side === 'LONG';
  const pnlColor = isWin ? 'text-bull' : 'text-bear';
  const borderColor = isWin ? 'border-l-bull' : 'border-l-bear/60';
  const exit = EXIT_LABELS[trade.exit_reason] || { text: trade.exit_reason, color: 'text-text-tertiary' };
  const sig = SIGNAL_LABELS[trade.signal_entry] || { text: trade.signal_entry, color: 'text-text-secondary' };

  const priceDir = trade.exit_price > trade.entry_price ? '↑' : trade.exit_price < trade.entry_price ? '↓' : '→';
  const priceDirColor = (isLong && trade.exit_price > trade.entry_price) || (!isLong && trade.exit_price < trade.entry_price)
    ? 'text-bull' : 'text-bear';

  return (
    <div className={`border-l-2 ${borderColor} bg-surface-1 rounded-r-lg px-3 sm:px-4 py-2.5 sm:py-3 animate-fade-in`}>
      <div className="flex items-center justify-between mb-1 sm:mb-1.5 flex-wrap gap-1">
        <div className="flex items-center gap-1.5 sm:gap-2">
          <span className="text-xxs text-text-tertiary tabular-nums">
            {fmtTime(trade.exit_time)}
          </span>
          <span className="text-xs sm:text-sm font-semibold text-text-primary">{trade.symbol}</span>
          <span className={`px-1 sm:px-1.5 py-0.5 rounded text-xxs font-bold ${
            isLong ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'
          }`}>
            {sideText(trade.side)}
          </span>
        </div>
        <div className="flex items-center gap-1 sm:gap-2">
          <span className={`text-sm sm:text-base font-bold tabular-nums ${pnlColor}`}>
            {safeDollar(trade.net_pnl)}
          </span>
          <span className={`text-xxs sm:text-xs tabular-nums ${pnlColor}`}>
            ({safePct(trade.net_pnl_pct, 1)})
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between text-xxs sm:text-xs flex-wrap gap-1">
        <div className="flex items-center gap-1 sm:gap-1.5 text-text-secondary">
          <span className="tabular-nums">${fmtPrice(trade.entry_price)}</span>
          <span className={`font-medium ${priceDirColor}`}>{priceDir}</span>
          <span className="tabular-nums">${fmtPrice(trade.exit_price)}</span>
          <span className="hidden sm:inline text-text-tertiary ml-1">({trade.leverage ?? '--'}x · ${trade.notional != null ? Math.round(trade.notional).toLocaleString() : '--'})</span>
        </div>
        <span className="text-text-tertiary">{fmtDur(trade.duration_s)}</span>
      </div>

      <div className="flex items-center justify-between text-xxs text-text-tertiary mt-1 sm:mt-1.5 flex-wrap gap-1">
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <span><span className={sig.color}>{sig.text}</span> → <span className={exit.color}>{exit.text}</span></span>
        </div>
        <span>费 ${safe(trade.fee_cost)}</span>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════
// 主组件
// ══════════════════════════════════════════

export default function PaperTradingPanel() {
  const [resetting, setResetting] = useState(false);
  const [showTrades, setShowTrades] = useState(false);

  const statusFetcher = useCallback(() => fetchPaperStatus(), []);
  const tradesFetcher = useCallback(() => fetchPaperTrades(100), []);
  const signalLogFetcher = useCallback(() => fetchPaperSignalLog(50), []);

  const { data: status, error: statusError } = usePolling<PaperStatus>(statusFetcher, 3000);
  const { data: trades } = usePolling<PaperTrade[]>(tradesFetcher, 5000);
  const { data: signalLog } = usePolling<SignalLogEntry[]>(signalLogFetcher, 4000);

  const handleReset = async () => {
    if (!confirm('确认重置纸盘账户？所有模拟交易记录将被清除，初始资金恢复为 $10,000。')) return;
    setResetting(true);
    try {
      await resetPaperAccount();
    } catch (e) {
      console.error('重置失败:', e);
    }
    setResetting(false);
  };

  if (statusError || !status) {
    return (
      <div className="card p-6">
        <h3 className="text-sm font-semibold text-text-primary mb-4">纸盘交易</h3>
        <div className="text-xs text-text-tertiary text-center py-4">
          {statusError ? '连接失败' : '等待数据...'}
        </div>
      </div>
    );
  }

  const { account, stats, positions } = status;
  const cfg = status.config;
  const rp = account?.return_pct ?? 0;
  const returnColor = rp >= 0 ? 'text-bull' : 'text-bear';
  const hasTrades = (stats?.total_trades ?? 0) > 0;
  const allTrades = trades || [];
  const allSignalLog = signalLog || [];

  return (
    <div className="card p-3 sm:p-6 space-y-4 sm:space-y-5">

      {/* ── 标题栏 ── */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <h3 className="text-sm font-semibold text-text-primary">纸盘交易</h3>
          <div className="flex items-center gap-1 sm:gap-1.5 text-xxs text-text-tertiary flex-wrap">
            <span className="px-1 sm:px-1.5 py-0.5 rounded bg-surface-2">
              {(cfg.leverage as number) || 10}x
            </span>
            <span className="px-1 sm:px-1.5 py-0.5 rounded bg-surface-2">
              止损 {(cfg.stop_loss_pct as number) || 2}%
            </span>
            <span className="px-1 sm:px-1.5 py-0.5 rounded bg-surface-2">
              止盈 {(cfg.take_profit_pct as number) || 0}%
            </span>
            <span className="hidden sm:inline px-1.5 py-0.5 rounded bg-surface-2">
              冷却 {(cfg.cooldown_s as number) || 60}s
            </span>
          </div>
        </div>
        <button
          onClick={handleReset}
          disabled={resetting}
          className="text-xxs px-2 py-1 rounded bg-surface-2 text-text-tertiary hover:text-bear transition-colors disabled:opacity-50"
        >
          {resetting ? '重置中...' : '重置账户'}
        </button>
      </div>

      {/* ── 核心指标栏 ── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-4 sm:gap-x-6 gap-y-2 sm:gap-y-3 py-2 sm:py-3 border-y border-surface-3">
        <MetricCard
          label="净值"
          value={`$${(account?.equity ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`}
          sub={`初始 $${(account?.initial_balance ?? 10000).toLocaleString()}`}
          color={returnColor}
          large
        />
        <MetricCard
          label="总收益"
          value={hasTrades ? safePct(rp) : '0.00%'}
          sub={hasTrades ? `${safeDollar((account?.equity ?? 0) - (account?.initial_balance ?? 10000))} = 已平仓${safeDollar(stats?.total_net_pnl)} + 未实现${safeDollar(account?.unrealized_pnl)}` : undefined}
          color={returnColor}
          large
        />
        <MetricCard
          label="胜率"
          value={hasTrades ? `${safe(stats?.win_rate, 0)}%` : '--'}
          sub={hasTrades ? `${stats?.winning_trades ?? 0}胜 ${stats?.losing_trades ?? 0}负` : undefined}
          color={(stats?.win_rate ?? 0) >= 50 ? 'text-bull' : hasTrades ? 'text-bear' : undefined}
        />
        <MetricCard
          label="盈亏比"
          value={hasTrades ? ((stats?.profit_factor ?? 0) === Infinity ? '∞' : safe(stats?.profit_factor)) : '--'}
          sub={hasTrades ? `均盈 ${safe(stats?.avg_win_pct, 1)}% / 均亏 ${safe(stats?.avg_loss_pct, 1)}%` : undefined}
          color={(stats?.profit_factor ?? 0) >= 1 ? 'text-bull' : hasTrades ? 'text-bear' : undefined}
        />
        <MetricCard
          label="最大回撤"
          value={hasTrades ? `${safe(stats?.max_drawdown_pct, 1)}%` : '--'}
          color={(stats?.max_drawdown_pct ?? 0) > 10 ? 'text-bear' : (stats?.max_drawdown_pct ?? 0) > 5 ? 'text-warn' : 'text-text-primary'}
        />
        <MetricCard
          label="交易次数"
          value={`${stats?.total_trades ?? 0}`}
          sub={hasTrades ? `手续费 $${safe(stats?.total_fee_cost, 1)}` : undefined}
        />
      </div>

      {/* ── 胜负序列图 ── */}
      {trades && trades.length > 0 && <WinLossStreak trades={trades} />}

      {/* ══════════════════════════════════════════
          主体区域：左右分栏
          左栏(3/5)：持仓 + TP/SL + 交易历史
          右栏(2/5)：信号决策记录
         ══════════════════════════════════════════ */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

        {/* ── 左栏 ── */}
        <div className="lg:col-span-3 space-y-4">

          {/* 实时持仓 */}
          {positions.length > 0 ? (
            <div className="space-y-3">
              <div className="text-xxs font-medium text-text-tertiary uppercase tracking-wider">
                实时持仓
                <span className="ml-2 text-text-primary">{positions.length} 个持仓</span>
              </div>
              {positions.map(pos => (
                <ActivePosition key={pos.symbol} pos={pos} config={cfg} />
              ))}
            </div>
          ) : (
            <div className="text-xs text-text-tertiary py-4 text-center border border-dashed border-surface-3 rounded-lg">
              暂无持仓 — 等待信号触发
            </div>
          )}

          {/* 条件委托 TP/SL */}
          {positions.length > 0 && (
            <TPSLTable positions={positions} config={cfg} />
          )}

          {/* 交易历史（可折叠） */}
          <div>
            <button
              onClick={() => setShowTrades(!showTrades)}
              className="flex items-center justify-between w-full text-xxs font-medium text-text-tertiary uppercase tracking-wider py-2 hover:text-text-secondary transition-colors"
            >
              <span>交易记录 {hasTrades && `(共 ${stats.total_trades} 笔，已平仓合计 ${safeDollar(stats?.total_net_pnl)})`}</span>
              <span className="text-xs">{showTrades ? '▲' : '▼'}</span>
            </button>
            {showTrades && allTrades.length > 0 && (
              <div className="space-y-2 max-h-[500px] overflow-y-auto pr-1 scrollbar-thin">
                {allTrades.map(trade => (
                  <TradeCard key={trade.id} trade={trade} />
                ))}
              </div>
            )}
            {showTrades && allTrades.length === 0 && (
              <div className="text-xs text-text-tertiary text-center py-3">暂无交易记录</div>
            )}
          </div>
        </div>

        {/* ── 右栏：信号决策记录 ── */}
        <div className="lg:col-span-2">
          <div className="text-xxs font-medium text-text-tertiary uppercase tracking-wider mb-3">
            信号决策记录
            {allSignalLog.length > 0 && (
              <span className="ml-2 text-text-tertiary/60">最近 {allSignalLog.length} 条</span>
            )}
          </div>

          {allSignalLog.length > 0 ? (
            <div className="space-y-2 max-h-[700px] overflow-y-auto pr-1 scrollbar-thin">
              {allSignalLog.map((entry, i) => (
                <SignalLogCard key={`${entry.ts}-${i}`} entry={entry} />
              ))}
            </div>
          ) : (
            <div className="text-xs text-text-tertiary text-center py-8 border border-dashed border-surface-3 rounded-lg">
              <div className="text-lg opacity-30 mb-2">&#128202;</div>
              等待信号变化...
              <div className="text-xxs mt-1 opacity-60">
                每次信号引擎产生新信号时，决策过程将记录在此
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── 空状态（无持仓无交易时） ── */}
      {!hasTrades && positions.length === 0 && allSignalLog.length === 0 && (
        <div className="text-center py-6 space-y-2">
          <div className="text-2xl opacity-30">&#9202;</div>
          <div className="text-sm text-text-secondary">等待第一笔虚拟交易</div>
          <div className="text-xxs text-text-tertiary max-w-sm mx-auto leading-relaxed">
            系统会在信号从中性变为看多/看空时，自动以真实标记价格开仓。
            模拟滑点 {(cfg.slippage_pct as number) || 0.02}% + 手续费 {(cfg.fee_pct as number) || 0.02}%（双向），
            信号反转或触发止损/止盈时自动平仓。
          </div>
        </div>
      )}
    </div>
  );
}
