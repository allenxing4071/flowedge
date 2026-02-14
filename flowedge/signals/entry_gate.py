"""
入场门卫 — 南哥式四层决策框架 + 30 分钟节点过滤。

核心思路：
  从"评分过线就交易"升级为"位置对 + 环境对 + 行为确认 + 方向对 才交易"。
  四层门卫全部通过才发出入场信号，任何一层不通过则保持 NEUTRAL。
  30 分钟整点前 5 分钟内不开新仓（做市商变盘高发期）。

四层架构（南哥打法 = 四层全做；skip_behavior_layer 仅临时“先开单”调试用）：
  Layer 0: TimeFilter     — 30 分钟节点过滤（整点前 5 分钟禁止入场）
  Layer 1: MarketRegime   — 市场环境分类（trending / ranging / breakout / extreme）
  Layer 2: LocationFilter — 位置过滤（只在关键价位附近交易）
  Layer 3: BehaviorConfirm — 做市商行为确认（吸收/假墙/大单）；skip_behavior_layer=True 时跳过
  Layer 4: DirectionConfirm — 方向确认（复用 SignalScorer 评分 + 提高门槛）

数据流：
  FeatureEngine.get_snapshot()
       ↓
  EntryGate.evaluate(features, composite_signal)
       ↓
  GateResult（通过/拒绝 + 各层详情）

「跟对做市商」判断逻辑（当前实现）：
  - 方向来源：L2 LocationFilter 给出 suggested_side（价值区边界、VWAP 带、突破方向等），即「跟谁」的决策依据
  - L4 DirectionConfirm：只做「确认不反对」— 要求 score 方向、CVD/OFI 方向与 suggested_side 一致，否则拒绝；不独立判断方向
  - 准不准：取决于 L2 位置条件（VWAP/POC/VA 阈值等）是否贴近真实做市商行为；无实时「跟对/跟错」信号，事后用纸盘平仓盈亏统计（多单 exit>entry、空单 exit<entry 为跟对）
  - 不能判断「跟的是不是做市商」：L3 只能看到吸收/假墙/大单等行为痕迹，无法区分是真实做市商还是散户/算法；做市商战略亏损（先故意亏再拉回）无专门逻辑，仅通过 PAPER_MIN_HOLD_WRONG_S 给浮亏持仓一点等待时间，避免 NEUTRAL+浮亏时秒平。
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from flowedge.optimizer.param_registry import ParamRegistry

logger = logging.getLogger("flowedge.gate")


# ── 数据结构 ──

@dataclass
class LayerResult:
    """单层门卫结果"""
    name: str
    passed: bool
    detail: str
    data: dict = field(default_factory=dict)


@dataclass
class GateResult:
    """四层门卫综合结果"""
    # 是否全部通过
    passed: bool
    # 建议信号（通过时为方向信号，不通过时为 NEUTRAL）
    signal: str
    # 建议方向（LONG / SHORT / NONE）
    side: str
    # 各层结果
    regime: LayerResult = field(default_factory=lambda: LayerResult("regime", False, "未评估"))
    location: LayerResult = field(default_factory=lambda: LayerResult("location", False, "未评估"))
    behavior: LayerResult = field(default_factory=lambda: LayerResult("behavior", False, "未评估"))
    direction: LayerResult = field(default_factory=lambda: LayerResult("direction", False, "未评估"))
    # 动态止损止盈建议（基于 VP 结构，默认值由 _calc_dynamic_sl_tp 从 Registry 覆盖）
    suggested_stop_loss_pct: float = 0.0
    suggested_take_profit_pct: float = 0.0
    # 门卫拒绝原因（第一个不通过的层）
    reject_layer: str = ""
    reject_reason: str = ""


# ── 配置 ──

@dataclass
class GateConfig:
    """
    门卫配置参数 — 所有值由 ParamRegistry 注入，无硬编码默认值。

    构建方式：EntryGate.__init__(registry=...) → _config_from_registry()
    """
    # Layer 1: 环境分类
    trending_min_alignment: float = 0.0
    ranging_max_alignment: float = 0.0
    extreme_band_width: float = 0.0
    breakout_min_alignment: float = 0.0

    # Layer 2: 位置过滤
    vwap_near_pct: float = 0.0
    poc_near_pct: float = 0.0
    va_edge_threshold: float = 0.0
    hvn_near_pct: float = 0.0
    vwap_band_near_pct: float = 0.0

    # Layer 3: 行为确认
    min_absorption_events: int = 0
    min_large_trade_flow: float = 0.0

    # Layer 4: 方向确认
    min_score: float = 0.0
    min_confidence: float = 0.0

    # Layer 0: 30 分钟节点过滤（布尔/整数参数，从 Registry 读取）
    time_filter_enabled: bool = True
    time_filter_minutes_before: int = 5
    time_filter_minutes_after: int = 2

    # 南哥打法：跳过 L3（布尔参数，从 Registry 读取）
    skip_behavior_layer: bool = False

    # 动态止损
    max_stop_loss_pct: float = 0.0
    min_stop_loss_pct: float = 0.0
    stop_loss_buffer_pct: float = 0.0


class EntryGate:
    """
    入场门卫：四层过滤，全部通过才允许交易。

    所有参数从 ParamRegistry 读取，支持热更新，无硬编码默认值。

    用法：
        gate = EntryGate(registry=param_registry)
        result = gate.evaluate(features, composite_signal)
        if result.passed:
            # 发出入场信号
        else:
            # 保持 NEUTRAL
    """

    def __init__(self, registry: "ParamRegistry"):
        if not registry:
            raise ValueError("EntryGate 必须传入 ParamRegistry，禁止无 Registry 运行")
        self._registry = registry
        self.config = self._config_from_registry(registry)
        self._sync_gate_extra_params(registry)
        registry.subscribe(self._on_params_updated)

    def _sync_gate_extra_params(self, reg: "ParamRegistry") -> None:
        """从 Registry 读取门卫内部补充参数"""
        self._strong_buy_threshold = reg.get("gate_signal_strong_buy")
        self._strong_sell_threshold = reg.get("gate_signal_strong_sell")
        self._default_take_profit_pct = reg.get("gate_default_take_profit_pct")
        self._min_take_profit_pct = reg.get("gate_min_take_profit_pct")
        self._ranging_max_tp_pct = reg.get("gate_ranging_max_tp_pct")
        self._trending_min_band_width = reg.get("gate_trending_min_band_width")
        self._ranging_min_absorption = int(reg.get("gate_ranging_min_absorption"))

    @staticmethod
    def _config_from_registry(registry: "ParamRegistry") -> GateConfig:
        """从 ParamRegistry 构建 GateConfig — 所有参数均从 Registry 读取"""
        return GateConfig(
            # Layer 1: 环境分类
            trending_min_alignment=registry.get("gate_trending_min_alignment"),
            ranging_max_alignment=registry.get("gate_ranging_max_alignment"),
            extreme_band_width=registry.get("gate_extreme_band_width"),
            breakout_min_alignment=registry.get("gate_breakout_min_alignment"),
            # Layer 2: 位置过滤
            vwap_near_pct=registry.get("gate_vwap_near_pct"),
            poc_near_pct=registry.get("gate_poc_near_pct"),
            va_edge_threshold=registry.get("gate_va_edge_threshold"),
            hvn_near_pct=registry.get("gate_hvn_near_pct"),
            vwap_band_near_pct=registry.get("gate_vwap_band_near_pct"),
            # Layer 3: 行为确认
            min_absorption_events=int(registry.get("gate_min_absorption_events")),
            min_large_trade_flow=registry.get("gate_min_large_trade_flow"),
            # Layer 4: 方向确认
            min_score=registry.get("gate_min_score"),
            min_confidence=registry.get("gate_min_confidence"),
            # Layer 0: 时间节点过滤
            time_filter_enabled=bool(int(registry.get("gate_time_filter_enabled"))),
            time_filter_minutes_before=int(registry.get("gate_time_filter_minutes_before")),
            time_filter_minutes_after=int(registry.get("gate_time_filter_minutes_after")),
            # 南哥打法
            skip_behavior_layer=bool(int(registry.get("gate_skip_behavior_layer"))),
            # 动态止损
            max_stop_loss_pct=registry.get("gate_max_stop_loss_pct"),
            min_stop_loss_pct=registry.get("gate_min_stop_loss_pct"),
            stop_loss_buffer_pct=registry.get("gate_stop_loss_buffer_pct"),
        )

    def _on_params_updated(self, all_values: dict[str, float]) -> None:
        """ParamRegistry 参数变更回调 — 热更新门卫配置和内部参数"""
        if self._registry:
            self.config = self._config_from_registry(self._registry)
            self._sync_gate_extra_params(self._registry)

    def evaluate(self, features: dict, signal: object) -> GateResult:
        """
        四层门卫评估。

        参数:
            features: FeatureEngine.get_snapshot()[symbol] 的特征字典
            signal: SignalScorer.score() 返回的 CompositeSignal

        返回:
            GateResult
        """
        result = GateResult(passed=False, signal="NEUTRAL", side="NONE")

        # ── Layer 0: 30 分钟节点过滤 ──
        # 做市商倾向于在 30 分钟整点发动变盘，整点前后不宜开新仓
        if self.config.time_filter_enabled:
            time_check = self._check_time_node()
            if not time_check.passed:
                result.reject_layer = "TimeFilter"
                result.reject_reason = time_check.detail
                return result

        # ── Layer 1: 市场环境分类 ──
        result.regime = self._classify_regime(features)
        if not result.regime.passed:
            result.reject_layer = "regime"
            result.reject_reason = result.regime.detail
            return result

        regime = result.regime.data.get("regime", "unknown")

        # ── Layer 2: 位置过滤 ──
        result.location = self._check_location(features, regime)
        if not result.location.passed:
            result.reject_layer = "location"
            result.reject_reason = result.location.detail
            return result

        suggested_side = result.location.data.get("suggested_side", "NONE")

        # ── Layer 3: 做市商行为确认（南哥打法可跳过，仅用 L1/L2/L4 跟随+判断方向） ──
        if self.config.skip_behavior_layer:
            result.behavior = LayerResult(
                "behavior", True,
                "南哥打法: 跳过行为层(先开单)",
                {"skip": True}
            )
        else:
            result.behavior = self._confirm_behavior(features, suggested_side)
            if not result.behavior.passed:
                result.reject_layer = "behavior"
                result.reject_reason = result.behavior.detail
                return result

        # ── Layer 4: 方向确认 ──
        result.direction = self._confirm_direction(features, signal, suggested_side)
        if not result.direction.passed:
            result.reject_layer = "direction"
            result.reject_reason = result.direction.detail
            return result

        # ── 全部通过 ──
        result.passed = True
        result.side = suggested_side

        if suggested_side == "LONG":
            result.signal = "STRONG_BUY" if signal.score >= self._strong_buy_threshold else "BUY"
        elif suggested_side == "SHORT":
            result.signal = "STRONG_SELL" if signal.score <= self._strong_sell_threshold else "SELL"

        # 计算动态止损止盈
        sl, tp = self._calc_dynamic_sl_tp(features, suggested_side, regime)
        result.suggested_stop_loss_pct = sl
        result.suggested_take_profit_pct = tp

        logger.info(
            f"[门卫通过] 环境={regime} 方向={suggested_side} "
            f"信号={result.signal} 止损={sl:.2f}% 止盈={tp:.2f}%"
        )

        return result

    # ══════════════════════════════════════════
    # Layer 0: 30 分钟节点过滤
    # ══════════════════════════════════════════

    def _check_time_node(self) -> LayerResult:
        """
        30 分钟节点过滤。

        做市商倾向于在 30 分钟整点（:00 和 :30）发动变盘：
          - 整点前 5 分钟：做市商在积累筹码/双向挤压，方向不明
          - 整点后 2 分钟：方向刚确立，等待确认

        加密市场 24 小时运行，此规则全天生效。
        """
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        minute = now.minute
        # 距离下一个 30 分钟整点的分钟数
        minutes_in_half = minute % 30
        minutes_to_next = 30 - minutes_in_half

        before = self.config.time_filter_minutes_before
        after = self.config.time_filter_minutes_after

        # 整点前 N 分钟
        if minutes_to_next <= before:
            return LayerResult(
                "time_filter", False,
                f"30分钟节点前{minutes_to_next}分钟（禁区：前{before}分钟）",
                {"minutes_to_node": minutes_to_next, "phase": "pre_node"}
            )

        # 整点后 N 分钟
        if minutes_in_half < after:
            return LayerResult(
                "time_filter", False,
                f"30分钟节点后{minutes_in_half}分钟（禁区：后{after}分钟）",
                {"minutes_after_node": minutes_in_half, "phase": "post_node"}
            )

        return LayerResult(
            "time_filter", True,
            f"距下个节点{minutes_to_next}分钟，安全",
            {"minutes_to_node": minutes_to_next, "phase": "safe"}
        )

    # ══════════════════════════════════════════
    # Layer 1: 市场环境分类
    # ══════════════════════════════════════════

    def _classify_regime(self, features: dict) -> LayerResult:
        """
        判断当前市场环境。

        分类：
          - trending: 强趋势，允许趋势跟随
          - ranging: 震荡盘整，允许均值回归
          - breakout: 刚突破关键位，允许突破跟随
          - extreme: 极端波动，禁止交易
        """
        cfg = self.config
        trend = features.get("trend", {})
        vwap = features.get("vwap", {})
        vp = features.get("volume_profile", {})
        absorption = features.get("absorption", {})

        alignment = abs(trend.get("alignment_score", 0))
        band_width = vwap.get("band_width_pct", 0)
        in_va = vp.get("in_value_area", False)
        events_5m = absorption.get("event_count_5m", 0)

        # 极端环境 → 禁止交易
        if band_width > cfg.extreme_band_width:
            return LayerResult(
                "regime", False,
                f"极端波动 band_width={band_width:.2f}% > {cfg.extreme_band_width}%",
                {"regime": "extreme"}
            )

        # 趋势环境
        if alignment >= cfg.trending_min_alignment and band_width > self._trending_min_band_width:
            return LayerResult(
                "regime", True,
                f"趋势环境 alignment={alignment:.1f} band_width={band_width:.2f}%",
                {"regime": "trending"}
            )

        # 突破环境：价格刚离开价值区域 + 有一定趋势对齐
        if not in_va and alignment >= cfg.breakout_min_alignment:
            return LayerResult(
                "regime", True,
                f"突破环境 alignment={alignment:.1f} 价格在VA外",
                {"regime": "breakout"}
            )

        # 震荡环境：趋势弱 + 在价值区域内
        if alignment <= cfg.ranging_max_alignment and in_va:
            # 震荡环境需要有吸收事件作为额外确认（有人在控盘）
            if events_5m >= 2:
                return LayerResult(
                    "regime", True,
                    f"震荡环境 alignment={alignment:.1f} VA内 吸收事件={events_5m}",
                    {"regime": "ranging"}
                )
            else:
                return LayerResult(
                    "regime", False,
                    f"震荡但无吸收确认 alignment={alignment:.1f} 吸收事件={events_5m}<2",
                    {"regime": "ranging_unconfirmed"}
                )

        # 不确定环境 → 不交易
        return LayerResult(
            "regime", False,
            f"环境不明确 alignment={alignment:.1f} band_width={band_width:.2f}% VA内={in_va}",
            {"regime": "unclear"}
        )

    # ══════════════════════════════════════════
    # Layer 2: 位置过滤
    # ══════════════════════════════════════════

    def _check_location(self, features: dict, regime: str) -> LayerResult:
        """
        检查价格是否在关键位置附近。

        不同环境下的位置要求：
          - ranging: 只在 VAL 附近做多、VAH 附近做空（均值回归）
          - trending: 在 VWAP 回踩时顺势开仓
          - breakout: 在 VAH/VAL 突破后确认时开仓
        """
        cfg = self.config
        vwap = features.get("vwap", {})
        vp = features.get("volume_profile", {})
        trend = features.get("trend", {})

        dev_1h = vwap.get("deviation_1h_pct", 0)
        poc_dev = vp.get("price_vs_poc_pct", 0)
        va_pct = vp.get("value_area_pct", 0.5)
        in_va = vp.get("in_value_area", False)
        current_price = vwap.get("current_price", 0)
        upper_band = vwap.get("upper_band_1h", 0)
        lower_band = vwap.get("lower_band_1h", 0)
        hvn_above = vp.get("hvn_above", 0)
        hvn_below = vp.get("hvn_below", 0)
        alignment_score = trend.get("alignment_score", 0)

        if regime == "ranging":
            return self._location_ranging(cfg, va_pct, in_va, poc_dev, dev_1h)

        if regime == "trending":
            return self._location_trending(cfg, dev_1h, alignment_score, current_price,
                                           upper_band, lower_band, hvn_below, hvn_above)

        if regime == "breakout":
            return self._location_breakout(cfg, in_va, poc_dev, alignment_score)

        return LayerResult("location", False, f"未知环境 {regime}，无位置规则")

    def _location_ranging(self, cfg, va_pct, in_va, poc_dev, dev_1h) -> LayerResult:
        """震荡环境：在 VAL 附近做多，VAH 附近做空"""
        if not in_va:
            return LayerResult("location", False,
                               f"震荡环境但价格在VA外 va_pct={va_pct:.2f}")

        # VAL 附近 → 做多
        if va_pct <= cfg.va_edge_threshold:
            return LayerResult(
                "location", True,
                f"震荡+VAL附近 va_pct={va_pct:.2f} → 做多",
                {"suggested_side": "LONG", "reason": "ranging_val_bounce"}
            )

        # VAH 附近 → 做空
        if va_pct >= (1.0 - cfg.va_edge_threshold):
            return LayerResult(
                "location", True,
                f"震荡+VAH附近 va_pct={va_pct:.2f} → 做空",
                {"suggested_side": "SHORT", "reason": "ranging_vah_reject"}
            )

        # POC 附近 → 做均值回归（方向取决于 VWAP 偏离）
        if abs(poc_dev) < cfg.poc_near_pct:
            side = "SHORT" if dev_1h > 0 else "LONG"
            return LayerResult(
                "location", True,
                f"震荡+POC附近 poc_dev={poc_dev:.3f}% VWAP偏离={dev_1h:.3f}% → {side}",
                {"suggested_side": side, "reason": "ranging_poc_reversion"}
            )

        return LayerResult(
            "location", False,
            f"震荡但不在关键位 va_pct={va_pct:.2f} poc_dev={poc_dev:.3f}%"
        )

    def _location_trending(self, cfg, dev_1h, alignment_score, current_price,
                           upper_band, lower_band, hvn_below, hvn_above) -> LayerResult:
        """趋势环境：VWAP 回踩时顺势开仓"""
        # 上升趋势 + 价格回踩到 VWAP 附近或下方
        if alignment_score > 0 and dev_1h <= cfg.vwap_near_pct:
            return LayerResult(
                "location", True,
                f"上升趋势+VWAP回踩 dev_1h={dev_1h:.3f}% alignment={alignment_score:.1f} → 做多",
                {"suggested_side": "LONG", "reason": "trend_vwap_pullback"}
            )

        # 下降趋势 + 价格反弹到 VWAP 附近或上方
        if alignment_score < 0 and dev_1h >= -cfg.vwap_near_pct:
            return LayerResult(
                "location", True,
                f"下降趋势+VWAP反弹 dev_1h={dev_1h:.3f}% alignment={alignment_score:.1f} → 做空",
                {"suggested_side": "SHORT", "reason": "trend_vwap_pullback"}
            )

        # VWAP 带边界（上升趋势中价格触及下轨 = 超卖回踩）
        if current_price > 0 and lower_band > 0:
            dist_to_lower = (current_price - lower_band) / current_price * 100
            if alignment_score > 0 and dist_to_lower < cfg.vwap_band_near_pct:
                return LayerResult(
                    "location", True,
                    f"上升趋势+触及VWAP下轨 距离={dist_to_lower:.3f}% → 做多",
                    {"suggested_side": "LONG", "reason": "trend_lower_band_bounce"}
                )

        if current_price > 0 and upper_band > 0:
            dist_to_upper = (upper_band - current_price) / current_price * 100
            if alignment_score < 0 and dist_to_upper < cfg.vwap_band_near_pct:
                return LayerResult(
                    "location", True,
                    f"下降趋势+触及VWAP上轨 距离={dist_to_upper:.3f}% → 做空",
                    {"suggested_side": "SHORT", "reason": "trend_upper_band_reject"}
                )

        # HVN 附近（趋势中的支撑/阻力确认）
        if hvn_below > 0 and current_price > 0:
            dist_hvn_below = (current_price - hvn_below) / current_price * 100
            if alignment_score > 0 and dist_hvn_below < cfg.hvn_near_pct:
                return LayerResult(
                    "location", True,
                    f"上升趋势+HVN支撑附近 距离={dist_hvn_below:.3f}% → 做多",
                    {"suggested_side": "LONG", "reason": "trend_hvn_support"}
                )

        if hvn_above > 0 and current_price > 0:
            dist_hvn_above = (hvn_above - current_price) / current_price * 100
            if alignment_score < 0 and dist_hvn_above < cfg.hvn_near_pct:
                return LayerResult(
                    "location", True,
                    f"下降趋势+HVN阻力附近 距离={dist_hvn_above:.3f}% → 做空",
                    {"suggested_side": "SHORT", "reason": "trend_hvn_resistance"}
                )

        return LayerResult(
            "location", False,
            f"趋势但不在回踩位 dev_1h={dev_1h:.3f}% alignment={alignment_score:.1f}"
        )

    def _location_breakout(self, cfg, in_va, poc_dev, alignment_score) -> LayerResult:
        """突破环境：VAH/VAL 突破后确认"""
        if in_va:
            return LayerResult("location", False, "突破环境但价格仍在VA内")

        # 向上突破（价格在 VA 上方 + 趋势向上）
        if poc_dev > 0 and alignment_score > 0:
            return LayerResult(
                "location", True,
                f"向上突破 poc_dev={poc_dev:.3f}% alignment={alignment_score:.1f} → 做多",
                {"suggested_side": "LONG", "reason": "breakout_above_va"}
            )

        # 向下突破（价格在 VA 下方 + 趋势向下）
        if poc_dev < 0 and alignment_score < 0:
            return LayerResult(
                "location", True,
                f"向下突破 poc_dev={poc_dev:.3f}% alignment={alignment_score:.1f} → 做空",
                {"suggested_side": "SHORT", "reason": "breakout_below_va"}
            )

        return LayerResult(
            "location", False,
            f"突破方向与趋势不一致 poc_dev={poc_dev:.3f}% alignment={alignment_score:.1f}"
        )

    # ══════════════════════════════════════════
    # Layer 3: 做市商行为确认
    # ══════════════════════════════════════════

    def _confirm_behavior(self, features: dict, suggested_side: str) -> LayerResult:
        """
        检查是否看到做市商行为痕迹。

        确认条件（至少满足一个）：
          1. 吸收检测：is_absorbing 且方向一致
          2. 假墙检测：depth_change 中 wall_events > 0
          3. 大单方向：large_trade 净流入方向一致
          4. 频繁吸收：5 分钟内 >= 3 次吸收事件
        """
        cfg = self.config
        absorption = features.get("absorption", {})
        depth = features.get("depth_change", {})
        large = features.get("large_trade", {})

        confirmations = []

        # 1. 吸收检测
        is_absorbing = absorption.get("is_absorbing", False)
        abs_side = absorption.get("absorption_side", "none")
        if is_absorbing:
            if (suggested_side == "LONG" and abs_side == "buy") or \
               (suggested_side == "SHORT" and abs_side == "sell"):
                confirmations.append(f"吸收确认({abs_side})")

        # 2. 频繁吸收事件
        events_5m = absorption.get("event_count_5m", 0)
        if events_5m >= cfg.min_absorption_events:
            confirmations.append(f"频繁吸收({events_5m}次/5m)")

        # 3. 假墙检测（假墙出现说明有人在操纵盘口）
        wall_events = depth.get("wall_events_30s", 0)
        if wall_events >= 1:
            confirmations.append(f"假墙检测({wall_events}次)")

        # 4. 大单方向一致
        net_flow = large.get("net_flow_30s", 0)
        if abs(net_flow) >= cfg.min_large_trade_flow:
            flow_side = "LONG" if net_flow > 0 else "SHORT"
            if flow_side == suggested_side:
                confirmations.append(f"大单{flow_side} ${abs(net_flow):,.0f}")

        if confirmations:
            return LayerResult(
                "behavior", True,
                f"行为确认: {' + '.join(confirmations)}",
                {"confirmations": confirmations}
            )

        return LayerResult(
            "behavior", False,
            f"无做市商行为确认 吸收={is_absorbing}({abs_side}) "
            f"假墙={wall_events} 大单净流=${net_flow:,.0f} 吸收事件={events_5m}"
        )

    # ══════════════════════════════════════════
    # Layer 4: 方向确认
    # ══════════════════════════════════════════

    def _confirm_direction(self, features: dict, signal: object, suggested_side: str) -> LayerResult:
        """
        复用 SignalScorer 的评分结果，但提高门槛。

        条件：
          1. |score| >= min_score (0.30)
          2. confidence >= min_confidence (0.50)
          3. CVD 和 OFI 方向与 suggested_side 一致（不允许矛盾）
        """
        cfg = self.config
        score = signal.score
        confidence = signal.confidence

        # 条件 1: 评分门槛
        if abs(score) < cfg.min_score:
            return LayerResult(
                "direction", False,
                f"评分不足 |{score:.3f}| < {cfg.min_score}"
            )

        # 条件 2: 置信度门槛
        if confidence < cfg.min_confidence:
            return LayerResult(
                "direction", False,
                f"置信度不足 {confidence:.3f} < {cfg.min_confidence}"
            )

        # 条件 3: 评分方向与建议方向一致
        score_side = "LONG" if score > 0 else "SHORT"
        if score_side != suggested_side:
            return LayerResult(
                "direction", False,
                f"方向矛盾 评分方向={score_side} 位置建议={suggested_side}"
            )

        # 条件 4: CVD 和 OFI 方向一致性检查
        cvd = features.get("cvd", {})
        ofi = features.get("ofi", {})
        cvd_buy = cvd.get("buy_vol_1m", 0)
        cvd_sell = cvd.get("sell_vol_1m", 0)
        ofi_z = ofi.get("z_score_30s", 0)

        cvd_total = cvd_buy + cvd_sell
        if cvd_total > 100:
            cvd_direction = "LONG" if cvd_buy > cvd_sell else "SHORT"
            ofi_direction = "LONG" if ofi_z > 0 else "SHORT"

            # CVD 和 OFI 都必须与建议方向一致
            if cvd_direction != suggested_side and ofi_direction != suggested_side:
                return LayerResult(
                    "direction", False,
                    f"CVD({cvd_direction})+OFI({ofi_direction})均与位置建议({suggested_side})矛盾"
                )

        return LayerResult(
            "direction", True,
            f"方向确认 score={score:.3f} conf={confidence:.3f} 方向={suggested_side}",
            {"score": score, "confidence": confidence}
        )

    # ══════════════════════════════════════════
    # 动态止损止盈计算
    # ══════════════════════════════════════════

    def _calc_dynamic_sl_tp(self, features: dict, side: str, regime: str) -> tuple[float, float]:
        """
        基于 VP 结构计算动态止损止盈。

        止损逻辑：
          - 做多止损 = min(VAL - buffer, 最大止损)
          - 做空止损 = min(VAH + buffer, 最大止损)

        止盈逻辑：
          - 做多止盈 = 对面 HVN 或 VAH（有结构依据的目标位）
          - 做空止盈 = 对面 HVN 或 VAL
        """
        cfg = self.config
        vp = features.get("volume_profile", {})
        vwap = features.get("vwap", {})

        current_price = vwap.get("current_price", 0)
        val_price = vp.get("val_price", 0)
        vah_price = vp.get("vah_price", 0)
        hvn_above = vp.get("hvn_above", 0)
        hvn_below = vp.get("hvn_below", 0)

        stop_loss = cfg.max_stop_loss_pct
        take_profit = self._default_take_profit_pct

        if current_price <= 0:
            return stop_loss, take_profit

        if side == "LONG":
            if val_price > 0:
                sl_from_val = (current_price - val_price) / current_price * 100 + cfg.stop_loss_buffer_pct
                stop_loss = max(cfg.min_stop_loss_pct, min(sl_from_val, cfg.max_stop_loss_pct))

            if hvn_above > 0 and hvn_above > current_price:
                tp_to_hvn = (hvn_above - current_price) / current_price * 100
                take_profit = max(self._min_take_profit_pct, tp_to_hvn)
            elif vah_price > 0 and vah_price > current_price:
                tp_to_vah = (vah_price - current_price) / current_price * 100
                take_profit = max(self._min_take_profit_pct, tp_to_vah)

        elif side == "SHORT":
            if vah_price > 0:
                sl_from_vah = (vah_price - current_price) / current_price * 100 + cfg.stop_loss_buffer_pct
                stop_loss = max(cfg.min_stop_loss_pct, min(sl_from_vah, cfg.max_stop_loss_pct))

            if hvn_below > 0 and hvn_below < current_price:
                tp_to_hvn = (current_price - hvn_below) / current_price * 100
                take_profit = max(self._min_take_profit_pct, tp_to_hvn)
            elif val_price > 0 and val_price < current_price:
                tp_to_val = (current_price - val_price) / current_price * 100
                take_profit = max(self._min_take_profit_pct, tp_to_val)

        if regime == "ranging":
            take_profit = min(take_profit, self._ranging_max_tp_pct)

        return round(stop_loss, 3), round(take_profit, 3)

    # ══════════════════════════════════════════
    # 状态查询（供 API 使用）
    # ══════════════════════════════════════════

    def get_config(self) -> dict:
        """返回当前门卫配置"""
        from dataclasses import asdict
        return asdict(self.config)
