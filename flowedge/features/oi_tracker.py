"""
持仓量 (Open Interest) 变化追踪器
从中频 REST 数据监控 OI 变化，检测资金进出场信号。
核心信号：
- OI 急增 + 价格不动 → 大户正在建仓
- OI 急减 + 价格下跌 → 清算中 / 恐慌平仓
- OI 急增 + 价格同向运动 → 趋势加速
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class OISnapshot:
    """持仓量特征快照"""
    # 币安 OI
    oi_usdt: float              # 当前 OI（USDT）
    oi_change_pct: float        # 对比上次变化百分比
    oi_signal: str              # 信号文本
    # Coinglass 全网 OI（如有）
    global_oi_usd: float        # 全网 OI
    global_oi_change_1h: float  # 全网 1h 变化百分比
    global_oi_change_24h: float # 全网 24h 变化百分比
    oi_by_exchange: dict        # 分交易所 OI


class OITracker:
    """
    OI 变化追踪器。
    从 MarketDataCollector 的缓存读取数据，生成特征快照。
    """

    def __init__(self):
        self._oi_usdt = 0.0
        self._oi_change_pct = 0.0
        self._global_oi_usd = 0.0
        self._global_change_1h = 0.0
        self._global_change_24h = 0.0
        self._oi_by_exchange: dict = {}

    def update_binance_oi(self, oi_usdt: float, change_pct: float) -> None:
        """更新币安 OI 数据"""
        self._oi_usdt = oi_usdt
        self._oi_change_pct = change_pct

    def update_coinglass_oi(
        self, total_usd: float, change_1h: float, change_24h: float, by_exchange: dict
    ) -> None:
        """更新 Coinglass 全网 OI 数据"""
        self._global_oi_usd = total_usd
        self._global_change_1h = change_1h
        self._global_change_24h = change_24h
        self._oi_by_exchange = by_exchange

    def snapshot(self) -> OISnapshot:
        """获取当前 OI 快照"""
        # 生成信号
        pct = self._oi_change_pct
        if pct > 3:
            signal = f"OI大幅增加{pct:+.1f}%，新资金大量入场"
        elif pct > 0.5:
            signal = f"OI温和增加{pct:+.1f}%，资金流入"
        elif pct < -3:
            signal = f"OI大幅减少{pct:+.1f}%，大量平仓/清算"
        elif pct < -0.5:
            signal = f"OI温和减少{pct:+.1f}%，资金流出"
        else:
            signal = "OI平稳"

        return OISnapshot(
            oi_usdt=round(self._oi_usdt, 2),
            oi_change_pct=round(self._oi_change_pct, 2),
            oi_signal=signal,
            global_oi_usd=round(self._global_oi_usd, 2),
            global_oi_change_1h=round(self._global_change_1h, 2),
            global_oi_change_24h=round(self._global_change_24h, 2),
            oi_by_exchange=self._oi_by_exchange,
        )
