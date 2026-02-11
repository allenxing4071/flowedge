"""
吸收检测器（Absorption Detector）
核心信号：大量成交但价格不动 → 大资金在暗中接盘/出货。

做市商逻辑：
  - 买方吸收：大量卖单成交，但价格不跌 → 有大买家在该价位持续接盘 → 看涨
  - 卖方吸收：大量买单成交，但价格不涨 → 有大卖家在该价位持续出货 → 看跌
  - 吸收强度越大，大资金意图越明确

实现：
  - 滚动窗口（30s / 1m）统计成交量和价格变化
  - 吸收比 = 成交量 / |价格变化|（量价背离度）
  - 方向判断：根据 taker 买卖比例判断是买方吸收还是卖方吸收
  - 吸收事件：当吸收比超过阈值时触发
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class AbsorptionSnapshot:
    """吸收检测特征快照"""
    buy_absorption_30s: float    # 30 秒内买方吸收强度 [0, 1]
    sell_absorption_30s: float   # 30 秒内卖方吸收强度 [0, 1]
    net_absorption_30s: float    # 净吸收方向 [-1, 1]（正=买方吸收/看涨，负=卖方吸收/看跌）
    absorption_ratio_1m: float   # 1 分钟量价背离度（成交量/价格变化）
    volume_1m_usdt: float        # 1 分钟总成交量
    price_change_1m_pct: float   # 1 分钟价格变化百分比
    is_absorbing: bool           # 当前是否处于吸收状态
    absorption_side: str         # "buy" / "sell" / "none"
    event_count_5m: int          # 5 分钟内吸收事件次数


class AbsorptionDetector:
    """
    吸收检测器。

    核心算法：
    1. 将时间划分为微窗口（默认 5 秒）
    2. 每个微窗口统计：成交量、买量、卖量、价格变化
    3. 如果成交量高但价格变化小 → 检测为吸收
    4. 根据买卖比例判断吸收方向

    参数：
      - micro_window_ms: 微窗口大小，默认 5 秒
      - absorption_threshold: 吸收判定阈值（量价比），默认 50000
        含义：每 0.01% 价格变化对应的 USDT 成交量
      - min_volume_usdt: 最低成交量阈值，默认 10000 USDT
    """

    def __init__(
        self,
        micro_window_ms: int = 5_000,
        absorption_threshold: float = 50_000,
        min_volume_usdt: float = 10_000,
    ):
        self._micro_window_ms = micro_window_ms
        self._absorption_threshold = absorption_threshold
        self._min_volume_usdt = min_volume_usdt

        # 逐笔成交缓存：(timestamp_ms, price, qty_usdt, is_taker_buy)
        self._trades: deque = deque()
        # 吸收事件历史：(timestamp_ms, side, strength)
        self._events: deque = deque()

        # 最大保留 5 分钟数据
        self._max_trade_age_ms = 60 * 1000       # 成交保留 1 分钟
        self._max_event_age_ms = 5 * 60 * 1000   # 事件保留 5 分钟

        self._current_price: float = 0.0

    def on_trade(self, price: float, qty_usdt: float, is_taker_buy: bool, timestamp_ms: int) -> None:
        """收到一笔成交"""
        self._current_price = price
        self._trades.append((timestamp_ms, price, qty_usdt, is_taker_buy))

        # 清理过期成交
        cutoff = timestamp_ms - self._max_trade_age_ms
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()

        # 清理过期事件
        event_cutoff = timestamp_ms - self._max_event_age_ms
        while self._events and self._events[0][0] < event_cutoff:
            self._events.popleft()

    def _calc_absorption(self, since_ms: int) -> tuple[float, float, float, float, float]:
        """
        计算指定时间窗口内的吸收指标。
        返回 (buy_absorption, sell_absorption, total_volume, price_change_pct, absorption_ratio)
        """
        if not self._trades:
            return 0.0, 0.0, 0.0, 0.0, 0.0

        buy_vol = 0.0
        sell_vol = 0.0
        first_price = 0.0
        last_price = 0.0
        total_vol = 0.0

        for ts, price, vol, is_buy in self._trades:
            if ts < since_ms:
                continue
            if first_price == 0.0:
                first_price = price
            last_price = price
            total_vol += vol
            if is_buy:
                buy_vol += vol
            else:
                sell_vol += vol

        if first_price <= 0 or total_vol < self._min_volume_usdt:
            return 0.0, 0.0, total_vol, 0.0, 0.0

        # 价格变化百分比
        price_change_pct = abs((last_price - first_price) / first_price * 100)

        # 量价比（吸收比）：每 0.01% 价格变化对应多少 USDT 成交量
        # 价格变化越小、成交量越大 → 吸收越强
        if price_change_pct < 0.001:
            # 价格几乎不变但有成交 → 极强吸收
            absorption_ratio = total_vol * 100
        else:
            absorption_ratio = total_vol / (price_change_pct * 100)

        # 判断吸收方向和强度
        # 买方吸收：卖单大量成交但价格不跌 → 有人在接盘
        # 卖方吸收：买单大量成交但价格不涨 → 有人在出货
        buy_absorption = 0.0
        sell_absorption = 0.0

        if absorption_ratio >= self._absorption_threshold:
            # 确实在吸收
            # 看卖方成交占比：卖方成交多但价格不跌 = 买方吸收
            sell_ratio = sell_vol / total_vol if total_vol > 0 else 0.5
            buy_ratio = buy_vol / total_vol if total_vol > 0 else 0.5

            # 归一化吸收强度到 [0, 1]
            raw_strength = min(absorption_ratio / (self._absorption_threshold * 5), 1.0)

            if sell_ratio > 0.55:
                # 卖方成交占多数但价格不跌 → 买方吸收（看涨）
                buy_absorption = raw_strength * (sell_ratio - 0.5) * 2
            elif buy_ratio > 0.55:
                # 买方成交占多数但价格不涨 → 卖方吸收（看跌）
                sell_absorption = raw_strength * (buy_ratio - 0.5) * 2

        # 加上价格变化方向修正
        signed_change = (last_price - first_price) / first_price * 100 if first_price > 0 else 0

        return buy_absorption, sell_absorption, total_vol, signed_change, absorption_ratio

    def snapshot(self) -> AbsorptionSnapshot:
        """获取当前吸收检测快照"""
        now_ms = int(time.time() * 1000)

        # 30 秒吸收
        buy_abs_30s, sell_abs_30s, _, _, _ = self._calc_absorption(now_ms - 30_000)

        # 1 分钟吸收
        buy_abs_1m, sell_abs_1m, vol_1m, price_chg_1m, ratio_1m = self._calc_absorption(
            now_ms - 60_000
        )

        # 净吸收方向
        net = buy_abs_30s - sell_abs_30s  # 正 = 买方吸收（看涨），负 = 卖方吸收（看跌）

        # 是否处于吸收状态
        is_absorbing = max(buy_abs_30s, sell_abs_30s) > 0.2

        # 吸收方向
        if buy_abs_30s > 0.2 and buy_abs_30s > sell_abs_30s:
            side = "buy"
        elif sell_abs_30s > 0.2 and sell_abs_30s > buy_abs_30s:
            side = "sell"
        else:
            side = "none"

        # 记录吸收事件
        if is_absorbing and (
            not self._events or now_ms - self._events[-1][0] >= self._micro_window_ms
        ):
            self._events.append((now_ms, side, max(buy_abs_30s, sell_abs_30s)))

        # 5 分钟内事件数
        event_cutoff = now_ms - 5 * 60 * 1000
        event_count = sum(1 for e in self._events if e[0] >= event_cutoff)

        return AbsorptionSnapshot(
            buy_absorption_30s=round(buy_abs_30s, 4),
            sell_absorption_30s=round(sell_abs_30s, 4),
            net_absorption_30s=round(net, 4),
            absorption_ratio_1m=round(ratio_1m, 2),
            volume_1m_usdt=round(vol_1m, 2),
            price_change_1m_pct=round(price_chg_1m, 4),
            is_absorbing=is_absorbing,
            absorption_side=side,
            event_count_5m=event_count,
        )
