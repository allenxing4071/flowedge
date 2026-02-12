"""
Tape 逐笔成交缓冲区（Time & Sales）

维护最近 N 笔 aggTrade 的环形缓冲区，供前端实时渲染 Tape 面板。
同时计算实时统计：
  - 买卖笔数/金额比
  - 大单占比
  - 成交速率（笔/秒）
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class TapeTrade:
    """单笔成交记录"""
    timestamp_ms: int
    price: float
    qty: float          # 币数量
    qty_usdt: float     # USDT 金额
    is_taker_buy: bool
    is_large: bool      # 是否大单


@dataclass
class TapeStats:
    """Tape 实时统计"""
    buy_count: int = 0
    sell_count: int = 0
    buy_usdt: float = 0.0
    sell_usdt: float = 0.0
    large_count: int = 0
    large_usdt: float = 0.0
    trades_per_sec: float = 0.0
    avg_trade_usdt: float = 0.0


@dataclass
class TapeSnapshot:
    """Tape 快照"""
    trades: list[dict]      # 最近 N 笔成交
    stats_10s: dict         # 10 秒统计
    stats_60s: dict         # 60 秒统计


class TapeBuffer:
    """
    Tape 逐笔成交缓冲区。

    维护两个窗口：
    - 展示窗口（最近 200 笔）：供前端渲染
    - 统计窗口（最近 60s）：计算实时统计
    """

    def __init__(
        self,
        display_size: int = 200,
        large_threshold_usdt: float = 50000,
    ):
        self._display_size = display_size
        self._large_threshold = large_threshold_usdt

        # 展示缓冲区（最近 N 笔）
        self._buffer: deque[TapeTrade] = deque(maxlen=display_size)
        # 统计缓冲区（最近 60s）
        self._stats_buffer: deque[TapeTrade] = deque()

    def on_trade(
        self,
        price: float,
        qty: float,
        qty_usdt: float,
        is_taker_buy: bool,
        timestamp_ms: int,
    ) -> None:
        """处理一笔 aggTrade"""
        trade = TapeTrade(
            timestamp_ms=timestamp_ms,
            price=price,
            qty=qty,
            qty_usdt=qty_usdt,
            is_taker_buy=is_taker_buy,
            is_large=qty_usdt >= self._large_threshold,
        )
        self._buffer.append(trade)
        self._stats_buffer.append(trade)

        # 清理超过 60s 的统计数据
        cutoff = timestamp_ms - 60000
        while self._stats_buffer and self._stats_buffer[0].timestamp_ms < cutoff:
            self._stats_buffer.popleft()

    def _calc_stats(self, window_ms: int) -> TapeStats:
        """计算指定时间窗口的统计"""
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - window_ms

        stats = TapeStats()
        count = 0
        for t in self._stats_buffer:
            if t.timestamp_ms < cutoff:
                continue
            count += 1
            if t.is_taker_buy:
                stats.buy_count += 1
                stats.buy_usdt += t.qty_usdt
            else:
                stats.sell_count += 1
                stats.sell_usdt += t.qty_usdt
            if t.is_large:
                stats.large_count += 1
                stats.large_usdt += t.qty_usdt

        total = stats.buy_count + stats.sell_count
        window_s = window_ms / 1000
        stats.trades_per_sec = round(total / window_s, 1) if window_s > 0 else 0
        stats.avg_trade_usdt = round(
            (stats.buy_usdt + stats.sell_usdt) / total, 2
        ) if total > 0 else 0

        stats.buy_usdt = round(stats.buy_usdt, 2)
        stats.sell_usdt = round(stats.sell_usdt, 2)
        stats.large_usdt = round(stats.large_usdt, 2)

        return stats

    def _stats_to_dict(self, stats: TapeStats) -> dict:
        return {
            "buy_count": stats.buy_count,
            "sell_count": stats.sell_count,
            "buy_usdt": stats.buy_usdt,
            "sell_usdt": stats.sell_usdt,
            "large_count": stats.large_count,
            "large_usdt": stats.large_usdt,
            "trades_per_sec": stats.trades_per_sec,
            "avg_trade_usdt": stats.avg_trade_usdt,
        }

    def snapshot(self) -> TapeSnapshot:
        """返回 Tape 快照"""
        # 最近 N 笔成交（从新到旧）
        trades = []
        for t in reversed(self._buffer):
            trades.append({
                "ts": t.timestamp_ms,
                "p": t.price,
                "q": round(t.qty, 6),
                "v": round(t.qty_usdt, 2),
                "s": "B" if t.is_taker_buy else "S",
                "lg": t.is_large,
            })

        return TapeSnapshot(
            trades=trades,
            stats_10s=self._stats_to_dict(self._calc_stats(10000)),
            stats_60s=self._stats_to_dict(self._calc_stats(60000)),
        )
