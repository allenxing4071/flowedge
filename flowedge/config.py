"""
FlowEdge 配置管理
从 .env 读取基础配置（API 密钥、币种、模式开关等），提供统一访问入口。

注意：纸盘/门卫/信号等可优化参数已全部迁移到 ParamRegistry（唯一数据源），
不再在此文件中定义。通过 API 热更新：PUT /optimizer/params
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

    # ── 特征参数（已迁移到 ParamRegistry，此处仅作 env 兼容兜底） ──
    LARGE_TRADE_THRESHOLD: float = float(os.getenv("LARGE_TRADE_THRESHOLD", "50000"))
    VPIN_BUCKET_SIZE: float = float(os.getenv("VPIN_BUCKET_SIZE", "100000"))
    VPIN_NUM_BUCKETS: int = int(os.getenv("VPIN_NUM_BUCKETS", "50"))

    # ── Coinglass API（付费，$29/月） ──
    COINGLASS_API_KEY: str = os.getenv("COINGLASS_API_KEY", "")

    # ── Coinalyze API（免费，40 次/分钟） ──
    COINALYZE_API_KEY: str = os.getenv("COINALYZE_API_KEY", "")

    # ── 数据路径 ──
    DATA_DIR: Path = DATA_DIR

    # ── 南哥打法：高频 + 跟随做市商 + 判断方向（参考 flowdege/参考/） ──
    NANGE_MODE: bool = os.getenv("NANGE_MODE", "false").lower() in ("true", "1", "yes")

    # ── 高频模式（更短间隔、更频繁推送与评估；南哥打法建议同开） ──
    HIGH_FREQ_MODE: bool = os.getenv("HIGH_FREQ_MODE", "true" if os.getenv("NANGE_MODE", "").lower() in ("true", "1", "yes") else "false").lower() in ("true", "1", "yes")
    BROADCAST_INTERVAL_MS: int = 100 if HIGH_FREQ_MODE else 200   # SSE 特征推送
    SIGNAL_EVAL_INTERVAL_MS: int = 300 if HIGH_FREQ_MODE else 1000  # 信号评估
    DATA_SYNC_LOOP_S: int = 5 if HIGH_FREQ_MODE else 10            # REST 缓存同步到引擎
    TRACKER_LOOP_S: int = 15 if HIGH_FREQ_MODE else 30             # 信号追踪循环
    EQUITY_LOOP_S: int = 30 if HIGH_FREQ_MODE else 60              # 纸盘资金曲线
    REST_POLL_INTERVAL_S: int = 120 if HIGH_FREQ_MODE else 300     # 中频 REST 轮询（Coinglass/币安 OI 等）
    EXTERNAL_CHECK_S: int = 30 if HIGH_FREQ_MODE else 60           # 外部数据检查周期

    # ── 纸盘/门卫/信号等可优化参数已全部迁移到 ParamRegistry ──
    # 不再在此定义 PAPER_*/GATE_* 常量
    # 查看/修改参数：GET/PUT /optimizer/params

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
