"""
币安 REST 全量数据采集器（中频，5 分钟轮询）
覆盖所有 FlowEdge 需要但 WebSocket 不提供的币安数据：

1. 持仓量 OI（openInterest）
2. 散户多空比（globalLongShortAccountRatio）
3. 大户多空比-账户数（topLongShortAccountRatio）
4. 大户持仓量比（topLongShortPositionRatio）
5. 资金费率历史（fundingRate，最近 10 期）
6. 24h 行情统计（ticker/24hr）
7. K 线数据（klines，1m/5m/15m/1h/4h 五档）

所有请求通过全局 RateLimiter 控制，确保 10+ 币种不超频。
每个币种每轮消耗 ~12 个 API 调用（权重约 20）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List

import httpx

from ..config import cfg
from ..core.rate_limiter import rate_limiters

logger = logging.getLogger("feed.binance_rest")

BINANCE_FAPI = "https://fapi.binance.com"
POLL_INTERVAL = 300   # 5 分钟

# K 线时间框架
KLINE_INTERVALS = ["1m", "5m", "15m", "1h", "4h"]
KLINE_LIMIT = 100  # 每档取最近 100 根


@dataclass
class SymbolRestData:
    """单个币种的 REST 数据快照"""
    symbol: str
    timestamp_ms: int = 0

    # ── 持仓量 ──
    oi_contracts: float = 0.0
    oi_usdt: float = 0.0
    oi_prev_usdt: float = 0.0
    oi_change_pct: float = 0.0

    # ── 散户多空比（账户数比例） ──
    ls_ratio: float = 1.0          # 多/空比值
    long_account_pct: float = 50.0  # 多头账户占比 %
    short_account_pct: float = 50.0

    # ── 大户多空比（账户数） ──
    top_ls_ratio: float = 1.0
    top_long_account_pct: float = 50.0
    top_short_account_pct: float = 50.0

    # ── 大户持仓量比 ──
    top_position_ratio: float = 1.0
    top_long_position_pct: float = 50.0
    top_short_position_pct: float = 50.0

    # ── 资金费率历史（最近 10 期，每 8h 一期） ──
    funding_history: List[dict] = field(default_factory=list)
    funding_avg: float = 0.0
    funding_trend: str = "stable"

    # ── 24h 行情统计 ──
    price: float = 0.0
    price_change_pct: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    volume_24h_usdt: float = 0.0
    trade_count_24h: int = 0

    # ── K 线数据（5 档） ──
    klines: Dict[str, list] = field(default_factory=dict)


class BinanceRestCollector:
    """
    币安 REST 全量数据采集器。
    按币种并行采集，通过 RateLimiter 控制总频率。
    """

    def __init__(self):
        self._running = False
        self._limiter = rate_limiters.get("binance")
        # 缓存：symbol -> 最新数据
        self._data: Dict[str, SymbolRestData] = {}
        self._prev_oi: Dict[str, float] = {}  # OI 变化追踪
        self._poll_count = 0

    @property
    def data(self) -> Dict[str, SymbolRestData]:
        return self._data

    def get(self, symbol: str) -> SymbolRestData:
        """获取单个币种数据"""
        return self._data.get(symbol, SymbolRestData(symbol=symbol))

    async def run(self) -> None:
        """定时轮询循环"""
        self._running = True
        logger.info(f"[BinanceREST] 启动，监控 {len(cfg.WATCH_SYMBOLS)} 个币种，间隔 {POLL_INTERVAL}s")

        while self._running:
            t0 = time.time()
            try:
                await self._collect_all()
                self._poll_count += 1
                elapsed = time.time() - t0
                logger.info(
                    f"[BinanceREST] 第 {self._poll_count} 轮采集完成，"
                    f"{len(cfg.WATCH_SYMBOLS)} 个币种，耗时 {elapsed:.1f}s"
                )
            except Exception as e:
                logger.error(f"[BinanceREST] 采集异常: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _collect_all(self) -> None:
        """并行采集所有币种"""
        tasks = [self._collect_symbol(s) for s in cfg.WATCH_SYMBOLS]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _collect_symbol(self, symbol: str) -> None:
        """采集单个币种的全部 REST 数据"""
        data = SymbolRestData(symbol=symbol, timestamp_ms=int(time.time() * 1000))

        async with httpx.AsyncClient(timeout=15) as client:
            # 并行发起所有请求（每个先获取限速令牌）
            results = await asyncio.gather(
                self._fetch_oi(client, symbol),
                self._fetch_ls_ratio(client, symbol),
                self._fetch_top_ls_ratio(client, symbol),
                self._fetch_top_position_ratio(client, symbol),
                self._fetch_funding_history(client, symbol),
                self._fetch_ticker(client, symbol),
                self._fetch_all_klines(client, symbol),
                return_exceptions=True,
            )

        # 填充数据
        if not isinstance(results[0], Exception) and results[0]:
            oi_contracts, price = results[0]
            oi_usdt = oi_contracts * price
            prev_oi = self._prev_oi.get(symbol, oi_usdt)
            change_pct = ((oi_usdt - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
            data.oi_contracts = oi_contracts
            data.oi_usdt = round(oi_usdt, 2)
            data.oi_prev_usdt = round(prev_oi, 2)
            data.oi_change_pct = round(change_pct, 2)
            data.price = price
            self._prev_oi[symbol] = oi_usdt

        if not isinstance(results[1], Exception) and results[1]:
            data.ls_ratio, data.long_account_pct, data.short_account_pct = results[1]

        if not isinstance(results[2], Exception) and results[2]:
            data.top_ls_ratio, data.top_long_account_pct, data.top_short_account_pct = results[2]

        if not isinstance(results[3], Exception) and results[3]:
            data.top_position_ratio, data.top_long_position_pct, data.top_short_position_pct = results[3]

        if not isinstance(results[4], Exception) and results[4]:
            data.funding_history, data.funding_avg, data.funding_trend = results[4]

        if not isinstance(results[5], Exception) and results[5]:
            ticker = results[5]
            data.price_change_pct = ticker["price_change_pct"]
            data.high_24h = ticker["high"]
            data.low_24h = ticker["low"]
            data.volume_24h_usdt = ticker["volume_usdt"]
            data.trade_count_24h = ticker["count"]
            if data.price == 0:
                data.price = ticker["last_price"]

        if not isinstance(results[6], Exception) and results[6]:
            data.klines = results[6]

        self._data[symbol] = data
        logger.debug(
            f"[BinanceREST] {symbol}: OI=${data.oi_usdt:,.0f}({data.oi_change_pct:+.1f}%) "
            f"多空比={data.ls_ratio:.2f} 大户={data.top_ls_ratio:.2f}"
        )

    # ── 各接口采集方法 ──

    async def _fetch_oi(self, client: httpx.AsyncClient, symbol: str):
        """持仓量 + 当前价格"""
        await self._limiter.acquire(2)
        oi_resp, price_resp = await asyncio.gather(
            client.get(f"{BINANCE_FAPI}/fapi/v1/openInterest", params={"symbol": symbol}),
            client.get(f"{BINANCE_FAPI}/fapi/v1/ticker/price", params={"symbol": symbol}),
        )
        oi = float(oi_resp.json().get("openInterest", 0))
        price = float(price_resp.json().get("price", 0))
        return oi, price

    async def _fetch_ls_ratio(self, client: httpx.AsyncClient, symbol: str):
        """散户多空比（全网账户数）"""
        await self._limiter.acquire(1)
        resp = await client.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "5m", "limit": 1},
        )
        items = resp.json()
        if items and isinstance(items, list):
            item = items[0]
            ratio = float(item.get("longShortRatio", 1))
            long_pct = float(item.get("longAccount", 0.5)) * 100
            short_pct = float(item.get("shortAccount", 0.5)) * 100
            return round(ratio, 4), round(long_pct, 1), round(short_pct, 1)
        return None

    async def _fetch_top_ls_ratio(self, client: httpx.AsyncClient, symbol: str):
        """大户多空比（账户数）"""
        await self._limiter.acquire(1)
        resp = await client.get(
            f"{BINANCE_FAPI}/futures/data/topLongShortAccountRatio",
            params={"symbol": symbol, "period": "5m", "limit": 1},
        )
        items = resp.json()
        if items and isinstance(items, list):
            item = items[0]
            ratio = float(item.get("longShortRatio", 1))
            long_pct = float(item.get("longAccount", 0.5)) * 100
            short_pct = float(item.get("shortAccount", 0.5)) * 100
            return round(ratio, 4), round(long_pct, 1), round(short_pct, 1)
        return None

    async def _fetch_top_position_ratio(self, client: httpx.AsyncClient, symbol: str):
        """大户持仓量比"""
        await self._limiter.acquire(1)
        resp = await client.get(
            f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio",
            params={"symbol": symbol, "period": "5m", "limit": 1},
        )
        items = resp.json()
        if items and isinstance(items, list):
            item = items[0]
            ratio = float(item.get("longShortRatio", 1))
            long_pct = float(item.get("longAccount", 0.5)) * 100
            short_pct = float(item.get("shortAccount", 0.5)) * 100
            return round(ratio, 4), round(long_pct, 1), round(short_pct, 1)
        return None

    async def _fetch_funding_history(self, client: httpx.AsyncClient, symbol: str):
        """资金费率历史（最近 10 期）"""
        await self._limiter.acquire(1)
        resp = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 10},
        )
        items = resp.json()
        if not items or not isinstance(items, list):
            return None

        history = []
        rates = []
        for item in items:
            rate = float(item.get("fundingRate", 0))
            rates.append(rate)
            history.append({
                "time": item.get("fundingTime"),
                "rate": round(rate, 6),
                "rate_pct": round(rate * 100, 4),
            })

        avg = sum(rates) / len(rates) if rates else 0
        # 趋势：比较最近 3 期 vs 之前
        if len(rates) >= 6:
            recent = sum(rates[-3:]) / 3
            older = sum(rates[:3]) / 3
            diff = recent - older
            if diff > 0.0001:
                trend = "rising"
            elif diff < -0.0001:
                trend = "falling"
            else:
                trend = "stable"
        else:
            trend = "stable"

        return history, round(avg, 6), trend

    async def _fetch_ticker(self, client: httpx.AsyncClient, symbol: str):
        """24h 行情统计"""
        await self._limiter.acquire(1)
        resp = await client.get(
            f"{BINANCE_FAPI}/fapi/v1/ticker/24hr",
            params={"symbol": symbol},
        )
        d = resp.json()
        return {
            "last_price": float(d.get("lastPrice", 0)),
            "price_change_pct": round(float(d.get("priceChangePercent", 0)), 2),
            "high": float(d.get("highPrice", 0)),
            "low": float(d.get("lowPrice", 0)),
            "volume_usdt": round(float(d.get("quoteVolume", 0)), 2),
            "count": int(d.get("count", 0)),
        }

    async def _fetch_all_klines(self, client: httpx.AsyncClient, symbol: str):
        """采集 5 档 K 线"""
        klines = {}
        for interval in KLINE_INTERVALS:
            await self._limiter.acquire(1)
            try:
                resp = await client.get(
                    f"{BINANCE_FAPI}/fapi/v1/klines",
                    params={"symbol": symbol, "interval": interval, "limit": KLINE_LIMIT},
                )
                raw = resp.json()
                if raw and isinstance(raw, list):
                    klines[interval] = [
                        {
                            "t": int(k[0]),      # 开盘时间
                            "o": float(k[1]),     # 开盘价
                            "h": float(k[2]),     # 最高价
                            "l": float(k[3]),     # 最低价
                            "c": float(k[4]),     # 收盘价
                            "v": float(k[5]),     # 成交量（币）
                            "qv": float(k[7]),    # 成交额（USDT）
                            "n": int(k[8]),       # 成交笔数
                            "tbv": float(k[9]),   # 主动买入量（币）
                            "tbqv": float(k[10]), # 主动买入额（USDT）
                        }
                        for k in raw
                    ]
            except Exception as e:
                logger.warning(f"[BinanceREST] {symbol} K线 {interval} 采集失败: {e}")
        return klines

    def stop(self) -> None:
        self._running = False
