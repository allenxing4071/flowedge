"""
bookTicker WebSocket 流处理器
接入币安最优买卖价实时推送，计算实时价差。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

import orjson
import websockets

logger = logging.getLogger("feed.book_ticker")

WS_BASE = "wss://fstream.binance.com"


@dataclass
class BookTick:
    """最优买卖价"""
    timestamp_ms: int
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    spread_pct: float     # (ask-bid)/mid * 100
    mid_price: float


OnBookTick = Callable[[str, BookTick], Awaitable[None]]


class BookTickerStream:
    """
    管理 bookTicker WebSocket 连接。
    实时推送最优买卖价和价差。
    """

    def __init__(self, symbols: list[str], on_tick: OnBookTick):
        self._symbols = [s.lower() for s in symbols]
        self._on_tick = on_tick
        self._running = False
        self._msg_count = 0

    @property
    def msg_count(self) -> int:
        return self._msg_count

    def _build_url(self) -> str:
        streams = [f"{s}@bookTicker" for s in self._symbols]
        if len(streams) == 1:
            return f"{WS_BASE}/ws/{streams[0]}"
        return f"{WS_BASE}/stream?streams={'/'.join(streams)}"

    async def run(self) -> None:
        """启动 WebSocket 连接，断线自动重连"""
        self._running = True
        url = self._build_url()

        while self._running:
            try:
                logger.info(f"[bookTicker] 连接 {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[bookTicker] 已连接，监控 {self._symbols}")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = orjson.loads(raw)
                            data = msg.get("data", msg)
                            await self._handle(data)
                        except Exception as e:
                            logger.warning(f"[bookTicker] 解析失败: {e}")
            except websockets.ConnectionClosed as e:
                logger.warning(f"[bookTicker] 连接关闭: {e}, 5s 后重连")
            except Exception as e:
                logger.error(f"[bookTicker] 连接错误: {e}, 5s 后重连")
            if self._running:
                await asyncio.sleep(5)

    async def _handle(self, data: dict) -> None:
        """解析一条 bookTicker 消息"""
        symbol = str(data.get("s", "")).upper()
        bid_price = float(data.get("b", 0))
        bid_qty = float(data.get("B", 0))
        ask_price = float(data.get("a", 0))
        ask_qty = float(data.get("A", 0))
        ts = int(data.get("T", data.get("E", 0)))

        mid = (bid_price + ask_price) / 2 if (bid_price + ask_price) > 0 else 0
        spread_pct = (ask_price - bid_price) / mid * 100 if mid > 0 else 0

        tick = BookTick(
            timestamp_ms=ts,
            bid_price=bid_price,
            bid_qty=bid_qty,
            ask_price=ask_price,
            ask_qty=ask_qty,
            spread_pct=round(spread_pct, 6),
            mid_price=round(mid, 2),
        )
        self._msg_count += 1
        await self._on_tick(symbol, tick)

    def stop(self) -> None:
        self._running = False
