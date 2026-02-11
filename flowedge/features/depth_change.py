"""
深度变化率 + 做市商假墙检测（Depth Change & Wall Detection）
追踪订单簿各档挂单量变化，检测做市商假墙和流动性抽离。
核心信号：
- 大挂单出现后迅速撤单 → 假墙（做市商诱导）
- 某一侧流动性突然大幅减少 → 即将发生大幅波动
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class WallEvent:
    """假墙事件"""
    timestamp_ms: int
    side: str            # "bid" or "ask"
    price: float
    appeared_qty_usdt: float  # 出现时的挂单额
    disappeared_ms: int       # 存活毫秒数
    event_type: str      # "fake_wall" / "liquidity_sweep"


@dataclass
class DepthChangeSnapshot:
    """深度变化特征快照"""
    bid_change_rate: float    # 买方深度变化速率（USDT/s）
    ask_change_rate: float    # 卖方深度变化速率（USDT/s）
    bid_depth_usdt: float     # 当前买方总深度（前 10 档 USDT）
    ask_depth_usdt: float     # 当前卖方总深度（前 10 档 USDT）
    depth_imbalance: float    # 深度不平衡 (bid-ask)/(bid+ask) * 100
    wall_events_30s: int      # 30s 内假墙事件数
    recent_walls: list[WallEvent]  # 最近的假墙事件（最多 5 个）


class DepthChangeDetector:
    """
    深度变化检测器。

    追踪：
    1. 买/卖方总深度的变化速率
    2. 大挂单的出现与消失（假墙检测）
    """

    def __init__(
        self,
        wall_threshold_usdt: float = 200_000,
        wall_max_lifetime_ms: int = 5_000,
        levels: int = 10,
    ):
        self._wall_threshold = wall_threshold_usdt
        self._wall_max_lifetime = wall_max_lifetime_ms
        self._levels = levels

        # 深度历史（用于计算变化率）
        self._prev_bid_depth = 0.0
        self._prev_ask_depth = 0.0
        self._prev_ts = 0

        # 大挂单追踪：{(side, price): (qty_usdt, appear_ts)}
        self._tracked_walls: dict[tuple[str, float], tuple[float, int]] = {}

        # 假墙事件
        self._wall_events: deque[WallEvent] = deque()

        # 变化率
        self._bid_change_rate = 0.0
        self._ask_change_rate = 0.0
        self._cur_bid_depth = 0.0
        self._cur_ask_depth = 0.0

    def on_depth_update(
        self,
        bids: list[list[float]],
        asks: list[list[float]],
        timestamp_ms: int,
    ) -> list[WallEvent]:
        """
        收到订单簿更新，返回新检测到的假墙事件列表。
        """
        new_events: list[WallEvent] = []

        # 计算前 N 档总深度（USDT）
        bid_depth = sum(p * q for p, q in bids[:self._levels])
        ask_depth = sum(p * q for p, q in asks[:self._levels])

        # 变化率
        if self._prev_ts > 0:
            dt_s = max((timestamp_ms - self._prev_ts) / 1000, 0.001)
            self._bid_change_rate = (bid_depth - self._prev_bid_depth) / dt_s
            self._ask_change_rate = (ask_depth - self._prev_ask_depth) / dt_s

        self._prev_bid_depth = bid_depth
        self._prev_ask_depth = ask_depth
        self._prev_ts = timestamp_ms
        self._cur_bid_depth = bid_depth
        self._cur_ask_depth = ask_depth

        # 大挂单追踪
        current_walls: set[tuple[str, float]] = set()

        for price, qty in bids[:self._levels]:
            qty_usdt = price * qty
            key = ("bid", price)
            if qty_usdt >= self._wall_threshold:
                current_walls.add(key)
                if key not in self._tracked_walls:
                    self._tracked_walls[key] = (qty_usdt, timestamp_ms)

        for price, qty in asks[:self._levels]:
            qty_usdt = price * qty
            key = ("ask", price)
            if qty_usdt >= self._wall_threshold:
                current_walls.add(key)
                if key not in self._tracked_walls:
                    self._tracked_walls[key] = (qty_usdt, timestamp_ms)

        # 检查消失的大挂单（假墙检测）
        disappeared = set(self._tracked_walls.keys()) - current_walls
        for key in disappeared:
            qty_usdt, appear_ts = self._tracked_walls.pop(key)
            lifetime = timestamp_ms - appear_ts
            if lifetime <= self._wall_max_lifetime:
                event = WallEvent(
                    timestamp_ms=timestamp_ms,
                    side=key[0],
                    price=key[1],
                    appeared_qty_usdt=qty_usdt,
                    disappeared_ms=lifetime,
                    event_type="fake_wall",
                )
                self._wall_events.append(event)
                new_events.append(event)

        # 清理过期事件
        cutoff = timestamp_ms - 30_000
        while self._wall_events and self._wall_events[0].timestamp_ms < cutoff:
            self._wall_events.popleft()

        return new_events

    def snapshot(self) -> DepthChangeSnapshot:
        """获取当前深度变化快照"""
        total = self._cur_bid_depth + self._cur_ask_depth
        imbalance = ((self._cur_bid_depth - self._cur_ask_depth) / total * 100) if total > 0 else 0

        return DepthChangeSnapshot(
            bid_change_rate=round(self._bid_change_rate, 2),
            ask_change_rate=round(self._ask_change_rate, 2),
            bid_depth_usdt=round(self._cur_bid_depth, 2),
            ask_depth_usdt=round(self._cur_ask_depth, 2),
            depth_imbalance=round(imbalance, 2),
            wall_events_30s=len(self._wall_events),
            recent_walls=list(self._wall_events)[-5:],
        )
