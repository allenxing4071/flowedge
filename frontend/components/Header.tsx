/**
 * 顶部导航栏 — 系统状态 + 连接指示器
 * 参考 KKline：h-14，字体 text-sm/base，间距宽裕
 */

'use client';

import Link from 'next/link';
import { usePolling } from '@/lib/hooks';
import { fetchHealth, fetchStatus } from '@/lib/api';

interface SystemStatus {
  version: string;
  uptime_s: number;
  symbols: string[];
  symbol_count: number;
  feature_count: number;
  total_messages: number;
  msg_rate_approx: number;
  data_sources: Record<string, unknown>;
  subscribers: number;
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}小时${m}分`;
  return `${m}分`;
}

function formatRate(rate: number): string {
  if (rate >= 1000) return `${(rate / 1000).toFixed(1)}千/秒`;
  return `${rate.toFixed(0)}/秒`;
}

export default function Header() {
  const health = usePolling(fetchHealth, 5000);
  const status = usePolling<SystemStatus>(fetchStatus, 3000);

  const isConnected = !health.error && health.data;
  const st = status.data;

  return (
    <header className="sticky top-0 z-50 border-b border-surface-3/50 bg-surface-0/80 backdrop-blur-xl">
      <div className="mx-auto max-w-[1920px] px-3 sm:px-6 lg:px-8">
        <div className="flex h-12 sm:h-14 items-center justify-between">
          {/* 左：品牌 */}
          <div className="flex items-center gap-2 sm:gap-4">
            <div className="flex items-center gap-2">
              <div className="h-7 w-7 sm:h-8 sm:w-8 rounded-lg bg-gradient-to-br from-info to-bull flex items-center justify-center">
                <span className="text-[10px] sm:text-xs font-bold text-white">FE</span>
              </div>
              <span className="text-sm sm:text-base font-semibold tracking-tight">FlowEdge</span>
              <span className="hidden sm:inline text-xs text-text-tertiary font-mono">
                v{st?.version || '...'}
              </span>
            </div>
          </div>

          {/* 导航链接 */}
          <nav className="hidden sm:flex items-center gap-1">
            <Link
              href="/evolution"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-text-secondary hover:text-text-primary hover:bg-surface-2/50 transition-colors"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 006 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0118 16.5h-2.25m-7.5 0h7.5m-7.5 0l-1 3m8.5-3l1 3m0 0l.5 1.5m-.5-1.5h-9.5m0 0l-.5 1.5M9 11.25v1.5M12 9v3.75m3-6v6" />
              </svg>
              进化
            </Link>
          </nav>

          {/* 中：关键指标（手机隐藏） */}
          <div className="hidden lg:flex items-center gap-8 text-sm text-text-secondary">
            {st && (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-text-tertiary">消息速率</span>
                  <span className="mono-num text-text-primary font-semibold text-base">
                    {formatRate(st.msg_rate_approx)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-text-tertiary">运行</span>
                  <span className="mono-num text-text-primary font-semibold text-base">
                    {formatUptime(st.uptime_s)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-text-tertiary">数据源</span>
                  <span className="mono-num text-text-primary font-semibold text-base">
                    {Object.values(st.data_sources).filter(Boolean).length}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-text-tertiary">特征</span>
                  <span className="mono-num text-text-primary font-semibold text-base">
                    {st.feature_count}
                  </span>
                </div>
              </>
            )}
          </div>

          {/* 右：连接状态 + 手机端简略指标 */}
          <div className="flex items-center gap-2 sm:gap-3">
            {/* 手机端显示消息速率 */}
            {st && (
              <span className="lg:hidden text-xxs mono-num text-text-tertiary">
                {formatRate(st.msg_rate_approx)}
              </span>
            )}
            <div className={`flex items-center gap-1.5 sm:gap-2 px-2.5 sm:px-3.5 py-1 sm:py-1.5 rounded-full text-xxs sm:text-xs font-semibold ${
              isConnected
                ? 'bg-bull-glow text-bull'
                : 'bg-bear-glow text-bear'
            }`}>
              <div className={`h-1.5 w-1.5 sm:h-2 sm:w-2 rounded-full ${
                isConnected ? 'bg-bull animate-pulse-slow' : 'bg-bear'
              }`} />
              {isConnected ? '在线' : '离线'}
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
