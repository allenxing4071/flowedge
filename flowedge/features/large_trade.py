"""
大单检测（Large Trade Detector）
追踪单笔 > 阈值的成交，统计 30s 窗口内大单净方向。
核心信号：连续同向大单 → 机构/大户正在建仓/出货。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class LargeTradeEvent:
    """单个大单事件"""
    timestamp_ms: int
    price: float
    qty_usdt: float
    is_taker_buy: bool


@dataclass
class LargeTradeSnapshot:
    """大单特征快照"""
    count_30s: int          # 30s 内大单笔数
    buy_count_30s: int      # 30s 内大单买入笔数
    sell_count_30s: int     # 30s 内大单卖出笔数
    net_flow_30s: float     # 30s 内大单净流入（正=买入为主）
    buy_total_30s: float    # 30s 内大单买入总额
    sell_total_30s: float   # 30s 内大单卖出总额
    last_large: LargeTradeEvent | None  # 最近一笔大单


class LargeTradeDetector:
    """
    大单检测器。

    单笔 aggTrade 成交额 > threshold → 标记为大单。
    维护 30s 滑动窗口统计。
    """

    def __init__(self, threshold_usdt: float = 50_000, window_ms: int = 30_000):
        self._threshold = threshold_usdt
        self._window_ms = window_ms
        self._events: deque[LargeTradeEvent] = deque()

    def on_trade(
        self, price: float, qty_usdt: float, is_taker_buy: bool, timestamp_ms: int
    ) -> LargeTradeEvent | None:
        """
        检查是否为大单。是则记录并返回事件，否则返回 None。
        """
        self._prune(timestamp_ms)

        if qty_usdt < self._threshold:
            return None

        event = LargeTradeEvent(
            timestamp_ms=timestamp_ms,
            price=price,
            qty_usdt=qty_usdt,
            is_taker_buy=is_taker_buy,
        )
        self._events.append(event)
        return event

    def _prune(self, now_ms: int) -> None:
        """清理过期事件"""
        cutoff = now_ms - self._window_ms
        while self._events and self._events[0].timestamp_ms < cutoff:
            self._events.popleft()

    def snapshot(self) -> LargeTradeSnapshot:
        """获取当前大单快照"""
        now_ms = int(time.time() * 1000)
        self._prune(now_ms)

        buy_count = 0
        sell_count = 0
        buy_total = 0.0
        sell_total = 0.0

        for e in self._events:
            if e.is_taker_buy:
                buy_count += 1
                buy_total += e.qty_usdt
            else:
                sell_count += 1
                sell_total += e.qty_usdt

        return LargeTradeSnapshot(
            count_30s=len(self._events),
            buy_count_30s=buy_count,
            sell_count_30s=sell_count,
            net_flow_30s=round(buy_total - sell_total, 2),
            buy_total_30s=round(buy_total, 2),
            sell_total_30s=round(sell_total, 2),
            last_large=self._events[-1] if self._events else None,
        )
