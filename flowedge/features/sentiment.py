"""
多空情绪特征计算器
综合散户多空比、大户多空比、恐慌贪婪指数、全网多空比，输出情绪快照。
核心价值：散户越看多 → 越可能跌（经典反向指标）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SentimentSnapshot:
    """多空情绪特征快照"""
    # 散户多空比（币安全网）
    retail_ls_ratio: float       # > 1 = 散户偏多
    retail_long_pct: float       # 多头账户占比 %
    retail_short_pct: float
    # 大户多空比（账户数）
    whale_ls_ratio: float        # > 1 = 大户偏多
    whale_long_pct: float
    whale_short_pct: float
    # 大户持仓量比
    whale_position_ratio: float
    whale_long_position_pct: float
    whale_short_position_pct: float
    # 散户 vs 大户分歧度（绝对值越大 = 分歧越大）
    divergence: float            # retail_ls - whale_ls，正 = 散户比大户更看多
    # 恐慌贪婪指数
    fear_greed_value: int        # 0-100
    fear_greed_label: str        # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
    fear_greed_trend: str        # "rising" / "falling" / "stable"
    # Coinalyze 全网多空比（如有）
    global_ls_ratio: float
    # 综合信号
    signal: str
    contrarian_signal: str       # 反向信号（核心价值）


class SentimentTracker:
    """
    多空情绪追踪器。
    从 BinanceRestCollector 和 ExternalDataCollector 获取数据。
    """

    def __init__(self):
        # 散户
        self._retail_ls = 1.0
        self._retail_long_pct = 50.0
        self._retail_short_pct = 50.0
        # 大户账户
        self._whale_ls = 1.0
        self._whale_long_pct = 50.0
        self._whale_short_pct = 50.0
        # 大户持仓
        self._whale_pos_ratio = 1.0
        self._whale_long_pos_pct = 50.0
        self._whale_short_pos_pct = 50.0
        # 恐慌贪婪
        self._fng_value = 50
        self._fng_label = "Neutral"
        self._fng_trend = "stable"
        # 全网
        self._global_ls = 1.0

    def update_retail(self, ratio: float, long_pct: float, short_pct: float) -> None:
        self._retail_ls = ratio
        self._retail_long_pct = long_pct
        self._retail_short_pct = short_pct

    def update_whale(self, ratio: float, long_pct: float, short_pct: float) -> None:
        self._whale_ls = ratio
        self._whale_long_pct = long_pct
        self._whale_short_pct = short_pct

    def update_whale_position(self, ratio: float, long_pct: float, short_pct: float) -> None:
        self._whale_pos_ratio = ratio
        self._whale_long_pos_pct = long_pct
        self._whale_short_pos_pct = short_pct

    def update_fear_greed(self, value: int, label: str, trend: str) -> None:
        self._fng_value = value
        self._fng_label = label
        self._fng_trend = trend

    def update_global_ls(self, ratio: float) -> None:
        self._global_ls = ratio

    def snapshot(self) -> SentimentSnapshot:
        # 散户 vs 大户分歧
        divergence = round(self._retail_ls - self._whale_ls, 4)

        # 综合信号
        signals = []
        if self._retail_ls > 1.5:
            signals.append(f"散户极度看多({self._retail_ls:.2f})")
        elif self._retail_ls > 1.2:
            signals.append(f"散户偏多({self._retail_ls:.2f})")
        elif self._retail_ls < 0.67:
            signals.append(f"散户极度看空({self._retail_ls:.2f})")
        elif self._retail_ls < 0.83:
            signals.append(f"散户偏空({self._retail_ls:.2f})")

        if self._whale_ls > 1.3:
            signals.append(f"大户看多({self._whale_ls:.2f})")
        elif self._whale_ls < 0.77:
            signals.append(f"大户看空({self._whale_ls:.2f})")

        if self._fng_value >= 80:
            signals.append(f"极度贪婪({self._fng_value})")
        elif self._fng_value >= 60:
            signals.append(f"贪婪({self._fng_value})")
        elif self._fng_value <= 20:
            signals.append(f"极度恐慌({self._fng_value})")
        elif self._fng_value <= 40:
            signals.append(f"恐慌({self._fng_value})")

        signal = "; ".join(signals) if signals else "情绪中性"

        # 反向信号（核心）
        contrarian = ""
        if self._retail_ls > 1.5 and self._fng_value >= 75:
            contrarian = "散户极度看多+极度贪婪 → 强烈看跌反向信号"
        elif self._retail_ls > 1.3 and self._fng_value >= 60:
            contrarian = "散户偏多+贪婪 → 中等看跌反向信号"
        elif self._retail_ls < 0.67 and self._fng_value <= 25:
            contrarian = "散户极度看空+极度恐慌 → 强烈看涨反向信号"
        elif self._retail_ls < 0.83 and self._fng_value <= 40:
            contrarian = "散户偏空+恐慌 → 中等看涨反向信号"
        elif abs(divergence) > 0.3:
            if divergence > 0:
                contrarian = f"散户比大户更看多(分歧{divergence:+.2f}) → 关注做空机会"
            else:
                contrarian = f"大户比散户更看多(分歧{divergence:+.2f}) → 跟随大户方向"
        else:
            contrarian = "无明显反向信号"

        return SentimentSnapshot(
            retail_ls_ratio=self._retail_ls,
            retail_long_pct=self._retail_long_pct,
            retail_short_pct=self._retail_short_pct,
            whale_ls_ratio=self._whale_ls,
            whale_long_pct=self._whale_long_pct,
            whale_short_pct=self._whale_short_pct,
            whale_position_ratio=self._whale_pos_ratio,
            whale_long_position_pct=self._whale_long_pos_pct,
            whale_short_position_pct=self._whale_short_pos_pct,
            divergence=divergence,
            fear_greed_value=self._fng_value,
            fear_greed_label=self._fng_label,
            fear_greed_trend=self._fng_trend,
            global_ls_ratio=self._global_ls,
            signal=signal,
            contrarian_signal=contrarian,
        )
