"""
depth@100ms WebSocket 流处理器
接入币安订单簿增量更新，本地维护完整 20 档快照，分发给特征引擎。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable

import orjson
import websockets
import httpx

logger = logging.getLogger("feed.depth")

WS_BASE = "wss://fstream.binance.com"
REST_BASE = "https://fapi.binance.com"


@dataclass
class OrderBookSnapshot:
    """20 档订单簿快照"""
    timestamp_ms: int
    bids: list[list[float]]    # [[price, qty], ...] 价格从高到低
    asks: list[list[float]]    # [[price, qty], ...] 价格从低到高
    bid_total_usdt: float = 0.0
    ask_total_usdt: float = 0.0
    imbalance_pct: float = 0.0  # (bid-ask)/(bid+ask)*100


OnDepthUpdate = Callable[[str, OrderBookSnapshot], Awaitable[None]]


class DepthStream:
    """
    管理订单簿 WebSocket 连接。
    使用增量更新 + REST 快照初始化，维护本地完整订单簿。
    """

    def __init__(self, symbols: list[str], on_update: OnDepthUpdate, depth_limit: int = 20):
        self._symbols = [s.lower() for s in symbols]
        self._on_update = on_update
        self._depth_limit = depth_limit
        self._running = False
        self._msg_count = 0

        # 本地订单簿：symbol -> {bids: {price: qty}, asks: {price: qty}, last_update_id}
        self._books: dict[str, dict] = {}

    @property
    def msg_count(self) -> int:
        return self._msg_count

    def _build_url(self) -> str:
        streams = [f"{s}@depth@100ms" for s in self._symbols]
        if len(streams) == 1:
            return f"{WS_BASE}/ws/{streams[0]}"
        return f"{WS_BASE}/stream?streams={'/'.join(streams)}"

    async def _init_snapshot(self, symbol: str) -> None:
        """通过 REST API 获取初始订单簿快照"""
        url = f"{REST_BASE}/fapi/v1/depth?symbol={symbol.upper()}&limit={self._depth_limit}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                data = resp.json()
            self._books[symbol] = {
                "bids": {float(b[0]): float(b[1]) for b in data.get("bids", [])},
                "asks": {float(a[0]): float(a[1]) for a in data.get("asks", [])},
                "last_update_id": data.get("lastUpdateId", 0),
            }
            logger.info(f"[depth] {symbol} 初始快照: bids={len(self._books[symbol]['bids'])}, asks={len(self._books[symbol]['asks'])}")
        except Exception as e:
            logger.error(f"[depth] {symbol} 初始快照失败: {e}")
            self._books[symbol] = {"bids": {}, "asks": {}, "last_update_id": 0}

    def _apply_update(self, symbol: str, bids: list, asks: list, final_id: int) -> None:
        """增量合并订单簿更新"""
        book = self._books.get(symbol)
        if not book:
            return
        if final_id <= book["last_update_id"]:
            return  # 旧消息，忽略

        for price_s, qty_s in bids:
            price, qty = float(price_s), float(qty_s)
            if qty == 0:
                book["bids"].pop(price, None)
            else:
                book["bids"][price] = qty

        for price_s, qty_s in asks:
            price, qty = float(price_s), float(qty_s)
            if qty == 0:
                book["asks"].pop(price, None)
            else:
                book["asks"][price] = qty

        book["last_update_id"] = final_id

    def _build_snapshot(self, symbol: str) -> OrderBookSnapshot | None:
        """从本地订单簿构建排序快照"""
        book = self._books.get(symbol)
        if not book:
            return None

        bids_sorted = sorted(book["bids"].items(), key=lambda x: -x[0])[:self._depth_limit]
        asks_sorted = sorted(book["asks"].items(), key=lambda x: x[0])[:self._depth_limit]

        bids = [[p, q] for p, q in bids_sorted]
        asks = [[p, q] for p, q in asks_sorted]

        bid_total = sum(p * q for p, q in bids_sorted)
        ask_total = sum(p * q for p, q in asks_sorted)
        total = bid_total + ask_total
        imbalance = (bid_total - ask_total) / total * 100 if total > 0 else 0.0

        return OrderBookSnapshot(
            timestamp_ms=int(time.time() * 1000),
            bids=bids,
            asks=asks,
            bid_total_usdt=bid_total,
            ask_total_usdt=ask_total,
            imbalance_pct=round(imbalance, 2),
        )

    async def run(self) -> None:
        """启动 WebSocket 连接，断线自动重连"""
        self._running = True
        url = self._build_url()

        # 先获取初始快照
        for sym in self._symbols:
            await self._init_snapshot(sym)

        while self._running:
            try:
                logger.info(f"[depth] 连接 {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"[depth] 已连接，监控 {self._symbols}")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = orjson.loads(raw)
                            data = msg.get("data", msg)
                            await self._handle(data)
                        except Exception as e:
                            logger.warning(f"[depth] 解析失败: {e}")
            except websockets.ConnectionClosed as e:
                logger.warning(f"[depth] 连接关闭: {e}, 5s 后重连")
            except Exception as e:
                logger.error(f"[depth] 连接错误: {e}, 5s 后重连")
            if self._running:
                await asyncio.sleep(5)
                # 重连时重新获取快照
                for sym in self._symbols:
                    await self._init_snapshot(sym)

    async def _handle(self, data: dict) -> None:
        """处理一条 depth 增量消息"""
        symbol = str(data.get("s", "")).lower()
        bids = data.get("b", [])
        asks = data.get("a", [])
        final_id = int(data.get("u", 0))

        self._apply_update(symbol, bids, asks, final_id)
        snapshot = self._build_snapshot(symbol)
        if snapshot:
            self._msg_count += 1
            await self._on_update(symbol.upper(), snapshot)

    def stop(self) -> None:
        self._running = False
