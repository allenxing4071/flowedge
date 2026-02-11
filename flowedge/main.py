"""
FlowEdge 入口文件
启动 WebSocket 数据流 + 特征引擎 + FastAPI 服务。

启动方式：
  uvicorn flowedge.api:app --host 0.0.0.0 --port 8000
  或
  python -m flowedge.main
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn


def setup_logging() -> None:
    """配置日志格式"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    setup_logging()
    logger = logging.getLogger("flowedge")
    port = int(os.getenv("PORT", "8005"))
    logger.info("=" * 50)
    logger.info("  FlowEdge v3.0 — 特征引擎 + 信号层 + 交易驾驶舱")
    logger.info("=" * 50)

    uvicorn.run(
        "flowedge.api:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
