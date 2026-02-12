"""
冰山单 / 单一 ID 推量检测器（Iceberg Order Detector）

核心思路：币安 aggTrade 不暴露 trader ID，但可通过统计特征推断"同一交易者"的连续操作：
  1. 同价位连续小单（Iceberg）：短时间内同方向、同价位（或极近价位）出现多笔小额成交
  2. 固定间隔模式（Algo）：成交时间间隔呈现规律性（标准差极低）
  3. 固定数量模式（Clip）：连续多笔成交量高度一致

检测结果用于：
  - 识别做市商/算法交易者的大额拆单行为
  - 估算"真实"大单规模（将碎片还原为整体）
  - 为 SignalScorer 提供"隐藏大单"因子
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import math


@dataclass
class IcebergCluster:
    """一组被识别为同一交易者的连续成交"""
    start_ms: int           # 首笔时间
    end_ms: int             # 末笔时间
    price_avg: float        # 加权均价
    total_qty_usdt: float   # 总成交额
    trade_count: int        # 笔数
    is_taker_buy: bool      # 方向
    pattern: str            # 模式: "iceberg" | "algo" | "clip"
    confidence: float       # 置信度 0-1


@dataclass
class IcebergSnapshot:
    """冰山单检测快照"""
    active_clusters: list[IcebergCluster]   # 当前活跃的聚类
    recent_clusters: list[IcebergCluster]   # 最近 60s 完成的聚类
    buy_hidden_usdt: float    # 60s 内隐藏买单总额
    sell_hidden_usdt: float   # 60s 内隐藏卖单总额
    net_hidden_usdt: float    # 净隐藏量（买-卖）
    cluster_count_60s: int    # 60s 内聚类数


class IcebergDetector:
    """
    基于 aggTrade 流的冰山单检测器。

    算法：
    1. 维护一个滑动窗口（默认 5s），收集同方向的连续成交
    2. 当窗口内成交满足以下任一条件时，标记为冰山聚类：
       a) 同价位（±0.05%）连续 ≥5 笔，单笔 < 大单阈值的 20%
       b) 时间间隔标准差 < 50ms（算法交易特征）
       c) 成交量变异系数 < 0.1（固定数量拆单）
    3. 聚类结束后计算总量，作为"隐藏大单"信号
    """

    def __init__(
        self,
        window_ms: int = 5000,          # 聚类窗口
        min_trades: int = 5,            # 最少笔数
        price_tolerance_pct: float = 0.05,  # 价格容差 %
        interval_std_ms: float = 50.0,  # 间隔标准差阈值
        qty_cv_threshold: float = 0.1,  # 数量变异系数阈值
        large_threshold_usdt: float = 50000,  # 大单阈值（用于判断"小单"）
    ):
        self._window_ms = window_ms
        self._min_trades = min_trades
        self._price_tol = price_tolerance_pct / 100.0
        self._interval_std = interval_std_ms
        self._qty_cv = qty_cv_threshold
        self._small_threshold = large_threshold_usdt * 0.2  # 小于大单 20% 视为拆单

        # 当前正在积累的窗口（按方向分）
        self._buy_window: deque = deque()   # [(ts_ms, price, qty_usdt), ...]
        self._sell_window: deque = deque()

        # 已完成的聚类（60s 保留）
        self._clusters: deque[IcebergCluster] = deque()

    def on_trade(
        self, price: float, qty_usdt: float, is_taker_buy: bool, timestamp_ms: int
    ) -> Optional[IcebergCluster]:
        """
        处理一笔 aggTrade，返回刚完成的聚类（如有）。
        """
        window = self._buy_window if is_taker_buy else self._sell_window
        other_window = self._sell_window if is_taker_buy else self._buy_window

        # 清理超时的对向窗口 → 检测是否形成聚类
        cluster_from_other = self._flush_window(other_window, not is_taker_buy, timestamp_ms)

        # 清理当前窗口中超时的数据
        cutoff = timestamp_ms - self._window_ms
        while window and window[0][0] < cutoff:
            window.popleft()

        # 加入当前成交
        window.append((timestamp_ms, price, qty_usdt))

        # 清理过期聚类
        cluster_cutoff = timestamp_ms - 60000
        while self._clusters and self._clusters[0].end_ms < cluster_cutoff:
            self._clusters.popleft()

        return cluster_from_other

    def _flush_window(
        self, window: deque, is_buy: bool, now_ms: int
    ) -> Optional[IcebergCluster]:
        """检查窗口是否形成聚类，如果最后一笔超时则结算"""
        if len(window) < self._min_trades:
            return None

        # 检查最后一笔是否超时
        if window and (now_ms - window[-1][0]) < 500:
            return None  # 还在活跃中，不结算

        cluster = self._detect_cluster(list(window), is_buy)
        if cluster:
            self._clusters.append(cluster)
            window.clear()
            return cluster

        # 没检测到模式，清理旧数据
        cutoff = now_ms - self._window_ms
        while window and window[0][0] < cutoff:
            window.popleft()
        return None

    def _detect_cluster(
        self, trades: list[tuple], is_buy: bool
    ) -> Optional[IcebergCluster]:
        """对一组成交检测是否符合冰山单模式"""
        if len(trades) < self._min_trades:
            return None

        timestamps = [t[0] for t in trades]
        prices = [t[1] for t in trades]
        quantities = [t[2] for t in trades]

        total_usdt = sum(quantities)
        avg_price = sum(p * q for p, q in zip(prices, quantities)) / total_usdt if total_usdt > 0 else 0

        patterns_found = []
        confidence = 0.0

        # ── 模式 1: 同价位连续小单（Iceberg） ──
        if all(q < self._small_threshold for q in quantities):
            price_range = (max(prices) - min(prices)) / avg_price if avg_price > 0 else 1
            if price_range < self._price_tol:
                patterns_found.append("iceberg")
                # 置信度：笔数越多、价格越集中 → 越确信
                confidence = max(confidence, min(0.5 + len(trades) * 0.05, 0.95))

        # ── 模式 2: 固定间隔（Algo） ──
        if len(timestamps) >= 3:
            intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
            mean_interval = sum(intervals) / len(intervals) if intervals else 0
            if mean_interval > 0:
                std_interval = math.sqrt(
                    sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
                )
                if std_interval < self._interval_std and mean_interval < 2000:
                    patterns_found.append("algo")
                    confidence = max(confidence, min(0.6 + (1 - std_interval / self._interval_std) * 0.3, 0.95))

        # ── 模式 3: 固定数量（Clip） ──
        if len(quantities) >= 3:
            mean_qty = sum(quantities) / len(quantities)
            if mean_qty > 0:
                cv = math.sqrt(
                    sum((q - mean_qty) ** 2 for q in quantities) / len(quantities)
                ) / mean_qty
                if cv < self._qty_cv:
                    patterns_found.append("clip")
                    confidence = max(confidence, min(0.7 + (1 - cv / self._qty_cv) * 0.2, 0.95))

        if not patterns_found:
            return None

        # 选择最高置信度的模式
        pattern = patterns_found[0]
        if "algo" in patterns_found:
            pattern = "algo"
        if "clip" in patterns_found and confidence >= 0.7:
            pattern = "clip"

        return IcebergCluster(
            start_ms=timestamps[0],
            end_ms=timestamps[-1],
            price_avg=round(avg_price, 2),
            total_qty_usdt=round(total_usdt, 2),
            trade_count=len(trades),
            is_taker_buy=is_buy,
            pattern=pattern,
            confidence=round(confidence, 3),
        )

    def snapshot(self) -> IcebergSnapshot:
        """返回当前冰山单检测快照"""
        now_ms = int(time.time() * 1000)

        # 检查活跃窗口中是否有潜在聚类
        active = []
        for window, is_buy in [(self._buy_window, True), (self._sell_window, False)]:
            if len(window) >= self._min_trades:
                cluster = self._detect_cluster(list(window), is_buy)
                if cluster:
                    active.append(cluster)

        # 60s 内完成的聚类
        recent = list(self._clusters)

        buy_hidden = sum(c.total_qty_usdt for c in recent if c.is_taker_buy)
        sell_hidden = sum(c.total_qty_usdt for c in recent if not c.is_taker_buy)

        return IcebergSnapshot(
            active_clusters=active,
            recent_clusters=recent,
            buy_hidden_usdt=round(buy_hidden, 2),
            sell_hidden_usdt=round(sell_hidden, 2),
            net_hidden_usdt=round(buy_hidden - sell_hidden, 2),
            cluster_count_60s=len(recent),
        )
