"""
FlowEdge 配置管理
从 .env 读取所有配置，提供统一访问入口。
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"

# 加载 .env
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)


class Config:
    """应用配置（只读，从环境变量加载）"""

    # ── 币安 API（与 KKline 共用同一账户） ──
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")

    # ── 监控币种 ──
    WATCH_SYMBOLS: list[str] = [
        s.strip().upper()
        for s in os.getenv("WATCH_SYMBOLS", "BTCUSDT").split(",")
        if s.strip()
    ]

    # ── 大单阈值（USDT） ──
    LARGE_TRADE_THRESHOLD: float = float(os.getenv("LARGE_TRADE_THRESHOLD", "50000"))

    # ── VPIN 参数 ──
    VPIN_BUCKET_SIZE: float = float(os.getenv("VPIN_BUCKET_SIZE", "100000"))
    VPIN_NUM_BUCKETS: int = int(os.getenv("VPIN_NUM_BUCKETS", "50"))

    # ── Coinglass API（付费，$29/月） ──
    COINGLASS_API_KEY: str = os.getenv("COINGLASS_API_KEY", "")

    # ── Coinalyze API（免费，40 次/分钟） ──
    COINALYZE_API_KEY: str = os.getenv("COINALYZE_API_KEY", "")

    # ── 数据路径 ──
    DATA_DIR: Path = DATA_DIR

    @classmethod
    def validate(cls) -> list[str]:
        """验证必须的配置项，返回错误列表"""
        errors = []
        if not cls.BINANCE_API_KEY:
            errors.append("BINANCE_API_KEY 未配置")
        if not cls.BINANCE_API_SECRET:
            errors.append("BINANCE_API_SECRET 未配置")
        if not cls.WATCH_SYMBOLS:
            errors.append("WATCH_SYMBOLS 未配置")
        return errors

    @classmethod
    def ensure_dirs(cls):
        """确保数据目录存在"""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)


# 全局配置单例
cfg = Config()
