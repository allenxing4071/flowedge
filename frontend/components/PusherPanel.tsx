/**
 * 半自动推送器控制面板 — 控制 FlowEdge 信号自动推送到 KKline 的行为。
 *
 * 功能：
 *   - 开关推送器
 *   - 调整置信度阈值
 *   - 切换 semi-auto / auto 模式
 *   - 查看推送历史和统计
 */

'use client';

import { useState, useEffect, useCallback, useRef } from 'react';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8005';

interface PusherStatus {
  enabled: boolean;
  mode: string;
  confidence_threshold: number;
  cooldown_s: number;
  push_signals: string[];
  default_leverage: number;
  default_position_pct: number;
  default_stop_loss_pct: number;
  stats: {
    total_pushes: number;
    successful: number;
    failed: number;
  };
  recent_pushes: Array<{
    symbol: string;
    signal: string;
    score: number;
    confidence: number;
    success: boolean;
    error: string | null;
    kkline_signal_id: number | null;
    push_time: number;
  }>;
}

async function fetchPusherStatus(): Promise<PusherStatus> {
  const res = await fetch(`${API_BASE}/pusher/status`);
  return res.json();
}

async function updatePusherConfig(updates: Record<string, unknown>): Promise<PusherStatus> {
  const res = await fetch(`${API_BASE}/pusher/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  return res.json();
}

export default function PusherPanel() {
  const [status, setStatus] = useState<PusherStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const mountedRef = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchPusherStatus();
      if (mountedRef.current) {
        setStatus(data);
        setLoading(false);
      }
    } catch {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      mountedRef.current = false;
      clearInterval(timer);
    };
  }, [refresh]);

  const handleUpdate = async (updates: Record<string, unknown>) => {
    setUpdating(true);
    try {
      const data = await updatePusherConfig(updates);
      setStatus(data);
    } catch {
      /* 静默失败 */
    }
    setUpdating(false);
  };

  if (loading || !status) {
    return (
      <div className="card p-6 animate-pulse">
        <div className="h-6 bg-surface-2 rounded w-48 mb-4" />
        <div className="h-10 bg-surface-2 rounded" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-text-primary">半自动推送</h2>
          <p className="text-sm text-text-tertiary mt-0.5">
            强信号自动提交到 KKline，人工审批后执行交易
          </p>
        </div>
        {/* 开关 */}
        <button
          onClick={() => handleUpdate({ enabled: !status.enabled })}
          disabled={updating}
          title={status.enabled ? '关闭推送器' : '开启推送器'}
          className={`relative w-14 h-7 rounded-full transition-colors ${
            status.enabled ? 'bg-bull' : 'bg-surface-3'
          }`}
        >
          <span
            className={`absolute top-0.5 w-6 h-6 bg-white rounded-full shadow transition-transform ${
              status.enabled ? 'translate-x-7' : 'translate-x-0.5'
            }`}
          />
        </button>
      </div>

      {/* 配置 */}
      <div className="card p-6 space-y-5">
        <h3 className="text-base font-semibold text-text-primary">推送配置</h3>

        {/* 模式 */}
        <div>
          <label className="text-sm text-text-secondary mb-2 block">执行模式</label>
          <div className="flex gap-2">
            {[
              { value: 'semi-auto', label: '半自动', desc: '推送后需人工审批' },
              { value: 'auto', label: '全自动', desc: '推送后直接执行' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => handleUpdate({ mode: opt.value })}
                disabled={updating}
                className={`flex-1 p-3 rounded-lg text-left transition-colors ${
                  status.mode === opt.value
                    ? 'bg-info/10 border border-info/30 text-info'
                    : 'bg-surface-2 text-text-secondary hover:bg-surface-3'
                }`}
              >
                <div className="text-sm font-medium">{opt.label}</div>
                <div className="text-xs text-text-tertiary mt-0.5">{opt.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 置信度阈值 */}
        <div>
          <label className="text-sm text-text-secondary mb-2 block">
            置信度阈值: <span className="text-text-primary font-bold">
              {(status.confidence_threshold * 100).toFixed(0)}%
            </span>
          </label>
          <input
            type="range"
            min={30}
            max={90}
            value={status.confidence_threshold * 100}
            onChange={(e) =>
              handleUpdate({ confidence_threshold: Number(e.target.value) / 100 })
            }
            title="置信度阈值"
            className="w-full accent-info"
          />
          <div className="flex justify-between text-xs text-text-tertiary mt-1">
            <span>30%（激进）</span>
            <span>60%（默认）</span>
            <span>90%（保守）</span>
          </div>
        </div>

        {/* 默认杠杆 */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-sm text-text-secondary mb-2 block">
              默认杠杆: <span className="text-text-primary font-bold">{status.default_leverage}x</span>
            </label>
            <input
              type="range"
              min={1}
              max={20}
              value={status.default_leverage}
              onChange={(e) => handleUpdate({ default_leverage: Number(e.target.value) })}
              title="默认杠杆"
              className="w-full accent-info"
            />
          </div>
          <div>
            <label className="text-sm text-text-secondary mb-2 block">
              仓位占比: <span className="text-text-primary font-bold">{status.default_position_pct}%</span>
            </label>
            <input
              type="range"
              min={1}
              max={10}
              step={0.5}
              value={status.default_position_pct}
              onChange={(e) => handleUpdate({ default_position_pct: Number(e.target.value) })}
              title="仓位占比"
              className="w-full accent-info"
            />
          </div>
        </div>
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">总推送</div>
          <div className="text-xl font-bold mono-num text-text-primary">
            {status.stats.total_pushes}
          </div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">成功</div>
          <div className="text-xl font-bold mono-num text-bull">
            {status.stats.successful}
          </div>
        </div>
        <div className="card p-4 text-center">
          <div className="text-xs text-text-tertiary mb-1">失败</div>
          <div className="text-xl font-bold mono-num text-bear">
            {status.stats.failed}
          </div>
        </div>
      </div>

      {/* 最近推送记录 */}
      {status.recent_pushes.length > 0 && (
        <div className="card p-6">
          <h3 className="text-base font-semibold text-text-primary mb-4">最近推送</h3>
          <div className="space-y-2">
            {status.recent_pushes.map((p, i) => (
              <div
                key={i}
                className="flex items-center gap-3 py-2 border-b border-surface-2/50 last:border-0 text-sm"
              >
                <span className={`w-2 h-2 rounded-full ${p.success ? 'bg-bull' : 'bg-bear'}`} />
                <span className="font-medium text-text-primary w-20">
                  {p.symbol.replace('USDT', '')}
                </span>
                <span className={`font-medium ${
                  p.signal.includes('BUY') ? 'text-bull' : 'text-bear'
                }`}>
                  {p.signal}
                </span>
                <span className="mono-num text-text-secondary">
                  conf={((p.confidence) * 100).toFixed(0)}%
                </span>
                {p.kkline_signal_id && (
                  <span className="text-xs text-info">
                    #{p.kkline_signal_id}
                  </span>
                )}
                {p.error && (
                  <span className="text-xs text-bear truncate max-w-[200px]">
                    {p.error}
                  </span>
                )}
                <span className="ml-auto text-xs text-text-tertiary mono-num">
                  {new Date(p.push_time * 1000).toLocaleTimeString('zh-CN', {
                    hour12: false,
                    timeZone: 'Asia/Shanghai',
                  })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
