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
    PAPER_MIN_HOLD_S: float = 120.0 if HIGH_FREQ_MODE else 300.0  # 纸盘最低持仓时间（秒）
    PAPER_MIN_HOLD_WRONG_S: float = float(os.getenv("PAPER_MIN_HOLD_WRONG_S", "15"))  # 错了马上出：NEUTRAL 且浮亏时最短持仓秒数

    # ── 放开/缩紧：先彻底放开，后续用 env 慢慢缩紧 ──
    PAPER_MIN_CONFIDENCE: float = float(os.getenv("PAPER_MIN_CONFIDENCE", "0.15"))   # 纸盘最低置信度（放开 0.15）
    PAPER_MIN_ENTRY_SCORE: float = float(os.getenv("PAPER_MIN_ENTRY_SCORE", "0.05"))  # 纸盘最低评分绝对值（放开 0.05）
    PAPER_COOLDOWN_S: float = float(os.getenv("PAPER_COOLDOWN_S", "30"))             # 开仓冷却秒（放开 30）
    GATE_MIN_SCORE: float = float(os.getenv("GATE_MIN_SCORE", "0.05"))               # 门卫最低 |score|（放开 0.05）
    GATE_MIN_CONFIDENCE: float = float(os.getenv("GATE_MIN_CONFIDENCE", "0.15"))     # 门卫最低置信度（放开 0.15）
    GATE_TIME_FILTER_ENABLED: bool = os.getenv("GATE_TIME_FILTER_ENABLED", "false").lower() in ("true", "1", "yes")  # 30 分钟节点过滤（放开时关）
    # 南哥打法 = 四层全做（含 L3 吸收/假墙/大单）。仅临时“先开单”调试时可设为 true 跳过 L3
    GATE_SKIP_BEHAVIOR_LAYER: bool = os.getenv("GATE_SKIP_BEHAVIOR_LAYER", "false").lower() in ("true", "1", "yes")

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
