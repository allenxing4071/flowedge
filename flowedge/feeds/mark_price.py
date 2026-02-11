"""
markPrice@1s WebSocket 流处理器
接入币安标记价格 + 实时资金费率推送，1 秒一条。
核心价值：资金费率极端值（>0.05%）是大规模清算的前兆。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

import orjson
import websockets

logger = logging.getLogger("feed.mark_price")

WS_BASE = "wss://fstream.binance.com"


@dataclass
class MarkPriceTick:
    """标记价格 + 资金费率"""
    timestamp_ms: int
    symbol: str
    mark_price: float          # 标记价格
    index_price: float         # 指数价格（现货参考）
    funding_rate: float        # 当前资金费率
    next_funding_time_ms: int  # 下次结算时间


OnMarkPrice = Callable[[str, MarkPriceTick], Awaitable[None]]


class MarkPriceStream:
    """
    管理 markPrice@1s WebSocket 连接。
    每秒推送标记价格和实时资金费率。
    """

    def __init__(self, symbols: list[str], on_tick: OnMarkPrice):
        self._symbols = [s.lower() for s in symbols]
        self._on_tick = on_tick
        self._running = False
        self._msg_count = 0

    @property
    def msg_count(self) -> int:
        return self._msg_count

    def _build_url(self) -> str:
        streams = [f"{s}@markPrice@1s" for s in self._symbols]
        if len(streams) == 1:
            return f"{WS_BASE}/ws/{streams[0]}"
        return f"{WS_BASE}/stream?streams={'/'.join(streams)}"

    async def run(self) -> None:
        """启动 WebSocket 连接，断线自动重连"""
        self._running = True
        url = self._build_url()

        while self._running:
            try:
                logger.info(f"[markPrice] 连接 {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[markPrice] 已连接，监控 {self._symbols}")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = orjson.loads(raw)
                            data = msg.get("data", msg)
                            await self._handle(data)
                        except Exception as e:
                            logger.warning(f"[markPrice] 解析失败: {e}")
            except websockets.ConnectionClosed as e:
                logger.warning(f"[markPrice] 连接关闭: {e}, 5s 后重连")
            except Exception as e:
                logger.error(f"[markPrice] 连接错误: {e}, 5s 后重连")
            if self._running:
                await asyncio.sleep(5)

    async def _handle(self, data: dict) -> None:
        """解析一条 markPrice 消息"""
        symbol = str(data.get("s", "")).upper()
        tick = MarkPriceTick(
            timestamp_ms=int(data.get("E", 0)),
            symbol=symbol,
            mark_price=float(data.get("p", 0)),
            index_price=float(data.get("i", 0)),
            funding_rate=float(data.get("r", 0)),
            next_funding_time_ms=int(data.get("T", 0)),
        )
        self._msg_count += 1
        await self._on_tick(symbol, tick)

    def stop(self) -> None:
        self._running = False
