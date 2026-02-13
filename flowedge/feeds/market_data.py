"""
中频 REST 数据采集器
定时轮询币安 OI + Coinglass 爆仓/OI 分交易所数据（5 分钟间隔）。
复用 KKline intel_collector 的 API 逻辑，简化为 FlowEdge 所需的核心字段。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from ..config import cfg

logger = logging.getLogger("feed.market_data")

BINANCE_REST = "https://fapi.binance.com"
COINGLASS_V4_URL = "https://open-api-v4.coinglass.com/api"


@dataclass
class OIData:
    """持仓量快照"""
    timestamp_ms: int
    symbol: str
    oi_contracts: float     # 持仓量（合约数量）
    oi_usdt: float          # 持仓量（USDT 估值）
    prev_oi_usdt: float     # 上次持仓量
    change_pct: float       # 变化百分比


@dataclass
class CoinglassData:
    """Coinglass 聚合数据"""
    timestamp_ms: int
    # OI 分交易所
    oi_total_usd: float
    oi_change_1h: float
    oi_change_24h: float
    oi_by_exchange: dict    # {exchange: {oi_usd, change_24h}}
    # 清算数据
    liq_long_1h: float      # 1h 多头清算金额
    liq_short_1h: float     # 1h 空头清算金额
    liq_long_24h: float
    liq_short_24h: float
    liq_signal: str


class MarketDataCollector:
    """
    中频市场数据采集器。
    定时轮询（5 分钟），数据缓存供特征引擎读取。
    """

    def __init__(self):
        self._running = False
        # 缓存：symbol -> 最新数据
        self._oi_data: dict[str, OIData] = {}
        self._coinglass_data: dict[str, CoinglassData] = {}
        self._prev_oi: dict[str, float] = {}  # 用于计算变化率

    @property
    def oi_data(self) -> dict:
        return self._oi_data

    @property
    def coinglass_data(self) -> dict:
        return self._coinglass_data

    async def run(self) -> None:
        """定时轮询循环"""
        self._running = True
        logger.info("[MarketData] 启动中频数据采集器")

        while self._running:
            try:
                await self._collect_all()
            except Exception as e:
                logger.error(f"[MarketData] 采集异常: {e}")
            await asyncio.sleep(cfg.REST_POLL_INTERVAL_S)

    async def _collect_all(self) -> None:
        """一次采集所有数据"""
        tasks = []
        for symbol in cfg.WATCH_SYMBOLS:
            tasks.append(self._collect_oi(symbol))
            tasks.append(self._collect_coinglass(symbol))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _collect_oi(self, symbol: str) -> None:
        """采集币安 OI"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{BINANCE_REST}/fapi/v1/openInterest",
                    params={"symbol": symbol},
                )
                data = resp.json()

            oi_contracts = float(data.get("openInterest", 0))

            # 获取当前价格来估算 USDT 价值
            price_resp = await httpx.AsyncClient(timeout=10).__aenter__()
            try:
                pr = await price_resp.get(
                    f"{BINANCE_REST}/fapi/v1/ticker/price",
                    params={"symbol": symbol},
                )
                price = float(pr.json().get("price", 0))
            finally:
                await price_resp.__aexit__(None, None, None)

            oi_usdt = oi_contracts * price
            prev_oi = self._prev_oi.get(symbol, oi_usdt)
            change_pct = ((oi_usdt - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0

            self._oi_data[symbol] = OIData(
                timestamp_ms=int(time.time() * 1000),
                symbol=symbol,
                oi_contracts=oi_contracts,
                oi_usdt=round(oi_usdt, 2),
                prev_oi_usdt=round(prev_oi, 2),
                change_pct=round(change_pct, 2),
            )
            self._prev_oi[symbol] = oi_usdt

            logger.info(f"[OI] {symbol}: ${oi_usdt:,.0f} ({change_pct:+.2f}%)")

        except Exception as e:
            logger.warning(f"[OI] {symbol} 采集失败: {e}")

    async def _collect_coinglass(self, symbol: str) -> None:
        """采集 Coinglass 数据（OI 分交易所 + 清算）"""
        api_key = cfg.COINGLASS_API_KEY
        if not api_key:
            return  # 无 Key 则静默跳过

        base_coin = symbol.replace("USDT", "")
        headers = {"CG-API-KEY": api_key}

        try:
            async with httpx.AsyncClient(headers=headers, timeout=15) as client:
                oi_resp, liq_resp = await asyncio.gather(
                    client.get(
                        f"{COINGLASS_V4_URL}/futures/open-interest/exchange-list",
                        params={"symbol": base_coin},
                    ),
                    client.get(
                        f"{COINGLASS_V4_URL}/futures/liquidation/coin-list",
                        params={"symbol": base_coin},
                    ),
                    return_exceptions=True,
                )

            result = CoinglassData(
                timestamp_ms=int(time.time() * 1000),
                oi_total_usd=0, oi_change_1h=0, oi_change_24h=0,
                oi_by_exchange={},
                liq_long_1h=0, liq_short_1h=0,
                liq_long_24h=0, liq_short_24h=0,
                liq_signal="",
            )

            # 解析 OI 分交易所
            if not isinstance(oi_resp, Exception) and oi_resp.status_code == 200:
                body = oi_resp.json()
                if body.get("code") == "0" and body.get("data"):
                    exchanges = body["data"]
                    all_oi = next((e for e in exchanges if e.get("exchange") == "All"), None)
                    if all_oi:
                        result.oi_total_usd = round(all_oi.get("open_interest_usd", 0))
                        result.oi_change_1h = all_oi.get("open_interest_change_percent_1h", 0)
                        result.oi_change_24h = all_oi.get("open_interest_change_percent_24h", 0)

                    top_ex = sorted(
                        [e for e in exchanges if e.get("exchange") != "All"],
                        key=lambda x: x.get("open_interest_usd", 0), reverse=True,
                    )[:5]
                    result.oi_by_exchange = {
                        e["exchange"]: {
                            "oi_usd": round(e.get("open_interest_usd", 0)),
                            "change_24h": e.get("open_interest_change_percent_24h", 0),
                        }
                        for e in top_ex
                    }

            # 解析清算数据
            if not isinstance(liq_resp, Exception) and liq_resp.status_code == 200:
                body = liq_resp.json()
                if body.get("code") == "0" and body.get("data"):
                    items = body["data"]
                    coin_data = next((c for c in items if c.get("symbol", "").upper() == base_coin.upper()), None)
                    if coin_data:
                        result.liq_long_1h = float(coin_data.get("longLiquidationUsd1h", 0))
                        result.liq_short_1h = float(coin_data.get("shortLiquidationUsd1h", 0))
                        result.liq_long_24h = float(coin_data.get("longLiquidationUsd24h", 0))
                        result.liq_short_24h = float(coin_data.get("shortLiquidationUsd24h", 0))

                        total_1h = result.liq_long_1h + result.liq_short_1h
                        if total_1h > 50_000_000:
                            result.liq_signal = f"1h清算超$50M，清算级联可能发生"
                        elif total_1h > 10_000_000:
                            result.liq_signal = f"1h清算${total_1h/1e6:.0f}M，市场波动加剧"
                        else:
                            result.liq_signal = "清算水平正常"

            self._coinglass_data[symbol] = result
            logger.info(f"[Coinglass] {symbol}: OI=${result.oi_total_usd:,.0f} 清算1h=${(result.liq_long_1h+result.liq_short_1h)/1e6:.1f}M")

        except Exception as e:
            logger.warning(f"[Coinglass] {symbol} 采集失败: {e}")

    def stop(self) -> None:
        self._running = False
