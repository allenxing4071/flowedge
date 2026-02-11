"""
清算特征计算器
从 forceOrder WebSocket 实时聚合清算事件，检测清算级联。
核心信号：短时间内同向大量清算 → 清算级联，价格将加速运动。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from ..feeds.force_order import LiquidationEvent


@dataclass
class LiquidationSnapshot:
    """清算特征快照"""
    # 实时（来自 forceOrder WS）
    count_1m: int               # 1 分钟内清算笔数
    count_5m: int               # 5 分钟内清算笔数
    long_liq_1m_usdt: float     # 1 分钟内多头清算金额
    short_liq_1m_usdt: float    # 1 分钟内空头清算金额
    long_liq_5m_usdt: float     # 5 分钟内多头清算金额
    short_liq_5m_usdt: float    # 5 分钟内空头清算金额
    net_liq_1m: float           # 1 分钟净清算（正=多头被清算为主）
    max_single_usdt: float      # 窗口内最大单笔清算金额
    cascade_level: str          # "none" / "minor" / "major" / "extreme"
    signal: str                 # 综合信号文本
    # Coinglass 补充（如果有）
    coinglass_liq_long_1h: float
    coinglass_liq_short_1h: float


class LiquidationTracker:
    """
    清算事件追踪器。

    从 forceOrder WS 实时接收清算事件，按时间窗口聚合统计。
    检测清算级联：
    - minor: 1 分钟内清算 > $1M
    - major: 1 分钟内清算 > $5M
    - extreme: 1 分钟内清算 > $20M
    """

    def __init__(self, window_5m: int = 300_000):
        self._window_5m = window_5m
        self._events: deque[LiquidationEvent] = deque()
        self._max_single = 0.0
        # Coinglass 补充数据
        self._cg_long_1h = 0.0
        self._cg_short_1h = 0.0

    def on_liquidation(self, event: LiquidationEvent) -> None:
        """收到一个清算事件"""
        self._events.append(event)
        if event.qty_usdt > self._max_single:
            self._max_single = event.qty_usdt
        self._prune(event.timestamp_ms)

    def update_coinglass(self, long_1h: float, short_1h: float) -> None:
        """更新 Coinglass 清算数据"""
        self._cg_long_1h = long_1h
        self._cg_short_1h = short_1h

    def _prune(self, now_ms: int) -> None:
        """清理 5 分钟窗口外的事件"""
        cutoff = now_ms - self._window_5m
        while self._events and self._events[0].timestamp_ms < cutoff:
            self._events.popleft()

    def snapshot(self) -> LiquidationSnapshot:
        """获取当前清算快照"""
        now_ms = int(time.time() * 1000)
        self._prune(now_ms)

        since_1m = now_ms - 60_000
        count_1m = 0
        count_5m = len(self._events)
        long_1m = 0.0
        short_1m = 0.0
        long_5m = 0.0
        short_5m = 0.0

        for e in self._events:
            if e.is_long_liq:
                long_5m += e.qty_usdt
                if e.timestamp_ms >= since_1m:
                    long_1m += e.qty_usdt
                    count_1m += 1
            else:
                short_5m += e.qty_usdt
                if e.timestamp_ms >= since_1m:
                    short_1m += e.qty_usdt
                    count_1m += 1

        total_1m = long_1m + short_1m
        net_1m = long_1m - short_1m  # 正 = 多头被清算为主（价格在跌）

        # 级联等级
        if total_1m >= 20_000_000:
            cascade = "extreme"
        elif total_1m >= 5_000_000:
            cascade = "major"
        elif total_1m >= 1_000_000:
            cascade = "minor"
        else:
            cascade = "none"

        # 信号
        if cascade == "extreme":
            direction = "多头" if net_1m > 0 else "空头"
            signal = f"极端清算级联！1m清算${total_1m/1e6:.1f}M，{direction}被大规模清算"
        elif cascade == "major":
            signal = f"严重清算 1m=${total_1m/1e6:.1f}M"
        elif cascade == "minor":
            signal = f"清算加剧 1m=${total_1m/1e3:.0f}K"
        else:
            signal = "清算水平正常"

        return LiquidationSnapshot(
            count_1m=count_1m,
            count_5m=count_5m,
            long_liq_1m_usdt=round(long_1m, 2),
            short_liq_1m_usdt=round(short_1m, 2),
            long_liq_5m_usdt=round(long_5m, 2),
            short_liq_5m_usdt=round(short_5m, 2),
            net_liq_1m=round(net_1m, 2),
            max_single_usdt=round(self._max_single, 2),
            cascade_level=cascade,
            signal=signal,
            coinglass_liq_long_1h=round(self._cg_long_1h, 2),
            coinglass_liq_short_1h=round(self._cg_short_1h, 2),
        )
