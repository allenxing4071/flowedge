"""
CVD（Cumulative Volume Delta）累计成交量偏差
核心信号：CVD 与价格的背离 → 趋势反转/延续的领先指标。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .ring_buffer import RingBuffer


@dataclass
class CVDSnapshot:
    """CVD 特征快照（含量价背离检测）"""
    cvd_1m: float       # 最近 1 分钟净 delta（正=买方主导）
    cvd_5m: float       # 最近 5 分钟净 delta
    cvd_total: float    # 连接以来累计 delta
    buy_vol_1m: float   # 最近 1 分钟买方成交额
    sell_vol_1m: float  # 最近 1 分钟卖方成交额
    trade_count_1m: int # 最近 1 分钟成交笔数
    # 量价背离检测（新增）
    divergence_score: float  # 背离分数 [-1, 1]，正=看涨背离，负=看跌背离，0=无背离
    divergence_type: str     # "bullish_div" / "bearish_div" / "none"


class CVDCalculator:
    """
    CVD 计算器。

    逐笔累加 taker 方向的成交额：
    - taker_buy: qty_usdt 为正
    - taker_sell: qty_usdt 为负
    CVD = cumsum(signed_qty_usdt)

    提供 1m / 5m 滚动窗口。
    """

    def __init__(self, capacity: int = 60000):
        # delta 缓冲：正=买，负=卖
        self._delta_buf = RingBuffer(capacity=capacity)
        # 绝对值缓冲（用于统计买/卖成交额）
        self._buy_buf = RingBuffer(capacity=capacity)
        self._sell_buf = RingBuffer(capacity=capacity)
        # 价格缓冲（用于量价背离检测）
        self._price_buf = RingBuffer(capacity=capacity)
        # 总累计
        self._total_delta = 0.0

    def on_trade(self, qty_usdt: float, is_taker_buy: bool, timestamp_ms: int,
                 price: float = 0.0) -> None:
        """收到一笔成交（price 用于量价背离检测）"""
        signed = qty_usdt if is_taker_buy else -qty_usdt
        self._delta_buf.push(signed, timestamp_ms)
        self._total_delta += signed

        if is_taker_buy:
            self._buy_buf.push(qty_usdt, timestamp_ms)
            self._sell_buf.push(0.0, timestamp_ms)
        else:
            self._buy_buf.push(0.0, timestamp_ms)
            self._sell_buf.push(qty_usdt, timestamp_ms)

        # 记录成交价格（用于背离检测）
        if price > 0:
            self._price_buf.push(price, timestamp_ms)

    def _detect_divergence(self, _now_ms: int) -> tuple[float, str]:
        """
        量价背离检测（南哥核心方法：Delta 背离 = 真金白银的反转信号）。

        逻辑：
          - 比较最近 1 分钟 vs 前 1 分钟（1-2 分钟前）的价格和 CVD
          - 看跌背离：价格新高但 CVD 未新高（买方力量衰竭）
          - 看涨背离：价格新低但 CVD 未新低（卖方力量衰竭）

        返回 (divergence_score, divergence_type)
          divergence_score: [-1, 1]，正=看涨背离，负=看跌背离
        """
        import numpy as np

        # 最近 1 分钟和前 1 分钟的数据
        recent_prices = self._price_buf.recent_values(600)  # 约 1-2 分钟的 aggTrade
        recent_cvd = self._delta_buf.recent_values(600)

        if len(recent_prices) < 100 or len(recent_cvd) < 100:
            return 0.0, "none"

        # 分成两半：前半段 vs 后半段
        mid = len(recent_prices) // 2
        first_prices = recent_prices[:mid]
        second_prices = recent_prices[mid:]
        first_cvd = recent_cvd[:mid]
        second_cvd = recent_cvd[mid:]

        # 价格变化方向
        price_first_max = float(np.max(first_prices))
        price_second_max = float(np.max(second_prices))
        price_first_min = float(np.min(first_prices))
        price_second_min = float(np.min(second_prices))

        # CVD 累计变化
        cvd_first_sum = float(np.sum(first_cvd))
        cvd_second_sum = float(np.sum(second_cvd))

        # 看跌背离：价格创新高但 CVD 没跟上
        if price_second_max > price_first_max and price_first_max > 0:
            price_gain = (price_second_max - price_first_max) / price_first_max
            if cvd_second_sum < cvd_first_sum * 0.5:
                # CVD 明显弱于前期 → 看跌背离
                strength = min(1.0, price_gain * 100)  # 价格涨幅越大背离越强
                return -strength, "bearish_div"

        # 看涨背离：价格创新低但 CVD 没跟上
        if price_second_min < price_first_min and price_first_min > 0:
            price_drop = (price_first_min - price_second_min) / price_first_min
            if cvd_second_sum > cvd_first_sum * 0.5:
                # CVD 明显强于前期 → 看涨背离
                strength = min(1.0, price_drop * 100)
                return strength, "bullish_div"

        return 0.0, "none"

    def snapshot(self) -> CVDSnapshot:
        """获取当前 CVD 快照（含量价背离检测）"""
        now_ms = int(time.time() * 1000)
        since_1m = now_ms - 60_000
        since_5m = now_ms - 300_000

        div_score, div_type = self._detect_divergence(now_ms)

        return CVDSnapshot(
            cvd_1m=round(self._delta_buf.window_sum(since_1m), 2),
            cvd_5m=round(self._delta_buf.window_sum(since_5m), 2),
            cvd_total=round(self._total_delta, 2),
            buy_vol_1m=round(self._buy_buf.window_sum(since_1m), 2),
            sell_vol_1m=round(self._sell_buf.window_sum(since_1m), 2),
            trade_count_1m=self._delta_buf.window_count(since_1m),
            divergence_score=round(div_score, 3),
            divergence_type=div_type,
        )
