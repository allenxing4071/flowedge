"""
VWAP（Volume Weighted Average Price）成交量加权平均价格
核心信号：价格偏离 VWAP 的程度 → 均值回归/趋势确认信号。

做市商逻辑：
  - VWAP 是动态的"公平价格"，做市商围绕 VWAP 挂单
  - 价格大幅偏离 VWAP → 回归概率高（均值回归交易）
  - 价格持续在 VWAP 上方/下方运行 → 趋势确认

实现：
  - 滚动 VWAP：分别计算 5m / 15m / 1h 时间窗口
  - 偏离度：(price - vwap) / vwap × 100%
  - 标准差带：VWAP ± 1σ / 2σ，类似布林带
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class VWAPSnapshot:
    """VWAP 特征快照"""
    vwap_5m: float          # 5 分钟 VWAP
    vwap_15m: float         # 15 分钟 VWAP
    vwap_1h: float          # 1 小时 VWAP
    deviation_5m_pct: float   # 当前价格偏离 5m VWAP 的百分比
    deviation_15m_pct: float  # 当前价格偏离 15m VWAP 的百分比
    deviation_1h_pct: float   # 当前价格偏离 1h VWAP 的百分比
    upper_band_1h: float    # VWAP + 2σ
    lower_band_1h: float    # VWAP - 2σ
    band_width_pct: float   # 带宽百分比 = (upper - lower) / vwap × 100
    current_price: float    # 当前价格（最后一笔成交价）


class VWAPCalculator:
    """
    VWAP 计算器。

    原理：VWAP = Σ(price × volume) / Σ(volume)

    维护三个时间窗口（5m / 15m / 1h）的滚动 VWAP。
    同时计算价格偏离度和标准差带。
    """

    def __init__(self):
        # 存储 (timestamp_ms, price, volume_usdt, price²×volume) 元组
        # 用于计算 VWAP 和标准差
        self._trades: deque = deque()

        # 滚动聚合值（避免每次全量遍历）
        self._current_price: float = 0.0

        # 窗口时长（毫秒）
        self._windows = {
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
        }

        # 最大保留 1 小时数据
        self._max_age_ms = 60 * 60 * 1000

    def on_trade(self, price: float, qty_usdt: float, timestamp_ms: int) -> None:
        """收到一笔成交"""
        self._current_price = price
        self._trades.append((timestamp_ms, price, qty_usdt))

        # 清理过期数据
        cutoff = timestamp_ms - self._max_age_ms
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()

    def _calc_vwap_and_std(self, since_ms: int) -> tuple[float, float]:
        """
        计算指定时间窗口的 VWAP 和价格标准差。
        返回 (vwap, std_dev)
        """
        sum_pv = 0.0   # Σ(price × volume)
        sum_v = 0.0    # Σ(volume)
        sum_p2v = 0.0  # Σ(price² × volume)

        for ts, price, vol in self._trades:
            if ts >= since_ms:
                sum_pv += price * vol
                sum_v += vol
                sum_p2v += price * price * vol

        if sum_v < 1.0:  # 成交量不足
            return 0.0, 0.0

        vwap = sum_pv / sum_v

        # 加权标准差: sqrt(Σ(price² × vol) / Σ(vol) - vwap²)
        variance = sum_p2v / sum_v - vwap * vwap
        std_dev = math.sqrt(max(0, variance))

        return vwap, std_dev

    def snapshot(self) -> VWAPSnapshot:
        """获取当前 VWAP 快照"""
        now_ms = int(time.time() * 1000)
        price = self._current_price

        # 计算三个窗口的 VWAP
        vwap_5m, _ = self._calc_vwap_and_std(now_ms - self._windows["5m"])
        vwap_15m, _ = self._calc_vwap_and_std(now_ms - self._windows["15m"])
        vwap_1h, std_1h = self._calc_vwap_and_std(now_ms - self._windows["1h"])

        # 偏离度计算
        def deviation_pct(vwap: float) -> float:
            if vwap <= 0 or price <= 0:
                return 0.0
            return round((price - vwap) / vwap * 100, 4)

        # 标准差带（基于 1h VWAP）
        upper = vwap_1h + 2 * std_1h if vwap_1h > 0 else 0.0
        lower = vwap_1h - 2 * std_1h if vwap_1h > 0 else 0.0
        band_width = ((upper - lower) / vwap_1h * 100) if vwap_1h > 0 else 0.0

        return VWAPSnapshot(
            vwap_5m=round(vwap_5m, 2),
            vwap_15m=round(vwap_15m, 2),
            vwap_1h=round(vwap_1h, 2),
            deviation_5m_pct=deviation_pct(vwap_5m),
            deviation_15m_pct=deviation_pct(vwap_15m),
            deviation_1h_pct=deviation_pct(vwap_1h),
            upper_band_1h=round(upper, 2),
            lower_band_1h=round(lower, 2),
            band_width_pct=round(band_width, 4),
            current_price=round(price, 2),
        )
