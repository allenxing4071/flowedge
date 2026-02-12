/**
 * FlowEdge 交易驾驶舱 — 主页 v3.3
 *
 * 布局：
 *   顶部：Header
 *   第一行：信号分布概览
 *   第二行：信号卡片（内嵌门卫状态）
 *   第三行：订单流可视化（Tab 切换币种，全宽 Tape + DOM + Footprint）
 *   第四行：纸盘交易（信号验证）
 *   第五行：质量看板（门卫健康诊断）
 *   折叠区：特征热力图 / 胜率追踪 / 异常告警 / 推送器
 *
 * 注：持仓/交易功能由 KKline 承担，FlowEdge 专注信号+订单流分析+纸盘验证。
 */

'use client';

import { useState } from 'react';
import Header from '@/components/Header';
import SignalCard from '@/components/SignalCard';
import SignalSummary from '@/components/SignalSummary';
import FactorBreakdown from '@/components/FactorBreakdown';
import FeatureHeatmap from '@/components/FeatureHeatmap';
import AnomalyPanel from '@/components/AnomalyPanel';
import PerformancePanel from '@/components/PerformancePanel';
import PusherPanel from '@/components/PusherPanel';
import PaperTradingPanel from '@/components/PaperTradingPanel';
import TapePanel from '@/components/TapePanel';
import DOMHeatmap from '@/components/DOMHeatmap';
import FootprintPanel from '@/components/FootprintPanel';
import KlinePanel from '@/components/KlinePanel';
import QualityBoardPanel from '@/components/QualityBoardPanel';
import { useDashboard, useGateStatus, DashboardData, GateStatusMap, SymbolSignal } from '@/lib/hooks';
import { SafePanel } from '@/components/ErrorBoundary';

// ── 可折叠面板容器 ──

function CollapsibleSection({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between py-3 px-1 text-sm text-text-tertiary hover:text-text-secondary transition-colors group"
      >
        <span className="font-medium">{title}</span>
        <span className={`text-xs transition-transform duration-200 ${open ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </button>
      {open && <div className="animate-fade-in">{children}</div>}
    </div>
  );
}

// ── 订单流 Tab 切换器 + 三栏面板 ──

function OrderFlowSection({
  symbolMap,
}: {
  symbolMap: Record<string, SymbolSignal>;
}) {
  // 按字母排序，保证顺序固定不跳动
  const symbolNames = Object.keys(symbolMap).sort();
  const [activeSymbol, setActiveSymbol] = useState<string>('');

  // 初始化 / 防止选中的币种被移除
  const currentSymbol = symbolNames.includes(activeSymbol) ? activeSymbol : symbolNames[0] || '';

  if (symbolNames.length === 0 || !currentSymbol) return null;

  const currentSignal = symbolMap[currentSymbol];

  return (
    <div className="space-y-3">
      {/* 标题 + Tab 切换 */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-text-primary">订单流</span>
          <span className="text-xxs text-text-tertiary">订单流可视化</span>
        </div>

        {/* 币种 Tab — 用 symbol 名切换，不用索引 */}
        {symbolNames.length > 1 && (
          <div className="flex items-center gap-1 bg-surface-2/50 rounded-lg p-0.5">
            {symbolNames.map((sym) => {
              const sig = symbolMap[sym];
              const isActive = sym === currentSymbol;
              const signalColor = sig.signal.includes('BUY')
                ? 'text-bull'
                : sig.signal.includes('SELL')
                  ? 'text-bear'
                  : 'text-text-secondary';

              return (
                <button
                  key={sym}
                  onClick={() => setActiveSymbol(sym)}
                  className={`px-3 py-1.5 text-xs rounded-md transition-all ${
                    isActive
                      ? 'bg-surface-1 text-text-primary shadow-sm font-medium'
                      : 'text-text-tertiary hover:text-text-secondary hover:bg-surface-1/50'
                  }`}
                >
                  <span className="font-mono">{sym.replace('USDT', '')}</span>
                  {isActive && (
                    <span className={`ml-1.5 text-xxs ${signalColor}`}>
                      {currentSignal.score > 0 ? '+' : ''}{(currentSignal.score * 100).toFixed(0)}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 订单流布局：2x2 对齐网格（同一行高度一致） */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 lg:gap-3 items-stretch">
        <div className="h-full">
          <SafePanel name={`深度盘口 ${currentSymbol}`}>
            <DOMHeatmap symbol={currentSymbol} key={`dom-${currentSymbol}`} />
          </SafePanel>
        </div>
        <div className="h-full">
          <SafePanel name={`K线图 ${currentSymbol}`}>
            <KlinePanel symbol={currentSymbol} key={`kline-${currentSymbol}`} />
          </SafePanel>
        </div>
        <div className="h-full">
          <SafePanel name={`逐笔成交 ${currentSymbol}`}>
            <TapePanel symbol={currentSymbol} key={`tape-${currentSymbol}`} />
          </SafePanel>
        </div>
        <div className="h-full">
          <SafePanel name={`足迹图 ${currentSymbol}`}>
            <FootprintPanel symbol={currentSymbol} key={`fp-${currentSymbol}`} />
          </SafePanel>
        </div>
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
      <div className="relative">
        <div className="h-12 w-12 rounded-xl bg-gradient-to-br from-info to-bull flex items-center justify-center animate-pulse">
          <span className="text-sm font-bold text-white">FE</span>
        </div>
      </div>
      <div className="text-sm text-text-secondary">连接 FlowEdge...</div>
      <div className="text-xxs text-text-tertiary">正在获取实时数据</div>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
      <div className="h-12 w-12 rounded-xl bg-bear/20 flex items-center justify-center">
        <span className="text-lg text-bear">!</span>
      </div>
      <div className="text-sm text-bear font-medium">连接失败</div>
      <div className="text-xs text-text-tertiary max-w-md text-center">{message}</div>
      <div className="text-xxs text-text-tertiary mt-2">
        请确保 FlowEdge 后端正在运行 (默认端口 8005)
      </div>
    </div>
  );
}

function Dashboard({ data, gateStatus }: { data: DashboardData; gateStatus: GateStatusMap | null }) {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);

  // 按字母排序，保证顺序固定不随 score 实时变化而跳动
  const symbols = Object.entries(data.symbols).sort((a, b) => {
    return a[0].localeCompare(b[0]);
  });

  return (
    <div className="w-full px-3 sm:px-4 lg:px-6 py-3 sm:py-4 space-y-4 sm:space-y-5">

      {/* ═══ 第一行：信号分布概览 ═══ */}
      <SignalSummary data={data} />

      {/* ═══ 第二行：信号卡片（内嵌门卫状态） ═══ */}
      <div className={`grid gap-3 sm:gap-5 ${
        symbols.length <= 2
          ? 'grid-cols-1 sm:grid-cols-2'
          : 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-3'
      }`}>
        {symbols.map(([symbol, signalData]) => (
          <SafePanel key={symbol} name={`信号卡片 ${symbol}`}>
            <SignalCard
              symbol={symbol}
              data={signalData}
              gate={gateStatus?.[symbol] || null}
              onClick={setSelectedSymbol}
            />
          </SafePanel>
        ))}
      </div>

      {/* 因子分解面板（点击信号卡片时展开） */}
      {selectedSymbol && (
        <SafePanel name="因子分解">
          <FactorBreakdown
            symbol={selectedSymbol}
            onClose={() => setSelectedSymbol(null)}
          />
        </SafePanel>
      )}

      {/* ═══ 第三行：订单流可视化（Tab 切换币种，全宽） ═══ */}
      <OrderFlowSection symbolMap={data.symbols} />

      {/* ═══ 纸盘交易（信号验证） ═══ */}
      <SafePanel name="纸盘交易">
        <PaperTradingPanel />
      </SafePanel>

      {/* ═══ 质量看板 ═══ */}
      <SafePanel name="质量看板">
        <QualityBoardPanel />
      </SafePanel>

      {/* ═══ 折叠区：次要面板 ═══ */}
      <div className="space-y-1 border-t border-surface-3/30 pt-3 sm:pt-4">
        <div className="text-xxs text-text-tertiary uppercase tracking-wider mb-2 px-1">
          详细分析
        </div>

        <CollapsibleSection title="特征热力图">
          <SafePanel name="特征热力图">
            <FeatureHeatmap />
          </SafePanel>
        </CollapsibleSection>

        <CollapsibleSection title="信号胜率追踪">
          <SafePanel name="胜率追踪">
            <PerformancePanel />
          </SafePanel>
        </CollapsibleSection>

        <CollapsibleSection title="异常告警">
          <SafePanel name="异常告警">
            <AnomalyPanel />
          </SafePanel>
        </CollapsibleSection>

        <CollapsibleSection title="半自动推送器">
          <SafePanel name="推送器">
            <PusherPanel />
          </SafePanel>
        </CollapsibleSection>
      </div>
    </div>
  );
}

export default function Home() {
  const { data, error, loading } = useDashboard(1000);
  const { data: gateStatus } = useGateStatus(2000);

  return (
    <div className="min-h-screen bg-surface-0">
      <Header />
      {loading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState message={error} />
      ) : data ? (
        <Dashboard data={data} gateStatus={gateStatus} />
      ) : (
        <LoadingState />
      )}
    </div>
  );
}
