"""
趋势上下文特征计算器
从 K 线数据和实时 kline WS 提取趋势方向，为微观结构信号提供宏观上下文。
核心价值：微观信号 + 趋势方向一致 → 成功率大幅提升。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List

from ..feeds.kline import KlineUpdate


@dataclass
class TrendSnapshot:
    """趋势上下文特征快照"""
    # 实时 1m K 线
    current_1m_open: float
    current_1m_high: float
    current_1m_low: float
    current_1m_close: float
    current_1m_volume_usdt: float
    current_1m_taker_buy_pct: float   # 主动买入占比 %

    # 多周期趋势方向
    trend_1m: str     # "up" / "down" / "flat" — 最近 5 根 1m K 线
    trend_5m: str     # 最近 5 根 5m K 线
    trend_15m: str    # 最近 5 根 15m K 线
    trend_1h: str     # 最近 5 根 1h K 线
    trend_4h: str     # 最近 5 根 4h K 线

    # 趋势一致性（多周期一致 = 强趋势）
    trend_alignment: str   # "strong_up" / "up" / "mixed" / "down" / "strong_down"
    alignment_score: int   # -5 到 +5（全部看空 = -5，全部看多 = +5）

    # 成交量信号
    volume_trend: str  # "increasing" / "decreasing" / "stable"

    # 综合信号
    signal: str


class TrendTracker:
    """
    趋势上下文追踪器。
    从 BinanceRestCollector 的 K 线数据 + kline WS 实时数据生成趋势判断。
    """

    def __init__(self):
        # 实时 1m K 线（WS 推送）
        self._current_kline: KlineUpdate = None
        # 已完成的 1m K 线缓存（用于趋势判断）
        self._closed_1m: deque[KlineUpdate] = deque(maxlen=30)
        # REST K 线数据（5 档）
        self._klines: Dict[str, list] = {}

    def on_kline_update(self, update: KlineUpdate) -> None:
        """收到 kline WS 实时更新"""
        self._current_kline = update
        if update.is_closed:
            self._closed_1m.append(update)

    def update_klines(self, klines: Dict[str, list]) -> None:
        """更新 REST K 线数据"""
        self._klines = klines

    def snapshot(self) -> TrendSnapshot:
        kl = self._current_kline

        current_open = kl.open_price if kl else 0
        current_high = kl.high_price if kl else 0
        current_low = kl.low_price if kl else 0
        current_close = kl.close_price if kl else 0
        current_vol = kl.volume_usdt if kl else 0
        taker_buy_pct = 0.0
        if kl and kl.volume_usdt > 0:
            taker_buy_pct = round(kl.taker_buy_usdt / kl.volume_usdt * 100, 1)

        # 多周期趋势
        trend_1m = self._calc_trend_from_ws()
        trend_5m = self._calc_trend_from_rest("5m")
        trend_15m = self._calc_trend_from_rest("15m")
        trend_1h = self._calc_trend_from_rest("1h")
        trend_4h = self._calc_trend_from_rest("4h")

        # 趋势一致性评分
        trends = [trend_1m, trend_5m, trend_15m, trend_1h, trend_4h]
        score = sum(1 if t == "up" else (-1 if t == "down" else 0) for t in trends)

        if score >= 4:
            alignment = "strong_up"
        elif score >= 2:
            alignment = "up"
        elif score <= -4:
            alignment = "strong_down"
        elif score <= -2:
            alignment = "down"
        else:
            alignment = "mixed"

        # 成交量趋势
        vol_trend = self._calc_volume_trend()

        # 综合信号
        signals = []
        if alignment in ("strong_up", "strong_down"):
            signals.append(f"多周期趋势一致({alignment}, 得分{score:+d})")
        if vol_trend == "increasing":
            signals.append("成交量放大")
        elif vol_trend == "decreasing":
            signals.append("成交量萎缩")
        if taker_buy_pct > 65:
            signals.append(f"主动买入强势({taker_buy_pct:.0f}%)")
        elif taker_buy_pct < 35:
            signals.append(f"主动卖出强势({taker_buy_pct:.0f}%)")

        signal = "; ".join(signals) if signals else "趋势中性"

        return TrendSnapshot(
            current_1m_open=current_open,
            current_1m_high=current_high,
            current_1m_low=current_low,
            current_1m_close=current_close,
            current_1m_volume_usdt=current_vol,
            current_1m_taker_buy_pct=taker_buy_pct,
            trend_1m=trend_1m,
            trend_5m=trend_5m,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            trend_4h=trend_4h,
            trend_alignment=alignment,
            alignment_score=score,
            volume_trend=vol_trend,
            signal=signal,
        )

    def _calc_trend_from_ws(self) -> str:
        """从最近 5 根已完成 1m K 线判断趋势"""
        klines = list(self._closed_1m)[-5:]
        if len(klines) < 3:
            return "flat"
        closes = [k.close_price for k in klines]
        return self._trend_from_closes(closes)

    def _calc_trend_from_rest(self, interval: str) -> str:
        """从 REST K 线数据判断趋势（最近 5 根）"""
        data = self._klines.get(interval, [])
        if len(data) < 5:
            return "flat"
        closes = [k["c"] for k in data[-5:]]
        return self._trend_from_closes(closes)

    @staticmethod
    def _trend_from_closes(closes: list) -> str:
        """简单趋势判断：比较首尾 + 计算涨跌根数"""
        if len(closes) < 3:
            return "flat"
        up_count = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
        down_count = len(closes) - 1 - up_count
        change_pct = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0

        if change_pct > 0.1 and up_count >= 3:
            return "up"
        elif change_pct < -0.1 and down_count >= 3:
            return "down"
        else:
            return "flat"

    def _calc_volume_trend(self) -> str:
        """从 1m 已完成 K 线判断成交量趋势"""
        klines = list(self._closed_1m)[-10:]
        if len(klines) < 6:
            return "stable"
        recent_vol = sum(k.volume_usdt for k in klines[-3:]) / 3
        older_vol = sum(k.volume_usdt for k in klines[:3]) / 3
        if older_vol <= 0:
            return "stable"
        ratio = recent_vol / older_vol
        if ratio > 1.5:
            return "increasing"
        elif ratio < 0.67:
            return "decreasing"
        return "stable"
