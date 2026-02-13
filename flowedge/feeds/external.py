"""
外部数据源采集器（非币安）
定时轮询，数据缓存供特征引擎读取。

数据源：
1. 恐慌贪婪指数（alternative.me，免费，无 Key）
2. Coinalyze 全网聚合衍生品数据（免费，需 Key，40 次/分钟）
   - 全网聚合 OI、资金费率、爆仓、多空比
3. Coinglass ETF 资金流向（付费，已有 Key）
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

logger = logging.getLogger("feed.external")

COINALYZE_BASE = "https://api.coinalyze.net/v1"
COINGLASS_V3 = "https://open-api-v3.coinglass.com/api"
COINGLASS_V4 = "https://open-api-v4.coinglass.com/api"

# 采集间隔由 cfg.REST_POLL_INTERVAL_S / cfg.EXTERNAL_CHECK_S 控制（高频模式更短）
COINGLASS_ETF_INTERVAL = 3600  # ETF 流向：1 小时（固定，避免超限）


@dataclass
class FearGreedData:
    """恐慌贪婪指数"""
    value: int = 50
    label: str = "Neutral"
    trend: str = "stable"      # "rising" / "falling" / "stable"
    timestamp_ms: int = 0


@dataclass
class CoinalyzeData:
    """Coinalyze 全网聚合数据（单币种）"""
    symbol: str = ""
    timestamp_ms: int = 0
    # 全网聚合 OI
    agg_oi_usd: float = 0.0
    agg_oi_change_pct: float = 0.0
    # 全网聚合资金费率
    agg_funding_rate: float = 0.0
    # 全网爆仓
    agg_liq_buy_usd: float = 0.0   # 多头爆仓（被强制卖出）
    agg_liq_sell_usd: float = 0.0  # 空头爆仓（被强制买入）
    # 全网多空比
    agg_ls_ratio: float = 1.0
    signal: str = ""


@dataclass
class ETFFlowData:
    """BTC ETF 资金流向"""
    total_net_flow_usd: float = 0.0
    daily_flows: List[dict] = field(default_factory=list)  # 最近 5 日
    signal: str = ""
    timestamp_ms: int = 0


class ExternalDataCollector:
    """
    外部数据源统一采集器。
    管理 Fear & Greed、Coinalyze、Coinglass ETF 三个数据源。
    """

    def __init__(self):
        self._running = False
        self._ext_limiter = rate_limiters.get("external")
        self._ca_limiter = rate_limiters.get("coinalyze")
        self._cg_limiter = rate_limiters.get("coinglass")

        # 缓存
        self._fng = FearGreedData()
        self._fng_prev_value = 50
        self._last_fng_time = 0.0

        self._coinalyze: Dict[str, CoinalyzeData] = {}
        self._last_coinalyze_time: Dict[str, float] = {}

        self._etf = ETFFlowData()
        self._last_etf_time = 0.0

    @property
    def fear_greed(self) -> FearGreedData:
        return self._fng

    @property
    def coinalyze_data(self) -> Dict[str, CoinalyzeData]:
        return self._coinalyze

    @property
    def etf_flow(self) -> ETFFlowData:
        return self._etf

    async def run(self) -> None:
        """定时轮询循环"""
        self._running = True
        logger.info("[External] 启动外部数据采集器")

        while self._running:
            try:
                now = time.time()
                tasks = []

                # 恐慌贪婪指数
                if now - self._last_fng_time >= cfg.REST_POLL_INTERVAL_S:
                    tasks.append(self._collect_fng(now))

                # Coinalyze（按币种）
                for symbol in cfg.WATCH_SYMBOLS:
                    last = self._last_coinalyze_time.get(symbol, 0.0)
                    if now - last >= cfg.REST_POLL_INTERVAL_S:
                        tasks.append(self._collect_coinalyze(now, symbol))

                # Coinglass ETF
                if cfg.COINGLASS_API_KEY and now - self._last_etf_time >= COINGLASS_ETF_INTERVAL:
                    tasks.append(self._collect_etf(now))

                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

            except Exception as e:
                logger.error(f"[External] 采集异常: {e}")

            await asyncio.sleep(cfg.EXTERNAL_CHECK_S)  # 检查周期（高频模式 30s，默认 60s）

    # ── 恐慌贪婪指数 ──

    async def _collect_fng(self, now: float) -> None:
        """采集恐慌贪婪指数"""
        await self._ext_limiter.acquire(1)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.alternative.me/fng/",
                    params={"limit": 2, "format": "json"},
                )
                resp.raise_for_status()
                items = resp.json().get("data", [])
                if items:
                    current = items[0]
                    value = int(current["value"])
                    prev_value = int(items[1]["value"]) if len(items) > 1 else value

                    if value > prev_value + 3:
                        trend = "rising"
                    elif value < prev_value - 3:
                        trend = "falling"
                    else:
                        trend = "stable"

                    self._fng = FearGreedData(
                        value=value,
                        label=current["value_classification"],
                        trend=trend,
                        timestamp_ms=int(now * 1000),
                    )
                    self._fng_prev_value = prev_value
                    self._last_fng_time = now
                    logger.info(f"[FNG] 恐慌贪婪={value} ({current['value_classification']}) 趋势={trend}")

        except Exception as e:
            logger.warning(f"[FNG] 采集失败: {e}")

    # ── Coinalyze 全网聚合 ──

    async def _collect_coinalyze(self, now: float, symbol: str) -> None:
        """采集 Coinalyze 全网聚合数据"""
        api_key = cfg.COINALYZE_API_KEY
        if not api_key:
            return

        ca_symbol = f"{symbol}_PERP.A"
        headers = {"api_key": api_key}
        now_ts = int(now)
        from_ts = now_ts - 3600

        try:
            async with httpx.AsyncClient(
                base_url=COINALYZE_BASE, headers=headers, timeout=15
            ) as client:
                await self._ca_limiter.acquire(4)  # 4 个 API 调用
                oi_resp, fr_resp, liq_resp, lsr_resp = await asyncio.gather(
                    client.get("/open-interest", params={
                        "symbols": ca_symbol, "convert_to_usd": "true",
                    }),
                    client.get("/funding-rate", params={"symbols": ca_symbol}),
                    client.get("/liquidation-history", params={
                        "symbols": ca_symbol, "from": from_ts, "to": now_ts,
                        "interval": "hour", "convert_to_usd": "true",
                    }),
                    client.get("/long-short-ratio-history", params={
                        "symbols": ca_symbol, "from": from_ts, "to": now_ts,
                        "interval": "hour",
                    }),
                    return_exceptions=True,
                )

            result = CoinalyzeData(symbol=symbol, timestamp_ms=int(now * 1000))

            # OI
            if not isinstance(oi_resp, Exception) and oi_resp.status_code == 200:
                items = oi_resp.json()
                if items and isinstance(items, list):
                    item = items[0]
                    result.agg_oi_usd = float(item.get("value", 0))

            # 资金费率
            if not isinstance(fr_resp, Exception) and fr_resp.status_code == 200:
                items = fr_resp.json()
                if items and isinstance(items, list):
                    result.agg_funding_rate = float(items[0].get("value", 0))

            # 爆仓
            if not isinstance(liq_resp, Exception) and liq_resp.status_code == 200:
                items = liq_resp.json()
                if items and isinstance(items, list):
                    for entry in items:
                        history = entry.get("history", [])
                        if history:
                            latest = history[-1]
                            result.agg_liq_buy_usd = float(latest.get("l", 0))
                            result.agg_liq_sell_usd = float(latest.get("s", 0))

            # 多空比
            if not isinstance(lsr_resp, Exception) and lsr_resp.status_code == 200:
                items = lsr_resp.json()
                if items and isinstance(items, list):
                    for entry in items:
                        history = entry.get("history", [])
                        if history:
                            latest = history[-1]
                            result.agg_ls_ratio = round(float(latest.get("r", 1)), 4)

            # 信号
            signals = []
            if result.agg_funding_rate > 0.0005:
                signals.append(f"全网费率偏高({result.agg_funding_rate:.4f})")
            elif result.agg_funding_rate < -0.0005:
                signals.append(f"全网费率偏低({result.agg_funding_rate:.4f})")
            total_liq = result.agg_liq_buy_usd + result.agg_liq_sell_usd
            if total_liq > 10_000_000:
                signals.append(f"全网1h爆仓${total_liq/1e6:.1f}M")
            result.signal = "; ".join(signals) if signals else "正常"

            self._coinalyze[symbol] = result
            self._last_coinalyze_time[symbol] = now
            logger.info(
                f"[Coinalyze] {symbol}: OI=${result.agg_oi_usd/1e9:.2f}B "
                f"费率={result.agg_funding_rate:.4f} 多空比={result.agg_ls_ratio}"
            )

        except Exception as e:
            logger.warning(f"[Coinalyze] {symbol} 采集失败: {e}")

    # ── Coinglass ETF 流向 ──

    async def _collect_etf(self, now: float) -> None:
        """采集 BTC ETF 资金流向"""
        api_key = cfg.COINGLASS_API_KEY
        if not api_key:
            return

        try:
            await self._cg_limiter.acquire(1)
            async with httpx.AsyncClient(
                headers={"CG-API-KEY": api_key}, timeout=15
            ) as client:
                resp = await client.get(f"{COINGLASS_V3}/bitcoin/etf/flow-history")

            if resp.status_code == 200:
                body = resp.json()
                if body.get("code") in ("0", 0) and body.get("data"):
                    items = body["data"]
                    # 取最近 5 日
                    recent = items[-5:] if len(items) >= 5 else items
                    daily = []
                    total_flow = 0.0
                    for item in recent:
                        flow = float(item.get("totalNetFlow", 0))
                        total_flow += flow
                        daily.append({
                            "date": item.get("date", ""),
                            "net_flow_usd": round(flow, 2),
                        })

                    # 信号
                    if total_flow > 500_000_000:
                        signal = f"ETF近5日净流入${total_flow/1e6:.0f}M，机构看多"
                    elif total_flow > 0:
                        signal = f"ETF近5日净流入${total_flow/1e6:.0f}M"
                    elif total_flow < -500_000_000:
                        signal = f"ETF近5日净流出${abs(total_flow)/1e6:.0f}M，机构撤资"
                    elif total_flow < 0:
                        signal = f"ETF近5日净流出${abs(total_flow)/1e6:.0f}M"
                    else:
                        signal = "ETF资金流平衡"

                    self._etf = ETFFlowData(
                        total_net_flow_usd=round(total_flow, 2),
                        daily_flows=daily,
                        signal=signal,
                        timestamp_ms=int(now * 1000),
                    )
                    self._last_etf_time = now
                    logger.info(f"[ETF] 近5日净流入=${total_flow/1e6:.0f}M — {signal}")

        except Exception as e:
            logger.warning(f"[ETF] 采集失败: {e}")

    def stop(self) -> None:
        self._running = False
