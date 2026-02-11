"""
多因子评分引擎 — 将 11 个原始特征转化为标准化方向分数。

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
from typing import Optional


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


# ── 因子权重配置 ──
# 微观结构因子（CVD + OFI + Book）权重最大——这是实时边
# 聪明钱因子（大单 + 深度变化）次之
# 宏观因子（趋势 + 情绪）提供方向背景

DEFAULT_WEIGHTS = {
    "cvd":            0.15,   # 成交量 delta — 实时买卖力量
    "ofi":            0.15,   # 订单流不平衡 — 挂单变化
    "book_imbalance": 0.10,   # L1 盘口压力
    "large_trade":    0.12,   # 大单资金流 — 聪明钱
    "depth_change":   0.08,   # 深度变化 + 假墙检测
    "funding":        0.10,   # 资金费率 — 反向指标
    "liquidation":    0.08,   # 清算级联 — 极端市况
    "sentiment":      0.07,   # 多空情绪 + 恐慌贪婪
    "trend":          0.08,   # 多周期趋势一致性
    "vpin":           0.04,   # 知情交易概率
    "oi":             0.03,   # 持仓量变化
}
# 权重合计 = 1.00


# ── 评分阈值 ──

# 综合信号阈值
SIGNAL_THRESHOLDS = {
    "strong_buy":  0.40,
    "buy":         0.15,
    "sell":       -0.15,
    "strong_sell": -0.40,
}


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _tanh_scale(v: float, sensitivity: float = 1.0) -> float:
    """用 tanh 将任意范围映射到 [-1, 1]，sensitivity 控制灵敏度"""
    return math.tanh(v * sensitivity)


class SignalScorer:
    """
    多因子评分器：读取特征快照 dict → 输出 CompositeSignal。
    """

    def __init__(self, weights: Optional[dict] = None):
        self.weights = weights or DEFAULT_WEIGHTS.copy()

    def score(self, features: dict, timestamp_ms: int = 0) -> CompositeSignal:
        """
        对单个币种的特征快照进行多因子评分。

        参数:
            features: 来自 FeatureEngine.get_snapshot()[symbol] 的字典
            timestamp_ms: 当前时间戳

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

        # ── 加权合成 ──
        total_score = sum(f.score * f.weight for f in factors)

        # ── 置信度：因子一致性 ──
        bullish = sum(1 for f in factors if f.score > 0.1)
        bearish = sum(1 for f in factors if f.score < -0.1)
        neutral = len(factors) - bullish - bearish
        dominant = max(bullish, bearish)
        confidence = dominant / len(factors) if factors else 0.0

        # 加强：如果方向一致性极高，上调置信度
        if dominant >= 8:
            confidence = min(1.0, confidence * 1.2)

        # ── 信号分级 ──
        if total_score >= SIGNAL_THRESHOLDS["strong_buy"]:
            signal = "STRONG_BUY"
        elif total_score >= SIGNAL_THRESHOLDS["buy"]:
            signal = "BUY"
        elif total_score <= SIGNAL_THRESHOLDS["strong_sell"]:
            signal = "STRONG_SELL"
        elif total_score <= SIGNAL_THRESHOLDS["sell"]:
            signal = "SELL"
        else:
            signal = "NEUTRAL"

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
    # 各因子评分逻辑
    # ══════════════════════════════════════════

    def _score_cvd(self, cvd: dict) -> FactorScore:
        """CVD: 买卖 delta 方向 + 5 分钟趋势"""
        w = self.weights.get("cvd", 0.15)
        buy = cvd.get("buy_vol_1m", 0)
        sell = cvd.get("sell_vol_1m", 0)
        total = buy + sell

        if total < 100:  # 成交量太低，无信号
            return FactorScore("cvd", 0.0, w, 0.0, "成交量不足")

        # 买卖比 → 方向
        ratio = (buy - sell) / total  # [-1, 1]
        # 5 分钟 CVD 趋势作为确认
        cvd_5m = cvd.get("cvd_5m", 0)
        if cvd_5m != 0 and total > 0:
            trend_boost = _clamp(cvd_5m / total * 0.3, -0.3, 0.3)
        else:
            trend_boost = 0

        score = _clamp(ratio + trend_boost)
        direction = "买方主导" if score > 0 else "卖方主导"
        return FactorScore("cvd", round(score, 4), w, ratio, f"{direction} 比率{ratio:.2f}")

    def _score_ofi(self, ofi: dict) -> FactorScore:
        """OFI: 订单流不平衡 z-score"""
        w = self.weights.get("ofi", 0.15)
        z = ofi.get("z_score_30s", 0)

        # z-score 直接用 tanh 映射
        score = _tanh_scale(z, sensitivity=0.5)
        reason = f"z-score={z:.2f}"
        if abs(z) > 2:
            reason += " 极端"
        return FactorScore("ofi", round(score, 4), w, z, reason)

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

        if total < 10000:
            return FactorScore("large_trade", 0.0, w, 0.0, "无大单")

        ratio = net / total  # [-1, 1]
        count = lt.get("count_30s", 0)
        # 大单数量加成
        count_boost = min(count * 0.05, 0.2)
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

        # 深度不平衡 → [-1, 1]
        score = _tanh_scale(imb / 100, sensitivity=2.0)

        # 假墙出现时降低该因子可信度
        if walls >= 2:
            score *= 0.5
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

        # 反向映射：positive funding → negative score
        # 典型费率范围: -0.05% ~ +0.1%，极端时 ±0.5%
        score = _tanh_scale(-rate * 100, sensitivity=3.0)

        # 极端等级加成
        if extreme in ("extreme", "critical"):
            score = _clamp(score * 1.5)

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

        if abs(net) < 1000:
            return FactorScore("liquidation", 0.0, w, 0.0, "无显著清算")

        # 多头清算 → 看空 → negative score
        score = _tanh_scale(-net / 500000, sensitivity=1.0)

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

        # 恐慌贪婪指数 → 反向
        fng = sent.get("fear_greed_value", 50)
        fng_score = _tanh_scale((50 - fng) / 50, sensitivity=1.5)

        # 散户多空比 → 反向（散户多 → 看空）
        retail_ls = sent.get("retail_ls_ratio", 1.0)
        retail_score = _tanh_scale(-(retail_ls - 1.0), sensitivity=2.0)

        # 鲸鱼多空比 → 正向（跟鲸鱼）
        whale_ls = sent.get("whale_ls_ratio", 1.0)
        whale_score = _tanh_scale((whale_ls - 1.0), sensitivity=2.0)

        # 加权：恐慌贪婪 30% + 反向散户 30% + 跟鲸鱼 40%
        score = _clamp(fng_score * 0.3 + retail_score * 0.3 + whale_score * 0.4)

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
        # 放量确认趋势
        if vol_trend == "increasing" and abs(score) > 0.2:
            score = _clamp(score * 1.2)

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

        if vpin < 0.3:
            # 低 VPIN → 市场平稳，无信号
            return FactorScore("vpin", 0.0, w, vpin, f"VPIN={vpin:.3f} 市场平稳")

        # 高 VPIN 时，买入比例偏向哪边
        direction = (buy_ratio - 0.5) * 2  # [-1, 1]
        # VPIN 越高，方向信号越弱（不确定性高）
        uncertainty_penalty = 1.0 - (vpin - 0.3) * 0.5  # [1.0 → 0.65]
        score = _clamp(direction * uncertainty_penalty * 0.5)

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

        if abs(change) < 0.5 and abs(global_1h) < 0.5:
            return FactorScore("oi", 0.0, w, change, f"OI变化 {change:.1f}% 无显著变化")

        # OI 变化方向：上升 → 市场参与度增加，但需要价格方向确认
        # 这里给一个弱信号：大幅上升 → 可能有行情（方向不定，给轻微正分）
        score = _tanh_scale(change / 10, sensitivity=0.5)

        # 全网 1h 变化作为确认
        if abs(global_1h) > 2:
            global_boost = _tanh_scale(global_1h / 10, sensitivity=0.3)
            score = _clamp(score * 0.6 + global_boost * 0.4)

        return FactorScore(
            "oi", round(score, 4), w, change,
            f"OI变化 {change:.1f}% 全网1h={global_1h:.1f}%"
        )
