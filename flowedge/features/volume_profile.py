"""
Volume Profile（VP）成交量分布
核心信号：识别 POC（控制点）、价值区域、高/低成交量节点 → 支撑阻力判断。

做市商逻辑：
  - POC = 成交量最大的价格 = 市场共识的"公平价格"
  - HVN（高成交量节点）= 支撑/阻力位，价格倾向于在此盘整
  - LVN（低成交量节点）= 价格真空带，价格快速穿越
  - Value Area（70% 成交量覆盖）= 价格最可能停留的区间

实现：
  - 按价格分桶（tick_size 精度），累计每个价格层级的成交量
  - 滚动窗口（默认 1h），定期衰减旧数据
  - 实时计算 POC、VAH、VAL、价值区域占比
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class VolumeProfileSnapshot:
    """Volume Profile 特征快照"""
    poc_price: float            # Point of Control 价格（成交量最大的价位）
    poc_volume_usdt: float      # POC 价位的成交量
    vah_price: float            # Value Area High（价值区域上沿）
    val_price: float            # Value Area Low（价值区域下沿）
    value_area_pct: float       # 当前价格在价值区域内的位置 (0=VAL, 1=VAH, <0或>1=区外)
    price_vs_poc_pct: float     # 当前价格相对 POC 的偏离百分比
    in_value_area: bool         # 当前价格是否在价值区域内
    hvn_above: float            # 当前价格上方最近的 HVN 价格（阻力）
    hvn_below: float            # 当前价格下方最近的 HVN 价格（支撑）
    total_volume_usdt: float    # 窗口内总成交量
    bin_count: int              # 有效价格桶数量


class VolumeProfileCalculator:
    """
    Volume Profile 计算器。

    原理：将每笔成交按价格分桶，累计各价位的成交量。
    通过 POC 和 Value Area 判断支撑阻力。

    参数：
      - bin_size_pct: 分桶精度（占价格的百分比），默认 0.01%
      - value_area_pct: 价值区域覆盖比例，默认 70%
      - window_ms: 滚动窗口，默认 1 小时
      - hvn_threshold_pct: HVN 判定阈值（占总量百分比），默认 3%
    """

    def __init__(
        self,
        bin_size_pct: float = 0.01,
        value_area_pct: float = 0.70,
        window_ms: int = 60 * 60 * 1000,
        hvn_threshold_pct: float = 3.0,
    ):
        self._bin_size_pct = bin_size_pct
        self._value_area_pct = value_area_pct
        self._window_ms = window_ms
        self._hvn_threshold_pct = hvn_threshold_pct

        # 存储 (timestamp_ms, bin_key, volume_usdt) 用于窗口滚动
        self._trades: deque = deque()
        # 当前有效的成交量分布 {bin_key: total_volume_usdt}
        self._bins: defaultdict = defaultdict(float)
        self._total_volume: float = 0.0
        self._current_price: float = 0.0

    def _price_to_bin(self, price: float) -> float:
        """将价格映射到最近的桶中心"""
        if price <= 0:
            return 0.0
        bin_size = price * self._bin_size_pct / 100.0
        if bin_size <= 0:
            return price
        return round(round(price / bin_size) * bin_size, 8)

    def on_trade(self, price: float, qty_usdt: float, timestamp_ms: int) -> None:
        """收到一笔成交"""
        self._current_price = price
        bin_key = self._price_to_bin(price)

        self._trades.append((timestamp_ms, bin_key, qty_usdt))
        self._bins[bin_key] += qty_usdt
        self._total_volume += qty_usdt

        # 清理过期数据
        cutoff = timestamp_ms - self._window_ms
        while self._trades and self._trades[0][0] < cutoff:
            _, old_bin, old_vol = self._trades.popleft()
            self._bins[old_bin] -= old_vol
            self._total_volume -= old_vol
            if self._bins[old_bin] <= 0:
                del self._bins[old_bin]

    def _calc_value_area(self, poc_bin: float) -> tuple[float, float]:
        """
        从 POC 向两侧扩展，直到覆盖 value_area_pct（默认 70%）的成交量。
        返回 (VAL, VAH)
        """
        if not self._bins or self._total_volume <= 0:
            return 0.0, 0.0

        target_volume = self._total_volume * self._value_area_pct
        sorted_bins = sorted(self._bins.keys())

        if poc_bin not in self._bins:
            first = sorted_bins[0] if sorted_bins else 0.0
            last = sorted_bins[-1] if sorted_bins else 0.0
            return first, last

        poc_idx = sorted_bins.index(poc_bin)
        accumulated = self._bins[poc_bin]
        low_idx = poc_idx
        high_idx = poc_idx
        n = len(sorted_bins)

        while accumulated < target_volume and (low_idx > 0 or high_idx < n - 1):
            can_go_low = low_idx > 0
            can_go_high = high_idx < n - 1
            vol_below = self._bins.get(sorted_bins[low_idx - 1], 0) if can_go_low else 0
            vol_above = self._bins.get(sorted_bins[high_idx + 1], 0) if can_go_high else 0

            # 优先扩展成交量更大的方向
            expand_low = can_go_low and (not can_go_high or vol_below >= vol_above)
            if expand_low:
                low_idx -= 1
                accumulated += self._bins.get(sorted_bins[low_idx], 0)
            elif can_go_high:
                high_idx += 1
                accumulated += self._bins.get(sorted_bins[high_idx], 0)
            else:
                break

        return sorted_bins[low_idx], sorted_bins[high_idx]

    def _find_nearest_hvn(self, price: float, direction: str) -> float:
        """
        找到当前价格上方/下方最近的高成交量节点（HVN）。
        HVN 定义：成交量 >= 总量的 hvn_threshold_pct%
        """
        if not self._bins or self._total_volume <= 0:
            return 0.0

        threshold = self._total_volume * self._hvn_threshold_pct / 100.0
        current_bin = self._price_to_bin(price)
        candidates = sorted(self._bins.keys(), reverse=(direction != "above"))

        for b in candidates:
            is_target = (b > current_bin) if direction == "above" else (b < current_bin)
            if is_target and self._bins[b] >= threshold:
                return b

        return 0.0

    def snapshot(self) -> VolumeProfileSnapshot:
        """获取当前 Volume Profile 快照"""
        price = self._current_price

        if not self._bins or self._total_volume < 100:
            return VolumeProfileSnapshot(
                poc_price=0.0, poc_volume_usdt=0.0,
                vah_price=0.0, val_price=0.0,
                value_area_pct=0.0, price_vs_poc_pct=0.0,
                in_value_area=False,
                hvn_above=0.0, hvn_below=0.0,
                total_volume_usdt=0.0, bin_count=0,
            )

        # POC: 成交量最大的价格桶
        poc_bin = max(self._bins, key=self._bins.get)
        poc_volume = self._bins[poc_bin]

        # Value Area
        val_price, vah_price = self._calc_value_area(poc_bin)

        # 当前价格在价值区域中的位置
        va_range = vah_price - val_price
        if va_range > 0:
            va_position = (price - val_price) / va_range
        else:
            va_position = 0.5

        in_va = val_price <= price <= vah_price

        # 偏离 POC
        price_vs_poc = ((price - poc_bin) / poc_bin * 100) if poc_bin > 0 else 0.0

        # 最近的 HVN
        hvn_above = self._find_nearest_hvn(price, "above")
        hvn_below = self._find_nearest_hvn(price, "below")

        return VolumeProfileSnapshot(
            poc_price=round(poc_bin, 2),
            poc_volume_usdt=round(poc_volume, 2),
            vah_price=round(vah_price, 2),
            val_price=round(val_price, 2),
            value_area_pct=round(va_position, 4),
            price_vs_poc_pct=round(price_vs_poc, 4),
            in_value_area=in_va,
            hvn_above=round(hvn_above, 2),
            hvn_below=round(hvn_below, 2),
            total_volume_usdt=round(self._total_volume, 2),
            bin_count=len(self._bins),
        )
