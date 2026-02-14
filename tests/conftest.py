"""
pytest 共享 fixture
测试前设置空配置以触发 lifespan 演示模式，不启动真实 WS/REST 连接。
"""

import os

# 必须在导入 flowedge 之前设置，使 cfg.validate() 失败，触发「演示模式」
os.environ["BINANCE_API_KEY"] = ""
os.environ["BINANCE_API_SECRET"] = ""
os.environ["WATCH_SYMBOLS"] = ""

import pytest
from fastapi.testclient import TestClient

from flowedge.api import app


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient，用于 API 端点测试"""
    return TestClient(app)
