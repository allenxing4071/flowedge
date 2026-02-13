"""
信号引擎 — FlowEdge 核心决策层（南哥四层门卫框架 v3.0）。

南哥打法（参考 flowdege/参考/）：高频 + 跟随做市商 + 判断方向，四层全做（含 L3 吸收/假墙/大单）。
  L0=30分钟节点(可关) L1=环境 L2=位置 L3=行为 L4=方向。GATE_SKIP_BEHAVIOR_LAYER=true 仅临时“先开单”调试用。

职责：
  1. 调用 SignalScorer 生成综合信号
  2. 调用 AnomalyDetector 检测异常
  3. 调用 EntryGate 四层门卫过滤（环境+位置+行为+方向）
  4. 维护信号历史（用于 UI 信号时间线）
  5. 提供信号变化事件（用于 SSE 推送）
  6. 输出 KKline 兼容的情报格式（对接层）

数据流：
  FeatureEngine.get_snapshot() → SignalScorer.score() → EntryGate.evaluate()
  → 只有门卫全部通过才发出方向信号，否则强制 NEUTRAL → PaperTrader（带动态止损止盈）
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
from .entry_gate import EntryGate, GateResult, GateConfig
from ..config import cfg

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
        gate: Optional[EntryGate] = None,
        history_size: int = 500,
    ):
        self.scorer = scorer or SignalScorer()
        self.detector = detector or AnomalyDetector()
        self.tracker = tracker or SignalTracker()
        self.gate = gate or EntryGate(config=GateConfig(
            min_score=cfg.GATE_MIN_SCORE,
            min_confidence=cfg.GATE_MIN_CONFIDENCE,
            time_filter_enabled=cfg.GATE_TIME_FILTER_ENABLED,
            skip_behavior_layer=cfg.GATE_SKIP_BEHAVIOR_LAYER,
        ))
        self.pusher = SignalPusher()
        self.paper_trader = None  # 由 api.py 在 lifespan 中注入

        # 每币种状态
        self._states: dict[str, SignalState] = {}

        # 信号历史（全局，最近 500 条）
        self._history: deque = deque(maxlen=history_size)

        # 每币种历史（最近 200 条）
        self._symbol_history: dict[str, deque] = {}

        # SSE 信号订阅者
        self._signal_subscribers: list = []

        # 门卫结果缓存（供 API 查询）
        self._last_gate_results: dict[str, GateResult] = {}

        # 门卫评估日志（质量看板数据源，最近 2000 条）
        self._gate_log: deque = deque(maxlen=2000)

        self._eval_count = 0

        # ── 信号去抖：防止临界点抖动产生虚假信号变化 ──
        # 新信号必须连续出现 DEBOUNCE_COUNT 次才算有效变化
        self.DEBOUNCE_COUNT = 5   # 评估间隔1s → 5次 = 5秒稳定期
        # 每币种的去抖状态：{symbol: {"pending": str, "count": int, "confirmed": str}}
        self._debounce: dict[str, dict] = {}

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
            # ── 市场活跃度自适应：先判断环境，再动态调整因子权重 ──
            # 学术依据：订单流信号在高波动期有效，低波动期失效
            regime_result = self.gate._classify_regime(features)
            detected_regime = regime_result.data.get("regime", "unclear")
            self.scorer.set_regime(detected_regime)

            # 多因子评分（传入 symbol 用于滞后区状态追踪）
            signal = self.scorer.score(features, timestamp_ms=now_ms, symbol=symbol)

            # 异常检测
            anomalies = self.detector.detect(features, symbol=symbol)

            # 异常影响信号置信度
            if anomalies.risk_level in ("HIGH", "EXTREME"):
                signal.confidence = round(signal.confidence * 0.7, 3)

            # ── 四层门卫过滤（南哥框架核心）──
            # 门卫决定是否允许方向信号通过，不通过则强制 NEUTRAL
            gate_result = self.gate.evaluate(features, signal)
            self._last_gate_results[symbol] = gate_result

            # 记录门卫评估日志（质量看板数据源）
            self._gate_log.append({
                "ts": now_ms / 1000,
                "symbol": symbol,
                "passed": gate_result.passed,
                "signal": gate_result.signal,
                "side": gate_result.side,
                "raw_score": signal.score,
                "raw_signal": signal.signal,
                "confidence": signal.confidence,
                "regime_passed": gate_result.regime.passed,
                "regime_detail": gate_result.regime.detail,
                "location_passed": gate_result.location.passed,
                "location_detail": gate_result.location.detail,
                "behavior_passed": gate_result.behavior.passed,
                "behavior_detail": gate_result.behavior.detail,
                "direction_passed": gate_result.direction.passed,
                "direction_detail": gate_result.direction.detail,
                "reject_layer": gate_result.reject_layer,
                "reject_reason": gate_result.reject_reason,
                "sl_pct": gate_result.suggested_stop_loss_pct,
                "tp_pct": gate_result.suggested_take_profit_pct,
            })

            if gate_result.passed:
                # 门卫通过 → 使用门卫建议的信号
                gated_signal = gate_result.signal
            else:
                # 门卫拒绝 → 强制 NEUTRAL（不管评分器给了什么信号）
                # 但如果当前有持仓且信号是平仓方向，仍然允许平仓信号通过
                # （门卫只管入场，不管出场）
                prev_state = self._states.get(symbol)
                if prev_state and prev_state.signal.signal != "NEUTRAL":
                    # 有持仓 — 允许 NEUTRAL 和反转信号通过（用于平仓）
                    gated_signal = signal.signal
                else:
                    gated_signal = "NEUTRAL"

            # 用门卫过滤后的信号替换原始信号
            raw_signal = gated_signal

            # 检测信号变化（带去抖：新信号必须连续稳定 N 次才算有效）
            prev = self._states.get(symbol)
            prev_signal = prev.signal.signal if prev else None

            # 去抖逻辑
            db = self._debounce.get(symbol, {"pending": None, "count": 0, "confirmed": prev_signal})
            if raw_signal == db.get("confirmed"):
                # 与当前确认信号相同 → 重置计数
                db["pending"] = None
                db["count"] = 0
            elif raw_signal == db.get("pending"):
                # 与待确认信号相同 → 计数+1
                db["count"] += 1
            else:
                # 出现新的候选信号 → 开始计数
                db["pending"] = raw_signal
                db["count"] = 1

            # 判断是否达到去抖阈值
            if db["count"] >= self.DEBOUNCE_COUNT:
                # 新信号已稳定，确认变化
                db["confirmed"] = raw_signal
                db["pending"] = None
                db["count"] = 0
                changed = (prev_signal != raw_signal)
            else:
                # 未达阈值，信号维持上一次确认值
                changed = False
                signal.signal = db.get("confirmed") or raw_signal

            self._debounce[symbol] = db

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

                # 纸盘交易 — 信号变化时触发虚拟开/平仓
                # 传入门卫结果，用于动态止损止盈
                if self.paper_trader:
                    _gate = self._last_gate_results.get(symbol)
                    _paper_task = asyncio.ensure_future(
                        self.paper_trader.on_signal_change(
                            symbol, signal.signal, signal.score, signal.confidence,
                            gate_result=_gate,
                        )
                    )
                    self._bg_tasks.add(_paper_task)
                    _paper_task.add_done_callback(self._bg_tasks.discard)

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

    def get_gate_status(self) -> dict:
        """获取门卫状态（供 API / UI 使用）"""
        result = {}
        for sym, gr in self._last_gate_results.items():
            result[sym] = {
                "passed": gr.passed,
                "signal": gr.signal,
                "side": gr.side,
                "regime": {
                    "passed": gr.regime.passed,
                    "detail": gr.regime.detail,
                    "data": gr.regime.data,
                },
                "location": {
                    "passed": gr.location.passed,
                    "detail": gr.location.detail,
                    "data": gr.location.data,
                },
                "behavior": {
                    "passed": gr.behavior.passed,
                    "detail": gr.behavior.detail,
                    "data": gr.behavior.data,
                },
                "direction": {
                    "passed": gr.direction.passed,
                    "detail": gr.direction.detail,
                    "data": gr.direction.data,
                },
                "reject_layer": gr.reject_layer,
                "reject_reason": gr.reject_reason,
                "suggested_stop_loss_pct": gr.suggested_stop_loss_pct,
                "suggested_take_profit_pct": gr.suggested_take_profit_pct,
            }
        return result

    def get_quality_board(self) -> dict:
        """
        质量看板数据（学习 KKline 质量看板设计）。

        包含：
          1. 四层门卫漏斗（每层通过/拒绝率）
          2. 方向分布（LONG/SHORT/NEUTRAL 占比 + 偏见告警）
          3. 市场环境分布（trending/ranging/breakout/extreme）
          4. 拒绝原因 Top N
          5. 动态 vs 固定止损对比（来自纸盘交易数据）
          6. 门卫通过 vs 全部的交易表现对比
          7. 样本量与置信度说明
        """
        log = list(self._gate_log)
        total = len(log)

        if total == 0:
            return {
                "status": "no_data",
                "message": "门卫评估日志为空，等待数据积累...",
                "sample_size": 0,
            }

        # ── 1. 四层门卫漏斗 ──
        # 注意：每一层是独立评估的，不是累加关系
        # 某些信号可能在第一层就被拒绝，后面的层不会评估
        regime_passed = sum(1 for e in log if e["regime_passed"])
        location_passed = sum(1 for e in log if e["location_passed"])
        behavior_passed = sum(1 for e in log if e["behavior_passed"])
        direction_passed = sum(1 for e in log if e["direction_passed"])
        final_passed = sum(1 for e in log if e["passed"])

        # 漏斗转化率（逐层：只有上一层通过的才进入下一层）
        # 但 EntryGate 是串行的，第一层不过后面不评估
        # 这里用 reject_layer 来精确计算
        rejected_at = {"regime": 0, "location": 0, "behavior": 0, "direction": 0}
        for e in log:
            if not e["passed"] and e["reject_layer"]:
                layer = e["reject_layer"].lower().replace("marketregime", "regime").replace(
                    "locationfilter", "location"
                ).replace("behaviorconfirm", "behavior").replace(
                    "directionconfirm", "direction"
                )
                if layer in rejected_at:
                    rejected_at[layer] += 1

        funnel = {
            "total_evaluations": total,
            "regime_passed": regime_passed,
            "location_passed": location_passed,
            "behavior_passed": behavior_passed,
            "direction_passed": direction_passed,
            "final_passed": final_passed,
            "final_pass_rate": round(final_passed / total * 100, 1) if total else 0,
            "rejected_at": rejected_at,
        }

        # ── 2. 方向分布 ──
        dir_counts = {"LONG": 0, "SHORT": 0, "NEUTRAL": 0}
        for e in log:
            if e["passed"]:
                side = e["side"] or "NEUTRAL"
                if side in dir_counts:
                    dir_counts[side] += 1
                else:
                    dir_counts["NEUTRAL"] += 1
            else:
                dir_counts["NEUTRAL"] += 1

        bias_warning = None
        if total >= 20:
            for d, c in dir_counts.items():
                if d != "NEUTRAL" and c / total > 0.7:
                    bias_warning = f"告警: {d} 占比 {c/total:.0%}，可能存在方向偏见"
            neutral_pct = dir_counts["NEUTRAL"] / total
            if neutral_pct > 0.95:
                bias_warning = f"告警: NEUTRAL 占比 {neutral_pct:.0%}，门卫可能过严"

        direction_dist = {
            "LONG": dir_counts["LONG"],
            "SHORT": dir_counts["SHORT"],
            "NEUTRAL": dir_counts["NEUTRAL"],
            "total": total,
            "long_pct": round(dir_counts["LONG"] / total * 100, 1) if total else 0,
            "short_pct": round(dir_counts["SHORT"] / total * 100, 1) if total else 0,
            "neutral_pct": round(dir_counts["NEUTRAL"] / total * 100, 1) if total else 0,
            "bias_warning": bias_warning,
        }

        # ── 3. 市场环境分布 ──
        regime_counts = {}
        for e in log:
            detail = e.get("regime_detail", "unknown")
            # 从 detail 提取环境类型（如 "trending_up → trending"）
            regime_type = detail.split("_")[0] if detail else "unknown"
            regime_counts[regime_type] = regime_counts.get(regime_type, 0) + 1

        # ── 4. 拒绝原因 Top N ──
        reject_reasons = {}
        for e in log:
            if not e["passed"] and e["reject_reason"]:
                reason = e["reject_reason"]
                reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
        top_rejects = sorted(reject_reasons.items(), key=lambda x: -x[1])[:10]

        # ── 5. 门卫建议的止损/止盈分布（仅通过的信号）──
        passed_entries = [e for e in log if e["passed"]]
        sl_values = [e["sl_pct"] for e in passed_entries if e["sl_pct"]]
        tp_values = [e["tp_pct"] for e in passed_entries if e["tp_pct"]]
        dynamic_sl_tp = {
            "count": len(passed_entries),
            "avg_sl_pct": round(sum(sl_values) / len(sl_values), 2) if sl_values else 0,
            "avg_tp_pct": round(sum(tp_values) / len(tp_values), 2) if tp_values else 0,
            "min_sl_pct": round(min(sl_values), 2) if sl_values else 0,
            "max_sl_pct": round(max(sl_values), 2) if sl_values else 0,
            "min_tp_pct": round(min(tp_values), 2) if tp_values else 0,
            "max_tp_pct": round(max(tp_values), 2) if tp_values else 0,
        }

        # ── 6. 纸盘交易表现（从 paper_trader 获取）──
        trade_performance = {"status": "no_paper_trader"}
        if self.paper_trader:
            try:
                trades = self.paper_trader.get_trades(limit=500)
                if trades:
                    dynamic_trades = [t for t in trades if t.get("sl_source") == "门卫动态"]
                    fixed_trades = [t for t in trades if t.get("sl_source") != "门卫动态"]

                    def _trade_stats(trade_list):
                        if not trade_list:
                            return {"count": 0}
                        wins = [t for t in trade_list if t.get("net_pnl", 0) > 0]
                        return {
                            "count": len(trade_list),
                            "win_rate": round(len(wins) / len(trade_list) * 100, 1),
                            "avg_pnl_pct": round(
                                sum(t.get("net_pnl_pct", 0) for t in trade_list) / len(trade_list), 2
                            ),
                            "total_pnl": round(
                                sum(t.get("net_pnl", 0) for t in trade_list), 2
                            ),
                        }

                    trade_performance = {
                        "all_trades": _trade_stats(trades),
                        "dynamic_sl_trades": _trade_stats(dynamic_trades),
                        "fixed_sl_trades": _trade_stats(fixed_trades),
                        "exit_reasons": {},
                    }
                    # 退出原因分布
                    for t in trades:
                        reason = t.get("exit_reason", "unknown")
                        trade_performance["exit_reasons"][reason] = (
                            trade_performance["exit_reasons"].get(reason, 0) + 1
                        )
                else:
                    trade_performance = {"status": "no_trades", "message": "暂无纸盘交易记录"}
            except Exception as e:
                trade_performance = {"status": "error", "message": str(e)}

        # ── 7. 样本量与置信度 ──
        if total < 20:
            confidence_note = "样本不足（<20），数据仅供参考，继续观察"
        elif total < 100:
            confidence_note = "样本量中等（20-100），可做初步判断"
        elif total < 500:
            confidence_note = "样本量充足（100-500），数据可靠"
        else:
            confidence_note = f"样本量大（{total}），数据高度可靠"

        # ── 8. 时间范围 ──
        first_ts = log[0]["ts"] if log else 0
        last_ts = log[-1]["ts"] if log else 0
        duration_hours = round((last_ts - first_ts) / 3600, 1) if first_ts and last_ts else 0

        return {
            "status": "ok",
            "gate_funnel": funnel,
            "direction_distribution": direction_dist,
            "regime_distribution": regime_counts,
            "top_reject_reasons": [{"reason": r, "count": c} for r, c in top_rejects],
            "dynamic_sl_tp": dynamic_sl_tp,
            "trade_performance": trade_performance,
            "sample_size": total,
            "confidence_note": confidence_note,
            "time_range": {
                "first_ts": first_ts,
                "last_ts": last_ts,
                "duration_hours": duration_hours,
            },
            "generated_at": time.time(),
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

                    # 转发实时价格给纸盘交易器（用于止损/盈亏追踪）
                    if self.paper_trader:
                        for sym, feat in snapshot.items():
                            book = feat.get("book", {})
                            mid = book.get("mid_price", 0)
                            if mid > 0:
                                self.paper_trader.on_price_update(sym, mid)
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
