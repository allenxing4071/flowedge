/**
 * FlowEdge 交易驾驶舱 — 主页 v3.1
 *
 * 布局优化（Google PM 审查后调整）：
 *   顶部：Header
 *   第一行：信号分布概览
 *   第二行：信号卡片（内嵌门卫状态）
 *   第三行：持仓 + 余额（真金白银优先）
 *   第四行：纸盘交易成绩单
 *   第五行：质量看板（门卫健康诊断）
 *   折叠区：特征热力图 / 胜率追踪 / 异常告警 / 推送器
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
import PositionsPanel from '@/components/PositionsPanel';
import PusherPanel from '@/components/PusherPanel';
import PaperTradingPanel from '@/components/PaperTradingPanel';
import QualityBoardPanel from '@/components/QualityBoardPanel';
import TradeDialog from '@/components/TradeDialog';
import { useDashboard, useGateStatus, DashboardData, GateStatusMap } from '@/lib/hooks';
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
  const [tradeTarget, setTradeTarget] = useState<{
    symbol: string;
    side: 'LONG' | 'SHORT';
  } | null>(null);

  const symbols = Object.entries(data.symbols).sort((a, b) => {
    return Math.abs(b[1].score) - Math.abs(a[1].score);
  });

  const handleTrade = (symbol: string, side: 'LONG' | 'SHORT') => {
    const signalData = data.symbols[symbol];
    if (signalData) {
      setTradeTarget({ symbol, side });
    }
  };

  return (
    <div className="mx-auto max-w-[1920px] px-6 lg:px-8 py-6 space-y-6">

      {/* ═══ 第一行：信号分布概览 ═══ */}
      <SignalSummary data={data} />

      {/* ═══ 第二行：信号卡片（内嵌门卫状态） ═══ */}
      <div className={`grid gap-5 ${
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
              onTrade={handleTrade}
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

      {/* ═══ 第三行：持仓 + 余额（真金白银优先） ═══ */}
      <SafePanel name="持仓面板">
        <PositionsPanel />
      </SafePanel>

      {/* ═══ 第四行：纸盘交易成绩单 ═══ */}
      <SafePanel name="纸盘交易">
        <PaperTradingPanel />
      </SafePanel>

      {/* ═══ 第五行：质量看板 ═══ */}
      <SafePanel name="质量看板">
        <QualityBoardPanel />
      </SafePanel>

      {/* ═══ 折叠区：次要面板 ═══ */}
      <div className="space-y-1 border-t border-surface-3/30 pt-4">
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

      {/* 交易确认对话框 */}
      {tradeTarget && (
        <TradeDialog
          symbol={tradeTarget.symbol}
          side={tradeTarget.side}
          score={data.symbols[tradeTarget.symbol]?.score || 0}
          confidence={data.symbols[tradeTarget.symbol]?.confidence || 0}
          onClose={() => setTradeTarget(null)}
          onSuccess={() => {
            setTradeTarget(null);
          }}
        />
      )}
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
