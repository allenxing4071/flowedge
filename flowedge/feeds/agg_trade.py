"""
aggTrade WebSocket 流处理器
接入币安逐笔成交数据，提取 taker 方向和成交量，分发给特征引擎。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

import orjson
import websockets

logger = logging.getLogger("feed.agg_trade")

WS_BASE = "wss://fstream.binance.com"


@dataclass
class AggTrade:
    """逐笔聚合成交"""
    timestamp_ms: int    # 成交时间
    price: float         # 成交价
    qty: float           # 成交量（币）
    qty_usdt: float      # 成交额（USDT）
    is_taker_buy: bool   # True=taker 主动买入, False=taker 主动卖出


# 回调类型：收到一笔成交后调用
OnAggTrade = Callable[[str, AggTrade], Awaitable[None]]


class AggTradeStream:
    """
    管理一个或多个币种的 aggTrade WebSocket 连接。
    解析消息后通过回调分发给特征引擎。
    """

    def __init__(self, symbols: list[str], on_trade: OnAggTrade):
        self._symbols = [s.lower() for s in symbols]
        self._on_trade = on_trade
        self._running = False
        self._msg_count = 0
        self._last_msg_time = 0.0

    @property
    def msg_count(self) -> int:
        return self._msg_count

    @property
    def msg_rate(self) -> float:
        """近似消息速率（条/秒）"""
        elapsed = time.monotonic() - self._last_msg_time if self._last_msg_time else 0
        return self._msg_count / max(elapsed, 1)

    def _build_url(self) -> str:
        streams = [f"{s}@aggTrade" for s in self._symbols]
        if len(streams) == 1:
            return f"{WS_BASE}/ws/{streams[0]}"
        return f"{WS_BASE}/stream?streams={'/'.join(streams)}"

    async def run(self) -> None:
        """启动 WebSocket 连接，断线自动重连"""
        self._running = True
        self._last_msg_time = time.monotonic()
        url = self._build_url()

        while self._running:
            try:
                logger.info(f"[aggTrade] 连接 {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[aggTrade] 已连接，监控 {self._symbols}")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = orjson.loads(raw)
                            # 多流格式
                            data = msg.get("data", msg)
                            await self._handle(data)
                        except Exception as e:
                            logger.warning(f"[aggTrade] 解析失败: {e}")
            except websockets.ConnectionClosed as e:
                logger.warning(f"[aggTrade] 连接关闭: {e}, 5s 后重连")
            except Exception as e:
                logger.error(f"[aggTrade] 连接错误: {e}, 5s 后重连")
            if self._running:
                await asyncio.sleep(5)

    async def _handle(self, data: dict) -> None:
        """解析一条 aggTrade 消息并分发"""
        symbol = str(data.get("s", "")).upper()
        price = float(data.get("p", 0))
        qty = float(data.get("q", 0))
        ts = int(data.get("T", 0))
        # isBuyerMaker=True 表示买方是 maker → taker 是卖方
        is_buyer_maker = data.get("m", False)

        trade = AggTrade(
            timestamp_ms=ts,
            price=price,
            qty=qty,
            qty_usdt=price * qty,
            is_taker_buy=not is_buyer_maker,
        )
        self._msg_count += 1
        await self._on_trade(symbol, trade)

    def stop(self) -> None:
        self._running = False
