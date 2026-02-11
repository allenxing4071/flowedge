"""
信号引擎 — FlowEdge 核心决策层。

职责：
  1. 调用 SignalScorer 生成综合信号
  2. 调用 AnomalyDetector 检测异常
  3. 维护信号历史（用于 UI 信号时间线）
  4. 提供信号变化事件（用于 SSE 推送）
  5. 输出 KKline 兼容的情报格式（对接层）

数据流：
  FeatureEngine.get_snapshot()
       ↓
  SignalEngine.evaluate()
       ↓
  CompositeSignal + AnomalySnapshot → API / SSE / Bridge
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional

import orjson

from .scorer import SignalScorer, CompositeSignal
from .detector import AnomalyDetector, AnomalySnapshot, AnomalyEvent
from .tracker import SignalTracker
from .pusher import SignalPusher

logger = logging.getLogger("flowedge.signals")


@dataclass
class SignalRecord:
    """信号历史记录"""
    symbol: str
    signal: str
    score: float
    confidence: float
    risk_level: str
    anomaly_count: int
    timestamp_ms: int


@dataclass
class SignalState:
    """单币种当前信号状态"""
    symbol: str
    signal: CompositeSignal
    anomalies: AnomalySnapshot
    prev_signal: Optional[str] = None
    signal_changed: bool = False
    last_update_ms: int = 0


class SignalEngine:
    """
    信号引擎：整合多因子评分 + 异常检测 → 可操作的交易信号。

    主要接口：
      - evaluate(features_snapshot) → dict[symbol, SignalState]
      - get_signal(symbol) → SignalState
      - get_history(symbol, limit) → list[SignalRecord]
      - get_kkline_intel(symbol) → dict (KKline 兼容格式)
    """

    def __init__(
        self,
        scorer: Optional[SignalScorer] = None,
        detector: Optional[AnomalyDetector] = None,
        tracker: Optional[SignalTracker] = None,
        history_size: int = 500,
    ):
        self.scorer = scorer or SignalScorer()
        self.detector = detector or AnomalyDetector()
        self.tracker = tracker or SignalTracker()
        self.pusher = SignalPusher()

        # 每币种状态
        self._states: dict[str, SignalState] = {}

        # 信号历史（全局，最近 500 条）
        self._history: deque = deque(maxlen=history_size)

        # 每币种历史（最近 200 条）
        self._symbol_history: dict[str, deque] = {}

        # SSE 信号订阅者
        self._signal_subscribers: list = []

        self._eval_count = 0

    # ══════════════════════════════════════════
    # 核心评估
    # ══════════════════════════════════════════

    def evaluate(self, snapshot: dict) -> dict:
        """
        对全部币种进行评估。

        参数:
            snapshot: FeatureEngine.get_snapshot() 的完整输出
                      格式: {"BTCUSDT": {features...}, "ETHUSDT": {features...}}

        返回:
            dict[symbol, SignalState]
        """
        now_ms = int(time.time() * 1000)
        results = {}

        for symbol, features in snapshot.items():
            # 多因子评分
            signal = self.scorer.score(features, timestamp_ms=now_ms)

            # 异常检测
            anomalies = self.detector.detect(features, symbol=symbol)

            # 异常影响信号置信度
            if anomalies.risk_level in ("HIGH", "EXTREME"):
                signal.confidence = round(signal.confidence * 0.7, 3)

            # 检测信号变化
            prev = self._states.get(symbol)
            prev_signal = prev.signal.signal if prev else None
            changed = prev_signal != signal.signal

            state = SignalState(
                symbol=symbol,
                signal=signal,
                anomalies=anomalies,
                prev_signal=prev_signal,
                signal_changed=changed,
                last_update_ms=now_ms,
            )

            self._states[symbol] = state
            results[symbol] = state

            # 记录历史
            record = SignalRecord(
                symbol=symbol,
                signal=signal.signal,
                score=signal.score,
                confidence=signal.confidence,
                risk_level=anomalies.risk_level,
                anomaly_count=anomalies.active_count,
                timestamp_ms=now_ms,
            )
            self._history.append(record)
            if symbol not in self._symbol_history:
                self._symbol_history[symbol] = deque(maxlen=200)
            self._symbol_history[symbol].append(record)

            # 信号变化时日志 + 追踪
            if changed:
                logger.info(
                    f"[信号变化] {symbol}: {prev_signal} → {signal.signal} "
                    f"(score={signal.score:.3f} conf={signal.confidence:.2f} "
                    f"risk={anomalies.risk_level})"
                )
                # 异步触发信号追踪 + 半自动推送（不阻塞评估循环）
                _track_task = asyncio.ensure_future(
                    self.tracker.on_signal_change(
                        symbol, signal.signal, signal.score, signal.confidence
                    )
                )
                _push_task = asyncio.ensure_future(
                    self.pusher.on_signal_change(
                        symbol, signal.signal, signal.score, signal.confidence
                    )
                )
                # 保持任务引用，防止 GC 回收
                self._bg_tasks = getattr(self, '_bg_tasks', set())
                self._bg_tasks.add(_track_task)
                self._bg_tasks.add(_push_task)
                _track_task.add_done_callback(self._bg_tasks.discard)
                _push_task.add_done_callback(self._bg_tasks.discard)

        self._eval_count += 1
        return results

    # ══════════════════════════════════════════
    # 查询接口
    # ══════════════════════════════════════════

    def get_signal(self, symbol: str) -> Optional[dict]:
        """获取单币种当前信号（API 输出格式）"""
        state = self._states.get(symbol)
        if not state:
            return None
        return self._state_to_dict(state)

    def get_all_signals(self) -> dict:
        """获取所有币种当前信号"""
        return {
            sym: self._state_to_dict(state)
            for sym, state in self._states.items()
        }

    def get_history(self, symbol: Optional[str] = None, limit: int = 50) -> list:
        """获取信号历史"""
        if symbol:
            history = self._symbol_history.get(symbol, deque())
        else:
            history = self._history
        records = list(history)[-limit:]
        return [asdict(r) for r in records]

    def get_dashboard(self) -> dict:
        """仪表盘数据（供 UI 使用）"""
        signals = {}
        for sym, state in self._states.items():
            signals[sym] = {
                "signal": state.signal.signal,
                "score": state.signal.score,
                "confidence": state.signal.confidence,
                "risk_level": state.anomalies.risk_level,
                "anomaly_count": state.anomalies.active_count,
                "bullish_factors": state.signal.bullish_count,
                "bearish_factors": state.signal.bearish_count,
                "signal_changed": state.signal_changed,
                "last_update_ms": state.last_update_ms,
            }

        # 全局统计
        all_signals = [s.signal.signal for s in self._states.values()]
        return {
            "symbols": signals,
            "summary": {
                "total_symbols": len(self._states),
                "strong_buy": all_signals.count("STRONG_BUY"),
                "buy": all_signals.count("BUY"),
                "neutral": all_signals.count("NEUTRAL"),
                "sell": all_signals.count("SELL"),
                "strong_sell": all_signals.count("STRONG_SELL"),
                "eval_count": self._eval_count,
            },
        }

    # ══════════════════════════════════════════
    # KKline 对接层（Phase 3 Bridge）
    # ══════════════════════════════════════════

    def get_kkline_intel(self, symbol: str, features: dict) -> dict:
        """
        生成 KKline 兼容的情报格式。

        KKline 的 DeepSeek 分析器期望接收结构化的市场情报数据。
        此方法将 FlowEdge 的特征和信号转换为 KKline 可直接注入的格式，
        写入 KKline 的 data/intelligence/auto_intel_{symbol}.json。

        返回格式与 KKline intel_collector.py 输出兼容。
        """
        state = self._states.get(symbol)
        signal_data = {}
        if state:
            signal_data = {
                "signal": state.signal.signal,
                "score": state.signal.score,
                "confidence": state.signal.confidence,
                "risk_level": state.anomalies.risk_level,
            }

        # 从特征中提取关键数据
        cvd = features.get("cvd", {})
        ofi = features.get("ofi", {})
        book = features.get("book") or {}
        funding = features.get("funding", {})
        liq = features.get("liquidation", {})
        oi = features.get("open_interest", {})
        sent = features.get("sentiment", {})
        trend = features.get("trend", {})
        vpin_data = features.get("vpin", {})
        large = features.get("large_trade", {})
        depth = features.get("depth_change", {})

        now_ms = int(time.time() * 1000)

        return {
            "source": "flowedge",
            "version": "2.0",
            "symbol": symbol,
            "timestamp_ms": now_ms,

            # FlowEdge 综合信号
            "flowedge_signal": signal_data,

            # 微观结构（KKline 没有的独特数据）
            "microstructure": {
                "cvd_1m": cvd.get("cvd_1m", 0),
                "cvd_5m": cvd.get("cvd_5m", 0),
                "buy_vol_1m": cvd.get("buy_vol_1m", 0),
                "sell_vol_1m": cvd.get("sell_vol_1m", 0),
                "ofi_z_score": ofi.get("z_score_30s", 0),
                "ofi_1m": ofi.get("ofi_1m", 0),
                "vpin": vpin_data.get("vpin", 0),
                "book_imbalance_l1": book.get("book_imbalance_l1", 0),
                "spread_pct": book.get("spread_pct", 0),
            },

            # 大单与深度
            "smart_money": {
                "large_trade_count_30s": large.get("count_30s", 0),
                "large_net_flow_30s": large.get("net_flow_30s", 0),
                "large_buy_total_30s": large.get("buy_total_30s", 0),
                "large_sell_total_30s": large.get("sell_total_30s", 0),
                "depth_imbalance": depth.get("depth_imbalance", 0),
                "fake_wall_count": depth.get("wall_events_30s", 0),
            },

            # 资金费率与清算
            "funding_liquidation": {
                "funding_rate": funding.get("current_rate", 0),
                "funding_extreme_level": funding.get("extreme_level", "normal"),
                "basis_pct": funding.get("basis_pct", 0),
                "liq_net_1m": liq.get("net_liq_1m", 0),
                "liq_cascade_level": liq.get("cascade_level", "none"),
            },

            # 持仓量
            "open_interest": {
                "oi_usdt": oi.get("oi_usdt", 0),
                "oi_change_pct": oi.get("oi_change_pct", 0),
                "global_oi_usd": oi.get("global_oi_usd", 0),
                "global_oi_change_1h": oi.get("global_oi_change_1h", 0),
            },

            # 情绪
            "sentiment": {
                "retail_ls_ratio": sent.get("retail_ls_ratio", 0),
                "whale_ls_ratio": sent.get("whale_ls_ratio", 0),
                "divergence": sent.get("divergence", 0),
                "fear_greed_value": sent.get("fear_greed_value", 50),
                "fear_greed_label": sent.get("fear_greed_label", "Neutral"),
            },

            # 趋势
            "trend": {
                "alignment_score": trend.get("alignment_score", 0),
                "trend_alignment": trend.get("trend_alignment", "mixed"),
                "volume_trend": trend.get("volume_trend", "stable"),
            },
        }

    # ══════════════════════════════════════════
    # SSE 信号推送
    # ══════════════════════════════════════════

    def subscribe_signals(self) -> asyncio.Queue:
        """订阅信号变化事件"""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._signal_subscribers.append(q)
        return q

    def unsubscribe_signals(self, q: asyncio.Queue) -> None:
        if q in self._signal_subscribers:
            self._signal_subscribers.remove(q)

    async def _push_signal_events(self, results: dict) -> None:
        """将信号变化推送给订阅者"""
        if not self._signal_subscribers:
            return

        # 只推送有变化的信号
        changed = {
            sym: self._state_to_dict(state)
            for sym, state in results.items()
            if state.signal_changed
        }
        if not changed:
            return

        data = orjson.dumps({"type": "signal_change", "data": changed}).decode()
        dead = []
        for q in self._signal_subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe_signals(q)

    # ══════════════════════════════════════════
    # 评估循环
    # ══════════════════════════════════════════

    async def evaluation_loop(
        self,
        feature_engine,
        interval_ms: int = 1000,
    ) -> None:
        """
        定时评估循环。每秒从 FeatureEngine 获取快照并评估。
        """
        logger.info(f"[SignalEngine] 评估循环启动，间隔 {interval_ms}ms")
        while True:
            try:
                snapshot = feature_engine.get_snapshot()
                if snapshot:
                    results = self.evaluate(snapshot)
                    await self._push_signal_events(results)
            except Exception as e:
                logger.error(f"[SignalEngine] 评估异常: {e}", exc_info=True)

            await asyncio.sleep(interval_ms / 1000)

    # ══════════════════════════════════════════
    # 内部工具
    # ══════════════════════════════════════════

    def _state_to_dict(self, state: SignalState) -> dict:
        """将 SignalState 转换为 API 输出格式"""
        sig = state.signal
        anom = state.anomalies

        factors_list = []
        for f in sig.factors:
            factors_list.append({
                "name": f.name,
                "score": f.score,
                "weight": f.weight,
                "raw_value": f.raw_value,
                "reason": f.reason,
                "weighted_score": round(f.score * f.weight, 4),
            })

        anomaly_events = []
        for e in anom.active_events:
            anomaly_events.append({
                "type": e.type,
                "severity": e.severity,
                "title": e.title,
                "description": e.description,
                "metric_value": e.metric_value,
                "threshold": e.threshold,
            })

        return {
            "symbol": state.symbol,
            "signal": sig.signal,
            "score": sig.score,
            "confidence": sig.confidence,
            "bullish_count": sig.bullish_count,
            "bearish_count": sig.bearish_count,
            "neutral_count": sig.neutral_count,
            "factors": factors_list,
            "risk_level": anom.risk_level,
            "anomalies": anomaly_events,
            "anomaly_count": anom.active_count,
            "prev_signal": state.prev_signal,
            "signal_changed": state.signal_changed,
            "last_update_ms": state.last_update_ms,
        }
