/**
 * FlowEdge 交易驾驶舱 — 主页
 * 布局：
 *   顶部：Header（系统状态 + 连接指示器）
 *   第一行：信号分布概览
 *   第二行：信号卡片网格 | 异常告警
 *   第三行：特征热力图
 *   底部：因子分解面板（点击信号卡片时展开）
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
import TradeDialog from '@/components/TradeDialog';
import { useDashboard, DashboardData } from '@/lib/hooks';

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

function Dashboard({ data }: { data: DashboardData }) {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [tradeTarget, setTradeTarget] = useState<{
    symbol: string;
    side: 'LONG' | 'SHORT';
  } | null>(null);

  const symbols = Object.entries(data.symbols).sort((a, b) => {
    // 按信号强度排序：强信号优先
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
      {/* 第一行：信号分布概览 */}
      <SignalSummary data={data} />

      {/* 第二行：信号卡片网格 + 异常面板 */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* 信号卡片网格 */}
        <div className="lg:col-span-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-5">
            {symbols.map(([symbol, signalData]) => (
              <SignalCard
                key={symbol}
                symbol={symbol}
                data={signalData}
                onClick={setSelectedSymbol}
                onTrade={handleTrade}
              />
            ))}
          </div>
        </div>

        {/* 异常面板 */}
        <div className="lg:col-span-1">
          <AnomalyPanel />
        </div>
      </div>

      {/* 因子分解面板（点击展开） */}
      {selectedSymbol && (
        <FactorBreakdown
          symbol={selectedSymbol}
          onClose={() => setSelectedSymbol(null)}
        />
      )}

      {/* 持仓 + 余额面板 */}
      <PositionsPanel />

      {/* 特征热力图 */}
      <FeatureHeatmap />

      {/* 信号胜率追踪面板 */}
      <PerformancePanel />

      {/* 半自动推送器控制面板 */}
      <PusherPanel />

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

  return (
    <div className="min-h-screen bg-surface-0">
      <Header />
      {loading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState message={error} />
      ) : data ? (
        <Dashboard data={data} />
      ) : (
        <LoadingState />
      )}
    </div>
  );
}
