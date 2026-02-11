"""
异步令牌桶速率限制器
保护所有外部 API 不因请求过频被封。
支持多个 API 源各自独立限速，币种数量增加时自动适配。

设计要点：
- 币安 REST：2400 权重/分钟（全 IP 共享）
- Coinglass：30 次/分钟（付费 Hobbyist）
- Coinalyze：40 次/分钟（免费）
- 恐慌贪婪指数：无限速（但做 60 次/分钟保护）
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("core.rate_limiter")


class TokenBucketLimiter:
    """
    令牌桶速率限制器。

    参数：
    - rate: 每秒补充的令牌数
    - burst: 桶的最大容量（允许短时突发）
    """

    def __init__(self, name: str, rate: float, burst: int):
        self.name = name
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._total_acquired = 0
        self._total_waited = 0.0

    async def acquire(self, tokens: int = 1) -> float:
        """
        获取令牌，不够时等待。
        返回等待时间（秒）。
        """
        async with self._lock:
            wait_total = 0.0
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self._total_acquired += tokens
                    self._total_waited += wait_total
                    return wait_total
                # 计算需要等待多久才能攒够令牌
                deficit = tokens - self._tokens
                wait_time = deficit / self._rate
                wait_total += wait_time
                if wait_total > 0.1:
                    logger.debug(f"[{self.name}] 限速等待 {wait_time:.2f}s（需要 {tokens} 令牌）")
                await asyncio.sleep(wait_time)

    def _refill(self) -> None:
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)

    @property
    def available(self) -> float:
        """当前可用令牌数"""
        self._refill()
        return self._tokens

    def stats(self) -> dict:
        """统计信息"""
        return {
            "name": self.name,
            "rate_per_sec": self._rate,
            "burst": self._burst,
            "available": round(self.available, 1),
            "total_acquired": self._total_acquired,
            "total_wait_sec": round(self._total_waited, 2),
        }


class RateLimiterRegistry:
    """
    全局速率限制器注册表。
    按 API 源名称管理，所有币种共享同一限制器。
    """

    def __init__(self):
        self._limiters: dict[str, TokenBucketLimiter] = {}
        self._init_defaults()

    def _init_defaults(self) -> None:
        """初始化默认限制器"""
        # 币安 REST：2400 权重/分钟 → 保守使用 80%（32/秒），burst=100
        self.register("binance", rate=32.0, burst=100)
        # Coinglass：30 次/分钟 → 0.5/秒，burst=10
        self.register("coinglass", rate=0.5, burst=10)
        # Coinalyze：40 次/分钟 → 0.66/秒，burst=10
        self.register("coinalyze", rate=0.66, burst=10)
        # 外部免费 API：保守限速
        self.register("external", rate=1.0, burst=5)

    def register(self, name: str, rate: float, burst: int) -> TokenBucketLimiter:
        """注册一个新的限制器"""
        limiter = TokenBucketLimiter(name, rate, burst)
        self._limiters[name] = limiter
        return limiter

    def get(self, name: str) -> TokenBucketLimiter:
        """获取限制器（不存在则创建默认的）"""
        if name not in self._limiters:
            logger.warning(f"限制器 '{name}' 不存在，创建默认限制器（1/s）")
            self.register(name, rate=1.0, burst=5)
        return self._limiters[name]

    def stats(self) -> dict:
        """所有限制器的统计"""
        return {name: l.stats() for name, l in self._limiters.items()}


# 全局单例
rate_limiters = RateLimiterRegistry()
