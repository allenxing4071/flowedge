"""
FlowEdge 速率限制器单元测试
覆盖 TokenBucketLimiter 的令牌补充、acquire 行为。
"""

import asyncio
import pytest
from flowedge.core.rate_limiter import TokenBucketLimiter, RateLimiterRegistry


class TestTokenBucketLimiter:
    """TokenBucketLimiter 基础功能测试"""

    @pytest.mark.asyncio
    async def test_initial_tokens_equal_burst(self):
        """初始化时可用令牌应等于 burst"""
        limiter = TokenBucketLimiter("test", rate=10.0, burst=5)
        assert limiter.available == 5.0

    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self):
        """acquire 消耗令牌"""
        limiter = TokenBucketLimiter("test", rate=100.0, burst=5)
        await limiter.acquire(1)
        assert limiter.available == pytest.approx(4.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_acquire_multiple(self):
        """acquire 多令牌"""
        limiter = TokenBucketLimiter("test", rate=100.0, burst=10)
        await limiter.acquire(3)
        assert limiter.available == pytest.approx(7.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_refill_over_time(self):
        """令牌随时间的补充"""
        limiter = TokenBucketLimiter("test", rate=10.0, burst=5)  # 每秒 10 个
        await limiter.acquire(5)
        assert limiter.available == pytest.approx(0.0, abs=0.01)
        await asyncio.sleep(0.2)  # 0.2 秒应补充约 2 个令牌
        assert limiter.available >= 1.5

    @pytest.mark.asyncio
    async def test_stats_has_expected_keys(self):
        """stats 返回期望字段"""
        limiter = TokenBucketLimiter("test", rate=1.0, burst=5)
        await limiter.acquire(1)
        s = limiter.stats()
        assert "name" in s
        assert "rate_per_sec" in s
        assert "burst" in s
        assert "available" in s
        assert "total_acquired" in s
        assert s["total_acquired"] == 1


class TestRateLimiterRegistry:
    """RateLimiterRegistry 注册表测试"""

    def test_default_limiters_registered(self):
        """默认应注册 4 个限制器"""
        registry = RateLimiterRegistry()
        stats = registry.stats()
        assert "binance" in stats
        assert "coinglass" in stats
        assert "coinalyze" in stats
        assert "external" in stats

    def test_get_unknown_creates_default(self):
        """获取不存在的限制器时创建默认"""
        registry = RateLimiterRegistry()
        limiter = registry.get("unknown_xyz")
        assert limiter is not None
        assert limiter.name == "unknown_xyz"
        assert limiter.available > 0
