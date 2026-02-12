"""
Footprint Chart 数据聚合器

将 aggTrade 逐笔成交按时间窗口（默认 1 分钟）和价格档位聚合，
生成 Footprint Chart 所需的买卖量分布数据。

每个价格档位记录：
  - buy_qty_usdt: 主动买入成交额
  - sell_qty_usdt: 主动卖出成交额
  - delta: buy - sell
  - trade_count: 成交笔数

前端可据此渲染专业级 Footprint Chart（类似 ATAS/Sierra Chart）。
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FootprintLevel:
    """单个价格档位的买卖量"""
    price: float
    buy_qty_usdt: float = 0.0
    sell_qty_usdt: float = 0.0
    delta: float = 0.0          # buy - sell
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0


@dataclass
class FootprintBar:
    """一根 Footprint K 线"""
    open_ms: int                # 开始时间
    close_ms: int               # 结束时间
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 999999999.0
    close_price: float = 0.0
    total_buy_usdt: float = 0.0
    total_sell_usdt: float = 0.0
    total_delta: float = 0.0
    trade_count: int = 0
    levels: dict[float, FootprintLevel] = field(default_factory=dict)
    # POC (Point of Control) — 成交量最大的价格
    poc_price: float = 0.0
    poc_volume: float = 0.0


@dataclass
class FootprintSnapshot:
    """Footprint 快照"""
    current_bar: Optional[dict] = None     # 当前正在构建的 bar
    recent_bars: list[dict] = field(default_factory=list)  # 最近 N 根完成的 bar
    tick_size: float = 0.0


class FootprintAggregator:
    """
    Footprint Chart 数据聚合器。

    将 aggTrade 按时间窗口分桶，每桶内按价格档位（tick_size 对齐）聚合买卖量。
    """

    def __init__(
        self,
        interval_ms: int = 60000,   # 1 分钟一根
        tick_size: float = 0.0,     # 价格档位大小（0=自动）
        max_bars: int = 30,         # 保留最近 N 根
    ):
        self._interval_ms = interval_ms
        self._tick_size = tick_size
        self._auto_tick = tick_size == 0.0
        self._max_bars = max_bars

        self._current_bar: Optional[FootprintBar] = None
        self._completed_bars: list[FootprintBar] = []

        # 用于自动计算 tick_size
        self._price_samples: list[float] = []

    def _get_tick_size(self, price: float) -> float:
        """自动计算合理的 tick_size（基于价格量级）"""
        if not self._auto_tick:
            return self._tick_size

        # 根据价格量级自动选择
        if price >= 10000:      # BTC 级别
            return 10.0
        elif price >= 1000:     # ETH 级别
            return 1.0
        elif price >= 100:
            return 0.1
        elif price >= 10:
            return 0.01
        elif price >= 1:
            return 0.001
        else:
            return 0.0001

    def _round_to_tick(self, price: float, tick: float) -> float:
        """将价格对齐到 tick_size"""
        if tick <= 0:
            return price
        return round(round(price / tick) * tick, 8)

    def _bar_start(self, timestamp_ms: int) -> int:
        """计算当前 bar 的起始时间"""
        return (timestamp_ms // self._interval_ms) * self._interval_ms

    def on_trade(
        self, price: float, qty_usdt: float, is_taker_buy: bool, timestamp_ms: int
    ) -> Optional[FootprintBar]:
        """
        处理一笔 aggTrade。
        如果当前 bar 已结束，返回完成的 bar。
        """
        bar_start = self._bar_start(timestamp_ms)
        completed = None

        # 检查是否需要切换到新 bar
        if self._current_bar is None or bar_start > self._current_bar.open_ms:
            if self._current_bar is not None:
                # 结算当前 bar
                self._finalize_bar(self._current_bar)
                completed = self._current_bar
                self._completed_bars.append(self._current_bar)
                if len(self._completed_bars) > self._max_bars:
                    self._completed_bars.pop(0)

            # 创建新 bar
            self._current_bar = FootprintBar(
                open_ms=bar_start,
                close_ms=bar_start + self._interval_ms,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
            )

        bar = self._current_bar
        tick = self._get_tick_size(price)
        level_price = self._round_to_tick(price, tick)

        # 更新 OHLC
        bar.high_price = max(bar.high_price, price)
        bar.low_price = min(bar.low_price, price)
        bar.close_price = price
        bar.trade_count += 1

        # 更新价格档位
        if level_price not in bar.levels:
            bar.levels[level_price] = FootprintLevel(price=level_price)

        level = bar.levels[level_price]
        level.trade_count += 1
        if is_taker_buy:
            level.buy_qty_usdt += qty_usdt
            level.buy_count += 1
            bar.total_buy_usdt += qty_usdt
        else:
            level.sell_qty_usdt += qty_usdt
            level.sell_count += 1
            bar.total_sell_usdt += qty_usdt

        level.delta = level.buy_qty_usdt - level.sell_qty_usdt
        bar.total_delta = bar.total_buy_usdt - bar.total_sell_usdt

        return completed

    def _finalize_bar(self, bar: FootprintBar) -> None:
        """结算 bar，计算 POC"""
        if not bar.levels:
            return
        # POC = 成交量最大的价格
        max_level = max(
            bar.levels.values(),
            key=lambda lv: lv.buy_qty_usdt + lv.sell_qty_usdt
        )
        bar.poc_price = max_level.price
        bar.poc_volume = max_level.buy_qty_usdt + max_level.sell_qty_usdt

    def _bar_to_dict(self, bar: FootprintBar) -> dict:
        """将 bar 转为可序列化的 dict"""
        levels_list = []
        for lv in sorted(bar.levels.values(), key=lambda x: -x.price):
            levels_list.append({
                "price": lv.price,
                "buy": round(lv.buy_qty_usdt, 2),
                "sell": round(lv.sell_qty_usdt, 2),
                "delta": round(lv.delta, 2),
                "count": lv.trade_count,
            })

        return {
            "open_ms": bar.open_ms,
            "close_ms": bar.close_ms,
            "open": bar.open_price,
            "high": bar.high_price,
            "low": bar.low_price,
            "close": bar.close_price,
            "buy_total": round(bar.total_buy_usdt, 2),
            "sell_total": round(bar.total_sell_usdt, 2),
            "delta": round(bar.total_delta, 2),
            "trades": bar.trade_count,
            "poc_price": bar.poc_price,
            "poc_volume": round(bar.poc_volume, 2),
            "levels": levels_list,
        }

    def snapshot(self) -> FootprintSnapshot:
        """返回 Footprint 快照"""
        current = None
        if self._current_bar:
            self._finalize_bar(self._current_bar)
            current = self._bar_to_dict(self._current_bar)

        recent = [self._bar_to_dict(b) for b in self._completed_bars[-self._max_bars:]]

        tick = self._tick_size
        if self._auto_tick and self._current_bar:
            tick = self._get_tick_size(self._current_bar.close_price)

        return FootprintSnapshot(
            current_bar=current,
            recent_bars=recent,
            tick_size=tick,
        )
