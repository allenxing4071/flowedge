"""
高性能环形缓冲（numpy 实现）
固定内存，O(1) push，O(1) 滚动聚合（sum/mean），用于实时特征计算。
"""

from __future__ import annotations

import numpy as np


class RingBuffer:
    """
    固定容量的 numpy 环形缓冲。

    特点：
    - 内存预分配，写入时零分配
    - push O(1)，sum/mean O(1)（维护增量 running sum）
    - 支持按时间窗口切片（需要配合 timestamps 使用）
    """

    __slots__ = ("_buf", "_ts", "_capacity", "_head", "_count", "_running_sum")

    def __init__(self, capacity: int = 30000, dtype=np.float64):
        self._capacity = capacity
        self._buf = np.zeros(capacity, dtype=dtype)
        self._ts = np.zeros(capacity, dtype=np.int64)  # 毫秒时间戳
        self._head = 0      # 下一个写入位置
        self._count = 0     # 已写入的总数（可超过 capacity）
        self._running_sum = 0.0

    @property
    def size(self) -> int:
        """当前缓冲中的有效数据量"""
        return min(self._count, self._capacity)

    @property
    def full(self) -> bool:
        return self._count >= self._capacity

    def push(self, value: float, timestamp_ms: int = 0) -> None:
        """写入一条数据，O(1)"""
        # 如果缓冲已满，减去即将被覆盖的旧值
        if self._count >= self._capacity:
            self._running_sum -= self._buf[self._head]
        self._buf[self._head] = value
        self._ts[self._head] = timestamp_ms
        self._running_sum += value
        self._head = (self._head + 1) % self._capacity
        self._count += 1

    def sum(self) -> float:
        """当前所有有效数据之和，O(1)"""
        return float(self._running_sum)

    def mean(self) -> float:
        """当前所有有效数据的均值，O(1)"""
        n = self.size
        return float(self._running_sum / n) if n > 0 else 0.0

    def last(self) -> float:
        """最后一条数据"""
        if self._count == 0:
            return 0.0
        idx = (self._head - 1) % self._capacity
        return float(self._buf[idx])

    def window_sum(self, since_ms: int) -> float:
        """返回 timestamp_ms >= since_ms 的数据之和，O(n) 最坏情况"""
        if self._count == 0:
            return 0.0
        n = self.size
        total = 0.0
        for i in range(n):
            idx = (self._head - 1 - i) % self._capacity
            if self._ts[idx] < since_ms:
                break
            total += self._buf[idx]
        return float(total)

    def window_count(self, since_ms: int) -> int:
        """返回 timestamp_ms >= since_ms 的数据数量"""
        if self._count == 0:
            return 0
        n = self.size
        count = 0
        for i in range(n):
            idx = (self._head - 1 - i) % self._capacity
            if self._ts[idx] < since_ms:
                break
            count += 1
        return count

    def recent_values(self, count: int) -> np.ndarray:
        """返回最近 count 条数据（从旧到新）"""
        n = min(count, self.size)
        if n == 0:
            return np.array([], dtype=self._buf.dtype)
        result = np.empty(n, dtype=self._buf.dtype)
        for i in range(n):
            result[n - 1 - i] = self._buf[(self._head - 1 - i) % self._capacity]
        return result

    def clear(self) -> None:
        """清空缓冲"""
        self._head = 0
        self._count = 0
        self._running_sum = 0.0
        self._buf[:] = 0
        self._ts[:] = 0
