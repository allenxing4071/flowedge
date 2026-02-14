"""
多因子评分引擎 — 将 14 个原始特征转化为标准化方向分数。

设计原则：
  1. 每个特征独立评分 → [-1.0, +1.0]（+1 看多, -1 看空, 0 中性）
  2. 加权合成 → composite score
  3. 一致性度量 → confidence（因子间一致性越高，置信度越高）
  4. 所有阈值可配置，无硬编码魔术数字

学术基础：
  - CVD/OFI: Cont, Kukanov & Stoikov (2014) — 订单流不平衡的价格影响
  - VPIN: Easley, López de Prado & O'Hara (2012) — 知情交易概率
  - 多因子加权: 风险平价思想，高信噪比因子给更大权重
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from flowedge.optimizer.param_registry import ParamRegistry


# ── 评分结果 ──

@dataclass
class FactorScore:
    """单因子评分"""
    name: str
    score: float      # [-1.0, +1.0]
    weight: float     # 权重
    raw_value: float  # 原始值（用于调试）
    reason: str       # 中文原因说明


@dataclass
class CompositeSignal:
    """综合信号"""
    signal: str            # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    score: float           # [-1.0, +1.0] 综合得分
    confidence: float      # [0.0, 1.0] 置信度（因子一致性）
    factors: list = field(default_factory=list)  # FactorScore 列表
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    timestamp_ms: int = 0


# ── 所有评分参数（权重/阈值/灵敏度）已迁移到 ParamRegistry（唯一数据源） ──
# 不再在此文件中定义任何默认值常量
# 所有参数通过 API 热更新：PUT /optimizer/params
# 查看当前值：GET /optimizer/params


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _tanh_scale(v: float, sensitivity: float = 1.0) -> float:
    """用 tanh 将任意范围映射到 [-1, 1]，sensitivity 控制灵敏度"""
    return math.tanh(v * sensitivity)


class SignalScorer:
    """
    多因子评分器：读取特征快照 dict → 输出 CompositeSignal。

    支持两种模式：
      1. 传统模式（向后兼容）：使用硬编码默认值
      2. Registry 模式：从 ParamRegistry 读取所有参数，支持热更新
    """

    # 市场活跃度自适应：不同环境下的微观结构因子权重倍率（默认值）
    # 学术依据：订单流信号在高波动期有效（+0.60%），低波动期失效（-0.16%）
    _DEFAULT_REGIME_MULTIPLIERS = {
        "trending":   {"micro": 1.2, "macro": 0.8},   # 趋势中微观信号更可靠
        "breakout":   {"micro": 1.3, "macro": 0.7},   # 突破时微观信号最强
        "ranging":    {"micro": 0.8, "macro": 1.2},   # 震荡中宏观因子更重要
        "extreme":    {"micro": 0.5, "macro": 0.5},   # 极端环境全部降权
        "unclear":    {"micro": 0.7, "macro": 0.7},   # 不明确时保守
    }

    # 微观结构因子列表（受市场活跃度影响最大的因子）
    MICRO_FACTORS = {"cvd", "ofi", "book_imbalance", "large_trade", "depth_change",
                     "absorption", "vwap", "volume_profile"}
    # 宏观因子列表
    MACRO_FACTORS = {"funding", "liquidation", "sentiment", "trend", "vpin", "oi"}

    def __init__(self, registry: "ParamRegistry"):
        if not registry:
            raise ValueError("SignalScorer 必须传入 ParamRegistry，禁止无 Registry 运行")
        self._registry = registry

        # 从 Registry 初始化所有参数（唯一数据源）
        self._base_weights = registry.get_weights_dict()
        self._signal_thresholds = registry.get_signal_thresholds()
        self._exit_thresholds = registry.get_exit_thresholds()
        self._reversal_thresholds = registry.get_reversal_thresholds()
        self._regime_multipliers = registry.get_regime_multipliers()
        # 置信度参数
        self._conf_bullish_threshold = registry.get("conf_bullish_threshold")
        self._conf_bearish_threshold = registry.get("conf_bearish_threshold")
        self._conf_dominant_boost_count = int(registry.get("conf_dominant_boost_count"))
        self._conf_dominant_boost_mult = registry.get("conf_dominant_boost_mult")
        # 评分函数内部参数（全部从 Registry 读取）
        self._sync_scorer_params(registry)
        # 注册热更新回调
        registry.subscribe(self._on_params_updated)

        self.weights = self._base_weights.copy()
        # 每币种上一次确认的信号（用于滞后区判断）
        self._prev_signals: dict[str, str] = {}
        # 当前市场环境（由 engine 在评估前设置）
        self._current_regime: str = "unclear"

    def _sync_scorer_params(self, reg: "ParamRegistry") -> None:
        """从 Registry 读取评分函数内部所有参数"""
        g = reg.get  # 简写
        # CVD
        self._cvd_min_volume = g("score_cvd_min_volume")
        self._cvd_trend_scale = g("score_cvd_trend_scale")
        self._cvd_base_weight = g("score_cvd_base_weight")
        self._cvd_trend_weight = g("score_cvd_trend_weight")
        self._cvd_div_threshold = g("score_cvd_div_threshold")
        self._cvd_div_base_weight = g("score_cvd_div_base_weight")
        self._cvd_div_weight = g("score_cvd_div_weight")
        # OFI
        self._ofi_core_sensitivity = g("score_ofi_core_sensitivity")
        self._ofi_long_sensitivity = g("score_ofi_long_sensitivity")
        self._ofi_trend_sensitivity = g("score_ofi_trend_sensitivity")
        self._ofi_core_weight = g("score_ofi_core_weight")
        self._ofi_long_weight = g("score_ofi_long_weight")
        self._ofi_trend_weight = g("score_ofi_trend_weight")
        self._ofi_agree_boost = g("score_ofi_agree_boost")
        self._ofi_disagree_mult = g("score_ofi_disagree_mult")
        # 大单
        self._lt_min_total = g("score_lt_min_total")
        self._lt_count_boost_rate = g("score_lt_count_boost_rate")
        self._lt_count_boost_max = g("score_lt_count_boost_max")
        # 深度
        self._depth_sensitivity = g("score_depth_sensitivity")
        self._depth_wall_threshold = int(g("score_depth_wall_threshold"))
        self._depth_wall_mult = g("score_depth_wall_mult")
        # 资金费率
        self._funding_sensitivity = g("score_funding_sensitivity")
        self._funding_extreme_mult = g("score_funding_extreme_mult")
        # 清算
        self._liq_min_net = g("score_liq_min_net")
        self._liq_scale = g("score_liq_scale")
        self._liq_sensitivity = g("score_liq_sensitivity")
        # 情绪
        self._sent_fng_sensitivity = g("score_sent_fng_sensitivity")
        self._sent_retail_sensitivity = g("score_sent_retail_sensitivity")
        self._sent_whale_sensitivity = g("score_sent_whale_sensitivity")
        self._sent_fng_weight = g("score_sent_fng_weight")
        self._sent_retail_weight = g("score_sent_retail_weight")
        self._sent_whale_weight = g("score_sent_whale_weight")
        # 趋势
        self._trend_vol_threshold = g("score_trend_vol_threshold")
        self._trend_vol_boost = g("score_trend_vol_boost")
        # VPIN
        self._vpin_low_threshold = g("score_vpin_low_threshold")
        self._vpin_penalty_rate = g("score_vpin_penalty_rate")
        self._vpin_direction_scale = g("score_vpin_direction_scale")
        # OI
        self._oi_min_change = g("score_oi_min_change")
        self._oi_sensitivity = g("score_oi_sensitivity")
        self._oi_global_threshold = g("score_oi_global_threshold")
        self._oi_global_sensitivity = g("score_oi_global_sensitivity")
        self._oi_local_weight = g("score_oi_local_weight")
        self._oi_global_weight = g("score_oi_global_weight")
        # VWAP
        self._vwap_mr_sensitivity = g("score_vwap_mr_sensitivity")
        self._vwap_mr_divisor = g("score_vwap_mr_divisor")
        self._vwap_trend_threshold = g("score_vwap_trend_threshold")
        self._vwap_trend_sensitivity = g("score_vwap_trend_sensitivity")
        self._vwap_trend_divisor = g("score_vwap_trend_divisor")
        self._vwap_mr_weight = g("score_vwap_mr_weight")
        self._vwap_trend_weight = g("score_vwap_trend_weight")
        # Volume Profile
        self._vp_min_volume = g("score_vp_min_volume")
        self._vp_va_sensitivity = g("score_vp_va_sensitivity")
        self._vp_va_divisor = g("score_vp_va_divisor")
        self._vp_breakout_sensitivity = g("score_vp_breakout_sensitivity")
        self._vp_breakout_divisor = g("score_vp_breakout_divisor")
        # 吸收
        self._abs_event_threshold = int(g("score_abs_event_threshold"))
        self._abs_event_boost_rate = g("score_abs_event_boost_rate")
        self._abs_event_boost_max = g("score_abs_event_boost_max")

    def _on_params_updated(self, all_values: dict[str, float]) -> None:
        """ParamRegistry 参数变更回调 — 热更新权重、阈值和评分参数"""
        if not self._registry:
            return
        self._base_weights = self._registry.get_weights_dict()
        self._signal_thresholds = self._registry.get_signal_thresholds()
        self._exit_thresholds = self._registry.get_exit_thresholds()
        self._reversal_thresholds = self._registry.get_reversal_thresholds()
        self._regime_multipliers = self._registry.get_regime_multipliers()
        self._conf_bullish_threshold = self._registry.get("conf_bullish_threshold")
        self._conf_bearish_threshold = self._registry.get("conf_bearish_threshold")
        self._conf_dominant_boost_count = int(self._registry.get("conf_dominant_boost_count"))
        self._conf_dominant_boost_mult = self._registry.get("conf_dominant_boost_mult")
        # 评分函数内部参数
        self._sync_scorer_params(self._registry)
        # 强制重算当前环境权重
        old_regime = self._current_regime
        self._current_regime = "__force_recalc__"
        self.set_regime(old_regime)

    def set_regime(self, regime: str) -> None:
        """
        根据市场环境动态调整因子权重。
        由 SignalEngine 在每次评估前调用。
        """
        if regime == self._current_regime:
            return  # 无变化，跳过重算

        self._current_regime = regime
        mults = self._regime_multipliers.get(regime, {"micro": 1.0, "macro": 1.0})

        # 按类别应用倍率
        adjusted = {}
        for factor, base_w in self._base_weights.items():
            if factor in self.MICRO_FACTORS:
                adjusted[factor] = base_w * mults["micro"]
            elif factor in self.MACRO_FACTORS:
                adjusted[factor] = base_w * mults["macro"]
            else:
                adjusted[factor] = base_w

        # 归一化权重（保证总和 = 1.0）
        total = sum(adjusted.values())
        if total > 0:
            self.weights = {k: v / total for k, v in adjusted.items()}
        else:
            self.weights = self._base_weights.copy()

    def score(self, features: dict, timestamp_ms: int = 0, symbol: str = "") -> CompositeSignal:
        """
        对单个币种的特征快照进行多因子评分。

        参数:
            features: 来自 FeatureEngine.get_snapshot()[symbol] 的字典
            timestamp_ms: 当前时间戳
            symbol: 币种（用于滞后区状态追踪）

        返回:
            CompositeSignal
        """
        factors = []

        # 1. CVD — 成交量 delta 方向
        factors.append(self._score_cvd(features.get("cvd", {})))

        # 2. OFI — 订单流不平衡
        factors.append(self._score_ofi(features.get("ofi", {})))

        # 3. Book Imbalance — L1 盘口
        factors.append(self._score_book(features.get("book")))

        # 4. Large Trade — 大单资金流
        factors.append(self._score_large_trade(features.get("large_trade", {})))

        # 5. Depth Change — 深度变化
        factors.append(self._score_depth(features.get("depth_change", {})))

        # 6. Funding — 资金费率（反向）
        factors.append(self._score_funding(features.get("funding", {})))

        # 7. Liquidation — 清算级联
        factors.append(self._score_liquidation(features.get("liquidation", {})))

        # 8. Sentiment — 多空情绪
        factors.append(self._score_sentiment(features.get("sentiment", {})))

        # 9. Trend — 多周期趋势
        factors.append(self._score_trend(features.get("trend", {})))

        # 10. VPIN — 知情交易
        factors.append(self._score_vpin(features.get("vpin", {})))

        # 11. OI — 持仓量变化
        factors.append(self._score_oi(features.get("open_interest", {})))

        # 12. VWAP — 价格偏离度（做市商逻辑）
        factors.append(self._score_vwap(features.get("vwap", {})))

        # 13. Volume Profile — 支撑阻力判断（做市商逻辑）
        factors.append(self._score_volume_profile(features.get("volume_profile", {})))

        # 14. Absorption — 吸收检测（做市商逻辑）
        factors.append(self._score_absorption(features.get("absorption", {})))

        # ── 加权合成 ──
        total_score = sum(f.score * f.weight for f in factors)

        # ── 置信度：因子一致性（参数化阈值） ──
        bullish = sum(1 for f in factors if f.score > self._conf_bullish_threshold)
        bearish = sum(1 for f in factors if f.score < self._conf_bearish_threshold)
        neutral = len(factors) - bullish - bearish
        dominant = max(bullish, bearish)
        confidence = dominant / len(factors) if factors else 0.0

        # 加强：如果方向一致性极高，上调置信度
        if dominant >= self._conf_dominant_boost_count:
            confidence = min(1.0, confidence * self._conf_dominant_boost_mult)

        # ── 信号分级（带滞后区，防止临界点抖动）──
        prev_sig = self._prev_signals.get(symbol, "NEUTRAL")
        signal = self._classify_with_hysteresis(total_score, prev_sig)
        if symbol:
            self._prev_signals[symbol] = signal

        return CompositeSignal(
            signal=signal,
            score=round(total_score, 4),
            confidence=round(confidence, 3),
            factors=factors,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            timestamp_ms=timestamp_ms,
        )

    # ══════════════════════════════════════════
    # 滞后区信号分类
    # ══════════════════════════════════════════

    def _classify_with_hysteresis(self, score: float, prev_signal: str) -> str:
        """
        带滞后区 + 反转保护 的信号分类。

        三层保护：
          1. 入场阈值（NEUTRAL→方向）：从 registry 或默认值读取
          2. 退出阈值（方向→NEUTRAL）：从 registry 或默认值读取
          3. 反转阈值（BUY→SELL 或 SELL→BUY）：从 registry 或默认值读取

        例：
          - NEUTRAL + score=0.16 → BUY（入场）
          - BUY + score=0.12 → BUY（滞后区保持）
          - BUY + score=0.07 → NEUTRAL（跌出滞后区）
          - BUY + score=-0.16 → NEUTRAL（未达反转阈值 -0.25，先回 NEUTRAL）
          - BUY + score=-0.26 → SELL（达到反转阈值，直接反转）
        """
        th = self._signal_thresholds
        ex = self._exit_thresholds
        rv = self._reversal_thresholds

        if prev_signal in ("STRONG_BUY", "BUY"):
            # 当前持多 — 优先检查反转（需更强信号），再检查退出
            if score >= th["strong_buy"]:
                return "STRONG_BUY"
            elif score >= ex["buy_exit"]:
                # 滞后区内 — 保持 BUY
                return "BUY" if prev_signal == "BUY" else (
                    "STRONG_BUY" if score >= ex["strong_buy_exit"] else "BUY"
                )
            elif score <= rv["buy_to_sell"]:
                # 达到反转阈值 — 直接反转到 SELL
                return "STRONG_SELL" if score <= th["strong_sell"] else "SELL"
            else:
                # 未达反转阈值但跌出买方滞后区 — 回到 NEUTRAL
                return "NEUTRAL"

        elif prev_signal in ("STRONG_SELL", "SELL"):
            # 当前持空 — 优先检查反转，再检查退出
            if score <= th["strong_sell"]:
                return "STRONG_SELL"
            elif score <= ex["sell_exit"]:
                return "SELL" if prev_signal == "SELL" else (
                    "STRONG_SELL" if score <= ex["strong_sell_exit"] else "SELL"
                )
            elif score >= rv["sell_to_buy"]:
                # 达到反转阈值 — 直接反转到 BUY
                return "STRONG_BUY" if score >= th["strong_buy"] else "BUY"
            else:
                # 未达反转阈值但超出卖方滞后区 — 回到 NEUTRAL
                return "NEUTRAL"

        else:
            # NEUTRAL — 用入场阈值（标准门槛）
            if score >= th["strong_buy"]:
                return "STRONG_BUY"
            elif score >= th["buy"]:
                return "BUY"
            elif score <= th["strong_sell"]:
                return "STRONG_SELL"
            elif score <= th["sell"]:
                return "SELL"
            else:
                return "NEUTRAL"

    # ══════════════════════════════════════════
    # 各因子评分逻辑
    # ══════════════════════════════════════════

    def _score_cvd(self, cvd: dict) -> FactorScore:
        """CVD: 买卖 delta 方向 + 5 分钟趋势 + 量价背离检测。"""
        w = self.weights.get("cvd", 0.15)
        buy = cvd.get("buy_vol_1m", 0)
        sell = cvd.get("sell_vol_1m", 0)
        total = buy + sell

        if total < self._cvd_min_volume:
            return FactorScore("cvd", 0.0, w, 0.0, "成交量不足")

        ratio = (buy - sell) / total

        cvd_5m = cvd.get("cvd_5m", 0)
        if cvd_5m != 0 and total > 0:
            trend_boost = _clamp(cvd_5m / total * self._cvd_trend_scale, -0.3, 0.3)
        else:
            trend_boost = 0

        base_score = _clamp(ratio * self._cvd_base_weight + trend_boost * self._cvd_trend_weight)

        div_score = cvd.get("divergence_score", 0)
        div_type = cvd.get("divergence_type", "none")

        if div_type != "none" and abs(div_score) > self._cvd_div_threshold:
            score = base_score * self._cvd_div_base_weight + div_score * self._cvd_div_weight
        else:
            score = base_score

        score = _clamp(score)
        direction = "买方主导" if score > 0 else "卖方主导"
        reason = f"{direction} 比率{ratio:.2f}"
        if div_type == "bearish_div":
            reason += f" ⚠️看跌背离({div_score:.2f})"
        elif div_type == "bullish_div":
            reason += f" ⚠️看涨背离({div_score:.2f})"

        return FactorScore("cvd", round(score, 4), w, ratio, reason)

    def _score_ofi(self, ofi: dict) -> FactorScore:
        """OFI: 多时间窗口订单流不平衡评分。"""
        w = self.weights.get("ofi", 0.15)
        z_30s = ofi.get("z_score_30s", 0)
        z_5m = ofi.get("z_score_5m", 0)
        trend = ofi.get("ofi_trend", 0)
        agreement = ofi.get("multi_window_agreement", 0)

        core_score = _tanh_scale(z_30s, sensitivity=self._ofi_core_sensitivity)
        long_score = _tanh_scale(z_5m, sensitivity=self._ofi_long_sensitivity)
        trend_score = _tanh_scale(trend, sensitivity=self._ofi_trend_sensitivity)

        score = (core_score * self._ofi_core_weight
                 + long_score * self._ofi_long_weight
                 + trend_score * self._ofi_trend_weight)

        if agreement * score > 0:
            boost = abs(agreement) * self._ofi_agree_boost
            score = _clamp(score * (1 + boost))
        elif abs(agreement) > 0.5 and agreement * score < 0:
            score *= self._ofi_disagree_mult

        reason_parts = [f"z30s={z_30s:.2f}"]
        if abs(z_5m) > 0.5:
            reason_parts.append(f"z5m={z_5m:.2f}")
        if abs(trend) > 0.3:
            direction = "加速" if trend > 0 else "减速"
            reason_parts.append(f"趋势{direction}")
        if abs(agreement) > 0.5:
            consistency = "一致" if agreement * score > 0 else "分歧"
            reason_parts.append(f"多窗口{consistency}")
        if abs(z_30s) > 2:
            reason_parts.append("极端")

        return FactorScore("ofi", round(score, 4), w, z_30s, " ".join(reason_parts))

    def _score_book(self, book: Optional[dict]) -> FactorScore:
        """Book Imbalance: L1 盘口压力"""
        w = self.weights.get("book_imbalance", 0.10)
        if not book:
            return FactorScore("book_imbalance", 0.0, w, 0.0, "无盘口数据")

        imb = book.get("book_imbalance_l1", 0)
        # imbalance 范围 [-100, 100] → 映射到 [-1, 1]
        score = _clamp(imb / 100)
        return FactorScore("book_imbalance", round(score, 4), w, imb, f"L1不平衡 {imb:.1f}%")

    def _score_large_trade(self, lt: dict) -> FactorScore:
        """Large Trade: 大单资金流方向"""
        w = self.weights.get("large_trade", 0.12)
        net = lt.get("net_flow_30s", 0)
        buy_total = lt.get("buy_total_30s", 0)
        sell_total = lt.get("sell_total_30s", 0)
        total = buy_total + sell_total

        if total < self._lt_min_total:
            return FactorScore("large_trade", 0.0, w, 0.0, "无大单")

        ratio = net / total
        count = lt.get("count_30s", 0)
        count_boost = min(count * self._lt_count_boost_rate, self._lt_count_boost_max)
        if ratio > 0:
            score = _clamp(ratio + count_boost)
        else:
            score = _clamp(ratio - count_boost)

        direction = "买入" if score > 0 else "卖出"
        return FactorScore(
            "large_trade", round(score, 4), w, net,
            f"{count}笔大单 净{direction} ${abs(net):,.0f}"
        )

    def _score_depth(self, dc: dict) -> FactorScore:
        """Depth Change: 深度不平衡 + 假墙预警"""
        w = self.weights.get("depth_change", 0.08)
        imb = dc.get("depth_imbalance", 0)
        walls = dc.get("wall_events_30s", 0)

        score = _tanh_scale(imb / 100, sensitivity=self._depth_sensitivity)

        if walls >= self._depth_wall_threshold:
            score *= self._depth_wall_mult
            reason = f"深度不平衡 {imb:.1f}% (假墙×{walls}，可信度降低)"
        else:
            reason = f"深度不平衡 {imb:.1f}%"

        return FactorScore("depth_change", round(score, 4), w, imb, reason)

    def _score_funding(self, f_data: dict) -> FactorScore:
        """
        Funding Rate: 反向指标。
        高正费率 → 多头拥挤 → 看空；负费率 → 空头拥挤 → 看多。
        """
        w = self.weights.get("funding", 0.10)
        rate = f_data.get("current_rate", 0)
        extreme = f_data.get("extreme_level", "normal")

        score = _tanh_scale(-rate * 100, sensitivity=self._funding_sensitivity)

        if extreme in ("extreme", "critical"):
            score = _clamp(score * self._funding_extreme_mult)

        reason = f"费率 {rate:.4f}% ({extreme})"
        return FactorScore("funding", round(score, 4), w, rate, reason)

    def _score_liquidation(self, liq: dict) -> FactorScore:
        """
        Liquidation: 清算方向。
        多头清算 → 下行压力 → 看空；空头清算 → 上行压力 → 看多。
        """
        w = self.weights.get("liquidation", 0.08)
        net = liq.get("net_liq_1m", 0)  # positive = long liq dominates
        cascade = liq.get("cascade_level", "none")

        if abs(net) < self._liq_min_net:
            return FactorScore("liquidation", 0.0, w, 0.0, "无显著清算")

        score = _tanh_scale(-net / self._liq_scale, sensitivity=self._liq_sensitivity)

        # 级联加成
        cascade_mult = {"none": 1.0, "minor": 1.2, "major": 1.5, "extreme": 2.0}
        score = _clamp(score * cascade_mult.get(cascade, 1.0))

        direction = "多头" if net > 0 else "空头"
        return FactorScore(
            "liquidation", round(score, 4), w, net,
            f"{direction}清算 ${abs(net):,.0f} (级联:{cascade})"
        )

    def _score_sentiment(self, sent: dict) -> FactorScore:
        """
        Sentiment: 反向情绪。
        散户极度看多 → 看空；恐慌贪婪极端 → 反向。
        鲸鱼方向作为确认。
        """
        w = self.weights.get("sentiment", 0.07)

        fng = sent.get("fear_greed_value", 50)
        fng_score = _tanh_scale((50 - fng) / 50, sensitivity=self._sent_fng_sensitivity)

        retail_ls = sent.get("retail_ls_ratio", 1.0)
        retail_score = _tanh_scale(-(retail_ls - 1.0), sensitivity=self._sent_retail_sensitivity)

        whale_ls = sent.get("whale_ls_ratio", 1.0)
        whale_score = _tanh_scale((whale_ls - 1.0), sensitivity=self._sent_whale_sensitivity)

        score = _clamp(fng_score * self._sent_fng_weight
                       + retail_score * self._sent_retail_weight
                       + whale_score * self._sent_whale_weight)

        label = sent.get("fear_greed_label", "Neutral")
        return FactorScore(
            "sentiment", round(score, 4), w, fng,
            f"恐慌贪婪={fng}({label}) 散户LS={retail_ls:.2f} 鲸鱼LS={whale_ls:.2f}"
        )

    def _score_trend(self, trend: dict) -> FactorScore:
        """Trend: 多周期趋势一致性"""
        w = self.weights.get("trend", 0.08)
        alignment = trend.get("alignment_score", 0)

        # alignment_score: [-5, +5] → [-1, 1]
        score = _clamp(alignment / 5.0)

        vol_trend = trend.get("volume_trend", "stable")
        if vol_trend == "increasing" and abs(score) > self._trend_vol_threshold:
            score = _clamp(score * self._trend_vol_boost)

        label = trend.get("trend_alignment", "mixed")
        return FactorScore("trend", round(score, 4), w, alignment, f"趋势一致性={label} 得分={alignment}")

    def _score_vpin(self, vpin_data: dict) -> FactorScore:
        """
        VPIN: 知情交易概率。
        高 VPIN → 市场风险升高 → 降低其他因子信心（不直接看方向）。
        结合最后一桶买入比例给出微弱方向。
        """
        w = self.weights.get("vpin", 0.04)
        vpin = vpin_data.get("vpin", 0)
        buy_ratio = vpin_data.get("last_bucket_buy_ratio", 0.5)

        if vpin < self._vpin_low_threshold:
            return FactorScore("vpin", 0.0, w, vpin, f"VPIN={vpin:.3f} 市场平稳")

        direction = (buy_ratio - 0.5) * 2
        uncertainty_penalty = 1.0 - (vpin - self._vpin_low_threshold) * self._vpin_penalty_rate
        score = _clamp(direction * uncertainty_penalty * self._vpin_direction_scale)

        return FactorScore(
            "vpin", round(score, 4), w, vpin,
            f"VPIN={vpin:.3f} 买入比={buy_ratio:.2f}"
        )

    def _score_oi(self, oi: dict) -> FactorScore:
        """
        OI: 持仓量变化。
        需结合价格趋势判断：OI↑+价格↑=看多确认，OI↑+价格↓=看空确认。
        单独 OI 只提供弱信号。
        """
        w = self.weights.get("oi", 0.03)
        change = oi.get("oi_change_pct", 0)
        global_1h = oi.get("global_oi_change_1h", 0)

        if abs(change) < self._oi_min_change and abs(global_1h) < self._oi_min_change:
            return FactorScore("oi", 0.0, w, change, f"OI变化 {change:.1f}% 无显著变化")

        score = _tanh_scale(change / 10, sensitivity=self._oi_sensitivity)

        if abs(global_1h) > self._oi_global_threshold:
            global_boost = _tanh_scale(global_1h / 10, sensitivity=self._oi_global_sensitivity)
            score = _clamp(score * self._oi_local_weight + global_boost * self._oi_global_weight)

        return FactorScore(
            "oi", round(score, 4), w, change,
            f"OI变化 {change:.1f}% 全网1h={global_1h:.1f}%"
        )

    # ══════════════════════════════════════════
    # 做市商逻辑因子（新增）
    # ══════════════════════════════════════════

    def _score_vwap(self, vwap: dict) -> FactorScore:
        """
        VWAP: 价格偏离 VWAP 的程度。
        逻辑：
          - 价格远高于 VWAP → 过度拉升，回落概率大 → 看空（均值回归）
          - 价格远低于 VWAP → 过度打压，反弹概率大 → 看多（均值回归）
          - 短周期和长周期偏离方向一致 → 趋势确认，增强信号
        """
        w = self.weights.get("vwap", 0.08)
        dev_5m = vwap.get("deviation_5m_pct", 0)
        dev_15m = vwap.get("deviation_15m_pct", 0)
        dev_1h = vwap.get("deviation_1h_pct", 0)
        vwap_1h = vwap.get("vwap_1h", 0)

        if vwap_1h <= 0:
            return FactorScore("vwap", 0.0, w, 0.0, "VWAP 数据不足")

        mean_reversion = _tanh_scale(-dev_15m / self._vwap_mr_divisor, sensitivity=self._vwap_mr_sensitivity)

        if dev_5m * dev_1h > 0 and abs(dev_1h) > self._vwap_trend_threshold:
            trend_score = _tanh_scale(dev_5m / self._vwap_trend_divisor, sensitivity=self._vwap_trend_sensitivity)
            score = _clamp(mean_reversion * self._vwap_mr_weight + trend_score * self._vwap_trend_weight)
        else:
            score = mean_reversion

        direction = "高于" if dev_15m > 0 else "低于"
        return FactorScore(
            "vwap", round(score, 4), w, dev_15m,
            f"价格{direction}VWAP {abs(dev_15m):.3f}% (5m:{dev_5m:.3f}% 1h:{dev_1h:.3f}%)"
        )

    def _score_volume_profile(self, vp: dict) -> FactorScore:
        """
        Volume Profile: 价格相对 POC 和价值区域的位置。
        逻辑：
          - 价格在 Value Area 内 → 中性（盘整区）
          - 价格突破 VAH → 看多（突破阻力）
          - 价格跌破 VAL → 看空（跌破支撑）
          - 价格接近 POC → 均值回归完成，方向不定
          - 价格接近 HVN → 支撑/阻力确认
        """
        w = self.weights.get("volume_profile", 0.07)
        poc = vp.get("poc_price", 0)
        vah = vp.get("vah_price", 0)
        val = vp.get("val_price", 0)
        in_va = vp.get("in_value_area", False)
        poc_dev = vp.get("price_vs_poc_pct", 0)
        total_vol = vp.get("total_volume_usdt", 0)

        if poc <= 0 or total_vol < self._vp_min_volume:
            return FactorScore("volume_profile", 0.0, w, 0.0, "VP 数据不足")

        if in_va:
            score = _tanh_scale(-poc_dev / self._vp_va_divisor, sensitivity=self._vp_va_sensitivity)
            location = "价值区内"
        elif poc_dev > 0:
            score = _tanh_scale(poc_dev / self._vp_breakout_divisor, sensitivity=self._vp_breakout_sensitivity)
            location = "突破VAH上方"
        else:
            score = _tanh_scale(poc_dev / self._vp_breakout_divisor, sensitivity=self._vp_breakout_sensitivity)
            location = "跌破VAL下方"

        return FactorScore(
            "volume_profile", round(score, 4), w, poc_dev,
            f"{location} POC偏离{poc_dev:.3f}% POC={poc:.1f} VA=[{val:.1f},{vah:.1f}]"
        )

    def _score_absorption(self, abs_data: dict) -> FactorScore:
        """
        Absorption: 吸收检测 — 大资金意图最直接的暴露。
        逻辑：
          - 买方吸收（卖单大量成交但价格不跌）→ 大买家在接盘 → 看涨
          - 卖方吸收（买单大量成交但价格不涨）→ 大卖家在出货 → 看跌
          - 吸收事件频繁出现 → 方向信号更强
        """
        w = self.weights.get("absorption", 0.10)
        net = abs_data.get("net_absorption_30s", 0)
        buy_abs = abs_data.get("buy_absorption_30s", 0)
        sell_abs = abs_data.get("sell_absorption_30s", 0)
        is_absorbing = abs_data.get("is_absorbing", False)
        side = abs_data.get("absorption_side", "none")
        events = abs_data.get("event_count_5m", 0)

        if not is_absorbing and events == 0:
            return FactorScore("absorption", 0.0, w, 0.0, "无吸收信号")

        # 净吸收方向 → 直接作为分数
        score = _clamp(net)

        if events >= self._abs_event_threshold:
            event_boost = min(events * self._abs_event_boost_rate, self._abs_event_boost_max)
            if score > 0:
                score = _clamp(score + event_boost)
            elif score < 0:
                score = _clamp(score - event_boost)

        if side == "buy":
            reason = f"买方吸收 强度={buy_abs:.3f} (5m事件×{events})"
        elif side == "sell":
            reason = f"卖方吸收 强度={sell_abs:.3f} (5m事件×{events})"
        else:
            reason = f"弱吸收 净值={net:.3f} (5m事件×{events})"

        return FactorScore("absorption", round(score, 4), w, net, reason)
