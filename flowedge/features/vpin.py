"""
VPIN（Volume-Synchronized Probability of Informed Trading）知情交易概率
基于 Easley et al. 方法，按固定成交额分桶，检测知情交易者活跃度。
核心信号：VPIN > 0.7 → 知情交易者活跃，市场即将出现大波动。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class VPINSnapshot:
    """VPIN 特征快照"""
    vpin: float               # VPIN 值 (0~1)
    current_bucket_fill: float  # 当前桶已填充比例
    buckets_filled: int       # 已完成的桶总数
    last_bucket_buy_ratio: float  # 最近一个桶的买方占比


class VPINCalculator:
    """
    VPIN 计算器。

    按固定 USDT 成交额（bucket_size）分桶：
    - 每桶内统计 taker_buy_volume / total_volume
    - VPIN = 最近 N 桶的 |buy_ratio - 0.5| 的平均值 * 2
    - VPIN ∈ [0, 1]，越接近 1 表示知情交易者越活跃

    相比时间窗口，volume bucket 更贴合市场实际活跃度。
    """

    def __init__(self, bucket_size: float = 100_000, num_buckets: int = 50):
        self._bucket_size = bucket_size
        self._num_buckets = num_buckets

        # 当前桶的累计
        self._cur_buy_vol = 0.0
        self._cur_total_vol = 0.0

        # 完成的桶：deque([buy_ratio, ...], maxlen=num_buckets)
        self._buckets: deque[float] = deque(maxlen=num_buckets)
        self._total_buckets = 0

    def on_trade(self, qty_usdt: float, is_taker_buy: bool) -> None:
        """收到一笔成交"""
        self._cur_total_vol += qty_usdt
        if is_taker_buy:
            self._cur_buy_vol += qty_usdt

        # 桶是否满了
        while self._cur_total_vol >= self._bucket_size:
            # 计算当前桶的 buy_ratio
            buy_ratio = self._cur_buy_vol / self._cur_total_vol if self._cur_total_vol > 0 else 0.5

            # 超出部分溢出到下一桶
            overflow = self._cur_total_vol - self._bucket_size
            overflow_buy = overflow * buy_ratio  # 按比例分配

            self._buckets.append(buy_ratio)
            self._total_buckets += 1

            # 重置当前桶，用溢出部分初始化
            self._cur_total_vol = overflow
            self._cur_buy_vol = overflow_buy

    def snapshot(self) -> VPINSnapshot:
        """获取当前 VPIN 快照"""
        if len(self._buckets) == 0:
            return VPINSnapshot(
                vpin=0.0,
                current_bucket_fill=self._cur_total_vol / self._bucket_size if self._bucket_size > 0 else 0,
                buckets_filled=0,
                last_bucket_buy_ratio=0.5,
            )

        # VPIN = mean(|buy_ratio - 0.5|) * 2，归一化到 [0, 1]
        imbalances = [abs(br - 0.5) for br in self._buckets]
        vpin = sum(imbalances) / len(imbalances) * 2

        return VPINSnapshot(
            vpin=round(min(vpin, 1.0), 4),
            current_bucket_fill=round(self._cur_total_vol / self._bucket_size, 4),
            buckets_filled=self._total_buckets,
            last_bucket_buy_ratio=round(self._buckets[-1], 4) if self._buckets else 0.5,
        )
