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
    """OFI 特征快照（多时间窗口 + 趋势 + 背离检测）"""
    ofi_10s: float        # 10 秒窗口累计 OFI
    ofi_30s: float        # 30 秒窗口累计 OFI
    ofi_1m: float         # 1 分钟窗口累计 OFI
    ofi_5m: float         # 5 分钟窗口累计 OFI（新增）
    ofi_15m: float        # 15 分钟窗口累计 OFI（新增）
    ofi_instant: float    # 最新一次 OFI 值
    sigma_30s: float      # 30 秒 OFI 的标准差
    z_score_30s: float    # 当前 OFI 在 30s 窗口中的 z-score
    z_score_5m: float     # 当前 OFI 在 5m 窗口中的 z-score（新增）
    # 趋势指标（新增）
    ofi_trend: float      # OFI 趋势：近 30s 均值 vs 前 30s 均值的变化率
    ofi_acceleration: float  # OFI 加速度：趋势的变化率
    # 多窗口一致性（新增）
    multi_window_agreement: float  # 多窗口方向一致性 [-1, 1]


class OFICalculator:
    """
    OFI 计算器。

    每次 depth 更新时，计算前 N 档 bid/ask 的数量变化：
    - delta_bid_qty = sum(新bid各档qty - 旧bid各档qty)
    - delta_ask_qty = sum(新ask各档qty - 旧ask各档qty)
    - OFI = delta_bid_qty - delta_ask_qty

    正 OFI → 买方力量增加，负 OFI → 卖方力量增加。
    """

    def __init__(self, levels: int = 5, capacity: int = 90000):
        # capacity 扩大到 90000（depth@100ms → 15 分钟 = 9000 条，留 10x 余量）
        self._levels = levels
        self._ofi_buf = RingBuffer(capacity=capacity)
        self._ofi_sq_buf = RingBuffer(capacity=capacity)  # OFI² 用于计算标准差
        # 上一次的订单簿前 N 档
        self._prev_bids: list[float] = []
        self._prev_asks: list[float] = []
        self._last_ofi = 0.0
        # 趋势追踪：记录上一次快照的 30s 均值，用于计算趋势变化率
        self._prev_mean_30s = 0.0
        self._prev_trend = 0.0

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

    def _calc_z_score(self, now_ms: int, window_ms: int) -> tuple[float, float, float]:
        """计算指定窗口的 z-score，返回 (z_score, mean, sigma)"""
        since = now_ms - window_ms
        n = self._ofi_buf.window_count(since)
        if n <= 1:
            return 0.0, 0.0, 0.0
        ofi_sum = self._ofi_buf.window_sum(since)
        mean = ofi_sum / n
        sum_sq = self._ofi_sq_buf.window_sum(since)
        variance = max(0, sum_sq / n - mean * mean)
        sigma = variance ** 0.5
        z = (self._last_ofi - mean) / sigma if sigma > 0 else 0.0
        return z, mean, sigma

    def snapshot(self) -> OFISnapshot:
        """获取当前 OFI 快照（多时间窗口 + 趋势 + 一致性）"""
        now_ms = int(time.time() * 1000)
        ofi_10s = self._ofi_buf.window_sum(now_ms - 10_000)
        ofi_30s = self._ofi_buf.window_sum(now_ms - 30_000)
        ofi_1m = self._ofi_buf.window_sum(now_ms - 60_000)
        ofi_5m = self._ofi_buf.window_sum(now_ms - 300_000)
        ofi_15m = self._ofi_buf.window_sum(now_ms - 900_000)

        # 多窗口 z-score
        z_30s, mean_30s, sigma_30s = self._calc_z_score(now_ms, 30_000)
        z_5m, _, _ = self._calc_z_score(now_ms, 300_000)

        # ── OFI 趋势：近 30s 均值 vs 前 30-60s 均值 ──
        # 趋势 > 0 表示 OFI 在加速偏向买方，< 0 表示加速偏向卖方
        n30 = self._ofi_buf.window_count(now_ms - 30_000)
        n60 = self._ofi_buf.window_count(now_ms - 60_000)
        if n30 > 0 and n60 > n30:
            current_mean = ofi_30s / n30
            prev_ofi = ofi_1m - ofi_30s  # 前 30-60s 的 OFI 总量
            prev_n = n60 - n30
            prev_mean = prev_ofi / prev_n if prev_n > 0 else 0
            # 趋势 = 当前均值 - 前期均值（归一化到 sigma 尺度）
            if sigma_30s > 0:
                trend = (current_mean - prev_mean) / sigma_30s
            else:
                trend = 0.0
        else:
            current_mean = mean_30s
            trend = 0.0

        # OFI 加速度 = 当前趋势 - 上次趋势
        acceleration = trend - self._prev_trend
        self._prev_trend = trend
        self._prev_mean_30s = current_mean

        # ── 多窗口方向一致性 ──
        # 检查 10s/30s/1m/5m 的 OFI 方向是否一致
        windows = [ofi_10s, ofi_30s, ofi_1m, ofi_5m]
        positive = sum(1 for w in windows if w > 0)
        negative = sum(1 for w in windows if w < 0)
        total_windows = len(windows)
        if positive > negative:
            agreement = positive / total_windows  # [0.25, 1.0]
        elif negative > positive:
            agreement = -negative / total_windows  # [-1.0, -0.25]
        else:
            agreement = 0.0

        return OFISnapshot(
            ofi_10s=round(ofi_10s, 4),
            ofi_30s=round(ofi_30s, 4),
            ofi_1m=round(ofi_1m, 4),
            ofi_5m=round(ofi_5m, 4),
            ofi_15m=round(ofi_15m, 4),
            ofi_instant=round(self._last_ofi, 4),
            sigma_30s=round(sigma_30s, 4),
            z_score_30s=round(z_30s, 2),
            z_score_5m=round(z_5m, 2),
            ofi_trend=round(trend, 3),
            ofi_acceleration=round(acceleration, 3),
            multi_window_agreement=round(agreement, 3),
        )
