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
      <div className="card p-6 flex items-center justify-between">
        <div className="min-w-0">
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
          className={`relative flex-shrink-0 w-14 h-7 rounded-full transition-colors ${
            status.enabled ? 'bg-bull' : 'bg-surface-3'
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-6 h-6 bg-white rounded-full shadow transition-transform ${
              status.enabled ? 'translate-x-[26px]' : 'translate-x-0'
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

      {/* 最近推送记录（参考 KKline AI 决策记录卡片风格） */}
      {status.recent_pushes.length > 0 && (
        <div className="card p-6">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-text-primary">最近推送</h3>
            <span className="text-xs text-text-tertiary">
              共 {status.stats.total_pushes} 次
            </span>
          </div>
          <div className="space-y-3">
            {status.recent_pushes.map((p, i) => (
              <PushCard key={i} push={p} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
// 推送记录卡片（参考 KKline DecisionCard 风格）
// ═══════════════════════════════════════════

/** 错误信息友好化 — 把原始 HTTP/JSON 错误转为人话 */
function friendlyError(raw: string): string {
  if (!raw) return '未知错误';
  // HTTP 状态码
  if (raw.includes('503')) return 'KKline 服务暂时不可用（503），可能正在重启';
  if (raw.includes('502')) return 'KKline 网关错误（502），服务可能未启动';
  if (raw.includes('500')) return 'KKline 服务器内部错误（500）';
  if (raw.includes('401') || raw.includes('Unauthorized')) return 'API 认证失败，请检查 API Key';
  if (raw.includes('403') || raw.includes('Forbidden')) return '权限不足，请检查 API Key 权限';
  if (raw.includes('404')) return '接口不存在（404），请检查 KKline 版本';
  if (raw.includes('timeout') || raw.includes('Timeout')) return '请求超时，KKline 响应过慢';
  if (raw.includes('ECONNREFUSED') || raw.includes('Connection refused')) return 'KKline 连接被拒绝，服务可能未启动';
  if (raw.includes('fetch failed') || raw.includes('network')) return '网络连接失败';
  // "detail" JSON 字段提取
  const detailMatch = raw.match(/"detail"\s*:\s*"([^"]+)"/);
  if (detailMatch) return detailMatch[1];
  // 控制接口未启用
  if (raw.includes('未启用') || raw.includes('disabled')) return '推送接口未启用，请检查配置';
  // 兜底：截取前 120 字符
  return raw.length > 120 ? raw.slice(0, 120) + '…' : raw;
}

/** 信号→中文标签 + 颜色 */
const SIGNAL_STYLE: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  STRONG_BUY:  { label: '强烈看多', color: '#00ffaa', bg: 'rgba(0,255,170,0.10)', icon: '🟢' },
  BUY:         { label: '看多',     color: '#00ffaa', bg: 'rgba(0,255,170,0.06)', icon: '🟢' },
  SELL:        { label: '看空',     color: '#ff3366', bg: 'rgba(255,51,102,0.06)', icon: '🔴' },
  STRONG_SELL: { label: '强烈看空', color: '#ff3366', bg: 'rgba(255,51,102,0.10)', icon: '🔴' },
};

function PushCard({ push }: {
  push: {
    symbol: string;
    signal: string;
    score: number;
    confidence: number;
    success: boolean;
    error: string | null;
    kkline_signal_id: number | null;
    push_time: number;
  };
}) {
  const sig = SIGNAL_STYLE[push.signal] || {
    label: push.signal, color: '#94a3b8', bg: 'rgba(255,255,255,0.04)', icon: '⚪',
  };
  const isBuy = push.signal.includes('BUY');
  const borderColor = push.success ? sig.color : '#ff3366';
  const confPct = Math.round((push.confidence ?? 0) * 100);

  // 北京时间格式化
  const timeStr = push.push_time
    ? new Date(push.push_time * 1000).toLocaleTimeString('zh-CN', {
        hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
        timeZone: 'Asia/Shanghai',
      })
    : '--:--';

  return (
    <div
      className="rounded-lg overflow-hidden transition-all hover:brightness-110"
      style={{ borderLeft: `3px solid ${borderColor}`, background: 'rgba(255,255,255,0.02)' }}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        {/* 左：信号图标 */}
        <div
          className="w-8 h-8 rounded-lg flex items-center justify-center text-sm shrink-0 mt-0.5"
          style={{ background: sig.bg }}
        >
          {sig.icon}
        </div>

        {/* 中：核心信息 */}
        <div className="flex-1 min-w-0">
          {/* 第一行：标签组 */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {/* 币种 */}
            <span className="text-sm font-bold text-text-primary">
              {push.symbol.replace('USDT', '')}
            </span>
            {/* 信号方向 */}
            <span
              className="text-xs font-medium px-1.5 py-0.5 rounded"
              style={{ background: sig.bg, color: sig.color }}
            >
              {isBuy ? '▲' : '▼'} {sig.label}
            </span>
            {/* 置信度 */}
            <span
              className="text-xs font-mono"
              style={{ color: confPct >= 60 ? '#00e5ff' : '#94a3b8' }}
            >
              conf={confPct}%
            </span>
            {/* 推送结果 */}
            {push.success ? (
              <span
                className="text-xs font-medium px-1.5 py-0.5 rounded"
                style={{ background: 'rgba(0,255,170,0.08)', color: '#00ffaa' }}
              >
                ✓ 已推送
              </span>
            ) : (
              <span
                className="text-xs font-medium px-1.5 py-0.5 rounded"
                style={{ background: 'rgba(255,51,102,0.08)', color: '#ff3366' }}
              >
                ✕ 失败
              </span>
            )}
          </div>

          {/* 第二行：详情 */}
          <div className="mt-1.5 flex items-center gap-3 text-xs flex-wrap">
            {/* 评分 */}
            <span className="text-text-secondary">
              score <span className="font-mono text-text-primary">{push.score?.toFixed(3) ?? '--'}</span>
            </span>
            {/* KKline 信号 ID */}
            {push.kkline_signal_id && (
              <span style={{ color: '#00e5ff' }}>
                KKline #{push.kkline_signal_id}
              </span>
            )}
          </div>
          {/* 错误信息（完整显示 + 友好化） */}
          {push.error && (
            <div
              className="mt-2 text-xs leading-relaxed px-3 py-2 rounded-md break-all"
              style={{ background: 'rgba(255,51,102,0.06)', color: '#ff6b8a' }}
            >
              <span className="font-medium" style={{ color: '#ff3366' }}>失败原因：</span>
              {friendlyError(push.error)}
            </div>
          )}
        </div>

        {/* 右：时间 */}
        <div className="text-right shrink-0">
          <div className="text-xs font-mono text-text-tertiary">{timeStr}</div>
        </div>
      </div>
    </div>
  );
}
