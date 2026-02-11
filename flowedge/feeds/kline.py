"""
kline@1m WebSocket 流处理器
接入币安实时 1 分钟 K 线推送，为趋势分析提供实时上下文。
核心价值：微观结构信号 + 趋势方向 = 避免逆势交易。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

import orjson
import websockets

logger = logging.getLogger("feed.kline")

WS_BASE = "wss://fstream.binance.com"


@dataclass
class KlineUpdate:
    """K 线实时更新"""
    timestamp_ms: int
    symbol: str
    interval: str          # "1m"
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float          # 成交量（币）
    volume_usdt: float     # 成交额（USDT）
    trade_count: int       # 成交笔数
    taker_buy_vol: float   # 主动买入量（币）
    taker_buy_usdt: float  # 主动买入额（USDT）
    is_closed: bool        # 是否为已完成的 K 线


OnKline = Callable[[str, KlineUpdate], Awaitable[None]]


class KlineStream:
    """
    管理 kline@1m WebSocket 连接。
    同时支持多个币种，通过组合流一个连接搞定。
    """

    def __init__(self, symbols: list[str], on_kline: OnKline):
        self._symbols = [s.lower() for s in symbols]
        self._on_kline = on_kline
        self._running = False
        self._msg_count = 0

    @property
    def msg_count(self) -> int:
        return self._msg_count

    def _build_url(self) -> str:
        streams = [f"{s}@kline_1m" for s in self._symbols]
        if len(streams) == 1:
            return f"{WS_BASE}/ws/{streams[0]}"
        return f"{WS_BASE}/stream?streams={'/'.join(streams)}"

    async def run(self) -> None:
        """启动 WebSocket 连接，断线自动重连"""
        self._running = True
        url = self._build_url()

        while self._running:
            try:
                logger.info(f"[kline] 连接 {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[kline] 已连接，监控 {self._symbols}")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = orjson.loads(raw)
                            data = msg.get("data", msg)
                            await self._handle(data)
                        except Exception as e:
                            logger.warning(f"[kline] 解析失败: {e}")
            except websockets.ConnectionClosed as e:
                logger.warning(f"[kline] 连接关闭: {e}, 5s 后重连")
            except Exception as e:
                logger.error(f"[kline] 连接错误: {e}, 5s 后重连")
            if self._running:
                await asyncio.sleep(5)

    async def _handle(self, data: dict) -> None:
        """解析一条 kline 消息"""
        k = data.get("k", {})
        symbol = str(data.get("s", "")).upper()

        update = KlineUpdate(
            timestamp_ms=int(data.get("E", 0)),
            symbol=symbol,
            interval=str(k.get("i", "1m")),
            open_price=float(k.get("o", 0)),
            high_price=float(k.get("h", 0)),
            low_price=float(k.get("l", 0)),
            close_price=float(k.get("c", 0)),
            volume=float(k.get("v", 0)),
            volume_usdt=float(k.get("q", 0)),
            trade_count=int(k.get("n", 0)),
            taker_buy_vol=float(k.get("V", 0)),
            taker_buy_usdt=float(k.get("Q", 0)),
            is_closed=bool(k.get("x", False)),
        )
        self._msg_count += 1
        await self._on_kline(symbol, update)

    def stop(self) -> None:
        self._running = False
