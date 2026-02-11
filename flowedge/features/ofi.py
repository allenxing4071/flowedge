"""
OFI（Order Flow Imbalance）订单流不平衡度
基于 Cont et al. (2014) 论文方法，从订单簿增量变化中提取信号。
核心信号：OFI 极端偏离（>2σ）→ 短期价格方向的领先指标。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .ring_buffer import RingBuffer


@dataclass
class OFISnapshot:
    """OFI 特征快照"""
    ofi_10s: float        # 10 秒窗口累计 OFI
    ofi_30s: float        # 30 秒窗口累计 OFI
    ofi_1m: float         # 1 分钟窗口累计 OFI
    ofi_instant: float    # 最新一次 OFI 值
    sigma_30s: float      # 30 秒 OFI 的标准差（用于判断极端偏离）
    z_score_30s: float    # 当前 OFI 在 30s 窗口中的 z-score


class OFICalculator:
    """
    OFI 计算器。

    每次 depth 更新时，计算前 N 档 bid/ask 的数量变化：
    - delta_bid_qty = sum(新bid各档qty - 旧bid各档qty)
    - delta_ask_qty = sum(新ask各档qty - 旧ask各档qty)
    - OFI = delta_bid_qty - delta_ask_qty

    正 OFI → 买方力量增加，负 OFI → 卖方力量增加。
    """

    def __init__(self, levels: int = 5, capacity: int = 6000):
        self._levels = levels
        self._ofi_buf = RingBuffer(capacity=capacity)
        self._ofi_sq_buf = RingBuffer(capacity=capacity)  # OFI² 用于计算标准差
        # 上一次的订单簿前 N 档
        self._prev_bids: list[float] = []
        self._prev_asks: list[float] = []
        self._last_ofi = 0.0

    def on_depth_update(
        self,
        bids: list[list[float]],  # [[price, qty], ...]
        asks: list[list[float]],  # [[price, qty], ...]
        timestamp_ms: int,
    ) -> None:
        """
        收到一次 depth 更新。
        bids/asks 已排序（bids 价高→低，asks 价低→高）。
        """
        # 提取前 N 档的数量
        bid_qtys = [q for _, q in bids[:self._levels]]
        ask_qtys = [q for _, q in asks[:self._levels]]

        if not self._prev_bids:
            # 第一次更新，只记录不计算
            self._prev_bids = bid_qtys
            self._prev_asks = ask_qtys
            return

        # 计算各档数量变化
        delta_bid = 0.0
        for i in range(min(len(bid_qtys), len(self._prev_bids))):
            delta_bid += bid_qtys[i] - self._prev_bids[i]

        delta_ask = 0.0
        for i in range(min(len(ask_qtys), len(self._prev_asks))):
            delta_ask += ask_qtys[i] - self._prev_asks[i]

        ofi = delta_bid - delta_ask
        self._last_ofi = ofi
        self._ofi_buf.push(ofi, timestamp_ms)
        self._ofi_sq_buf.push(ofi * ofi, timestamp_ms)

        # 记录当前值作为下一次的 prev
        self._prev_bids = bid_qtys
        self._prev_asks = ask_qtys

    def snapshot(self) -> OFISnapshot:
        """获取当前 OFI 快照"""
        now_ms = int(time.time() * 1000)
        ofi_10s = self._ofi_buf.window_sum(now_ms - 10_000)
        ofi_30s = self._ofi_buf.window_sum(now_ms - 30_000)
        ofi_1m = self._ofi_buf.window_sum(now_ms - 60_000)

        # 计算 30s 标准差和 z-score
        n30 = self._ofi_buf.window_count(now_ms - 30_000)
        if n30 > 1:
            mean_30 = ofi_30s / n30
            sum_sq_30 = self._ofi_sq_buf.window_sum(now_ms - 30_000)
            variance = max(0, sum_sq_30 / n30 - mean_30 * mean_30)
            sigma = variance ** 0.5
            z_score = (self._last_ofi - mean_30) / sigma if sigma > 0 else 0.0
        else:
            sigma = 0.0
            z_score = 0.0

        return OFISnapshot(
            ofi_10s=round(ofi_10s, 4),
            ofi_30s=round(ofi_30s, 4),
            ofi_1m=round(ofi_1m, 4),
            ofi_instant=round(self._last_ofi, 4),
            sigma_30s=round(sigma, 4),
            z_score_30s=round(z_score, 2),
        )
