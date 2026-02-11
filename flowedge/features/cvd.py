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
    """CVD 特征快照"""
    cvd_1m: float       # 最近 1 分钟净 delta（正=买方主导）
    cvd_5m: float       # 最近 5 分钟净 delta
    cvd_total: float    # 连接以来累计 delta
    buy_vol_1m: float   # 最近 1 分钟买方成交额
    sell_vol_1m: float  # 最近 1 分钟卖方成交额
    trade_count_1m: int # 最近 1 分钟成交笔数


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
        # 总累计
        self._total_delta = 0.0

    def on_trade(self, qty_usdt: float, is_taker_buy: bool, timestamp_ms: int) -> None:
        """收到一笔成交"""
        signed = qty_usdt if is_taker_buy else -qty_usdt
        self._delta_buf.push(signed, timestamp_ms)
        self._total_delta += signed

        if is_taker_buy:
            self._buy_buf.push(qty_usdt, timestamp_ms)
            self._sell_buf.push(0.0, timestamp_ms)
        else:
            self._buy_buf.push(0.0, timestamp_ms)
            self._sell_buf.push(qty_usdt, timestamp_ms)

    def snapshot(self) -> CVDSnapshot:
        """获取当前 CVD 快照"""
        now_ms = int(time.time() * 1000)
        since_1m = now_ms - 60_000
        since_5m = now_ms - 300_000

        return CVDSnapshot(
            cvd_1m=round(self._delta_buf.window_sum(since_1m), 2),
            cvd_5m=round(self._delta_buf.window_sum(since_5m), 2),
            cvd_total=round(self._total_delta, 2),
            buy_vol_1m=round(self._buy_buf.window_sum(since_1m), 2),
            sell_vol_1m=round(self._sell_buf.window_sum(since_1m), 2),
            trade_count_1m=self._delta_buf.window_count(since_1m),
        )
