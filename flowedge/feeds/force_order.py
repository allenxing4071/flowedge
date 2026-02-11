"""
forceOrder WebSocket 流处理器（全市场强制清算流）
接入币安全市场实时清算事件推送 — 这是 KKline 没有的全新数据源。
核心价值：清算级联是市场暴涨暴跌的直接原因，实时检测可提前 1-3 秒反应。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Awaitable

import orjson
import websockets

logger = logging.getLogger("feed.force_order")

WS_BASE = "wss://fstream.binance.com"


@dataclass
class LiquidationEvent:
    """单个清算事件"""
    timestamp_ms: int
    symbol: str
    side: str           # "BUY" 或 "SELL"（被清算方向的反向 = taker 方向）
    price: float        # 清算价格
    qty: float          # 清算数量（币）
    qty_usdt: float     # 清算金额（USDT）
    is_long_liq: bool   # True = 多头被清算（价格下跌导致）


OnLiquidation = Callable[[LiquidationEvent], Awaitable[None]]


class ForceOrderStream:
    """
    管理全市场清算事件 WebSocket 连接。
    订阅 !forceOrder@arr，接收所有币种的实时清算。
    """

    def __init__(self, on_liquidation: OnLiquidation, watch_symbols: list[str] = None):
        self._on_liquidation = on_liquidation
        # 只关注指定币种的清算（None = 全部）
        self._watch = set(s.upper() for s in watch_symbols) if watch_symbols else None
        self._running = False
        self._msg_count = 0

    @property
    def msg_count(self) -> int:
        return self._msg_count

    async def run(self) -> None:
        """启动 WebSocket 连接，断线自动重连"""
        self._running = True
        url = f"{WS_BASE}/ws/!forceOrder@arr"

        while self._running:
            try:
                logger.info(f"[forceOrder] 连接 {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("[forceOrder] 已连接，监听全市场清算事件")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = orjson.loads(raw)
                            # forceOrder@arr 返回的是数组
                            if isinstance(msg, list):
                                for item in msg:
                                    await self._handle(item)
                            else:
                                await self._handle(msg)
                        except Exception as e:
                            logger.warning(f"[forceOrder] 解析失败: {e}")
            except websockets.ConnectionClosed as e:
                logger.warning(f"[forceOrder] 连接关闭: {e}, 5s 后重连")
            except Exception as e:
                logger.error(f"[forceOrder] 连接错误: {e}, 5s 后重连")
            if self._running:
                await asyncio.sleep(5)

    async def _handle(self, data: dict) -> None:
        """解析一条 forceOrder 消息"""
        order = data.get("o", data)
        symbol = str(order.get("s", "")).upper()

        # 如果设置了关注列表，过滤不关注的币种
        if self._watch and symbol not in self._watch:
            return

        side = str(order.get("S", "")).upper()       # BUY/SELL
        price = float(order.get("p", 0))
        qty = float(order.get("q", 0))
        ts = int(order.get("T", int(time.time() * 1000)))

        # 被清算方的方向：如果 side=SELL，说明系统在卖出（清算多头）
        is_long_liq = (side == "SELL")

        event = LiquidationEvent(
            timestamp_ms=ts,
            symbol=symbol,
            side=side,
            price=price,
            qty=qty,
            qty_usdt=price * qty,
            is_long_liq=is_long_liq,
        )
        self._msg_count += 1
        await self._on_liquidation(event)

    def stop(self) -> None:
        self._running = False
