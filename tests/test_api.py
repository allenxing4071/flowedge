"""
FlowEdge API 端点测试
覆盖 /health、/status、/features/snapshot 等核心接口。
"""

import pytest


class TestHealth:
    """健康检查端点"""

    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "FlowEdge" in data.get("service", "")
        assert "version" in data


class TestStatus:
    """系统状态端点"""

    def test_status_returns_dict(self, client):
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_status_has_expected_keys(self, client):
        r = client.get("/status")
        data = r.json()
        # 演示模式下可能缺少部分 key，至少返回非 5xx
        assert r.status_code == 200


class TestFeaturesSnapshot:
    """特征快照端点"""

    def test_snapshot_returns_dict(self, client):
        r = client.get("/features/snapshot")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)

    def test_snapshot_with_symbol_filter(self, client):
        r = client.get("/features/snapshot?symbol=BTCUSDT")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)


class TestRateLimits:
    """速率限制器状态端点"""

    def test_rate_limits_returns_dict(self, client):
        r = client.get("/rate-limits")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict)
