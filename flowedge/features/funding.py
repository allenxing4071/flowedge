"""
资金费率特征计算器
追踪实时资金费率变化，检测极端费率（清算前兆）。
核心信号：|funding_rate| > 0.05% → 大规模清算风险极高。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class FundingSnapshot:
    """资金费率特征快照"""
    current_rate: float          # 当前资金费率（%）
    rate_abs: float              # 绝对值
    mark_price: float            # 当前标记价格
    index_price: float           # 当前指数价格
    basis_pct: float             # 基差 (mark-index)/index * 100
    next_funding_min: int        # 距下次结算（分钟）
    rate_trend: str              # "rising" / "falling" / "stable"
    extreme_level: str           # "normal" / "elevated" / "extreme" / "critical"
    signal: str                  # 综合信号文本


class FundingRateTracker:
    """
    资金费率追踪器。

    从 markPrice@1s 流获取实时费率，分析：
    - 费率趋势（上升/下降/稳定）
    - 极端费率级别
    - 基差（标记价 vs 指数价）
    """

    def __init__(self, history_size: int = 300):
        # 保存最近 N 秒的费率历史（用于趋势判断）
        self._history: deque[tuple[int, float]] = deque(maxlen=history_size)
        self._current_rate = 0.0
        self._mark_price = 0.0
        self._index_price = 0.0
        self._next_funding_ms = 0

    def on_mark_price(
        self,
        funding_rate: float,
        mark_price: float,
        index_price: float,
        next_funding_ms: int,
        timestamp_ms: int,
    ) -> None:
        """收到一条 markPrice 更新"""
        self._current_rate = funding_rate
        self._mark_price = mark_price
        self._index_price = index_price
        self._next_funding_ms = next_funding_ms
        self._history.append((timestamp_ms, funding_rate))

    def snapshot(self) -> FundingSnapshot:
        """获取当前资金费率快照"""
        rate = self._current_rate
        rate_pct = rate * 100  # 转为百分比
        rate_abs = abs(rate_pct)

        # 基差
        basis_pct = 0.0
        if self._index_price > 0:
            basis_pct = (self._mark_price - self._index_price) / self._index_price * 100

        # 距下次结算分钟数
        now_ms = int(time.time() * 1000)
        next_min = max(0, (self._next_funding_ms - now_ms) // 60000)

        # 趋势判断（比较最近 30s vs 前 30s）
        trend = "stable"
        if len(self._history) >= 60:
            recent = [r for _, r in list(self._history)[-30:]]
            older = [r for _, r in list(self._history)[-60:-30]]
            if recent and older:
                recent_avg = sum(recent) / len(recent)
                older_avg = sum(older) / len(older)
                diff = recent_avg - older_avg
                if diff > 0.00005:
                    trend = "rising"
                elif diff < -0.00005:
                    trend = "falling"

        # 极端等级
        if rate_abs >= 0.1:
            extreme = "critical"
        elif rate_abs >= 0.05:
            extreme = "extreme"
        elif rate_abs >= 0.02:
            extreme = "elevated"
        else:
            extreme = "normal"

        # 综合信号
        direction = "多付空" if rate > 0 else "空付多" if rate < 0 else "平衡"
        if extreme == "critical":
            signal = f"费率{rate_pct:+.4f}%极端危险({direction})，清算级联风险极高"
        elif extreme == "extreme":
            signal = f"费率{rate_pct:+.4f}%过高({direction})，大规模清算风险"
        elif extreme == "elevated":
            signal = f"费率{rate_pct:+.4f}%偏高({direction})，关注清算"
        else:
            signal = f"费率{rate_pct:+.4f}%正常({direction})"

        return FundingSnapshot(
            current_rate=round(rate_pct, 6),
            rate_abs=round(rate_abs, 6),
            mark_price=self._mark_price,
            index_price=self._index_price,
            basis_pct=round(basis_pct, 4),
            next_funding_min=next_min,
            rate_trend=trend,
            extreme_level=extreme,
            signal=signal,
        )
