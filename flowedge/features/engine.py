"""
FeatureEngine — 特征引擎聚合层 v2.1
持有所有 14 个特征计算器，提供统一的快照输出和 SSE 订阅接口。

完整数据流架构（Phase 2 — 全量数据基建）：

实时层（6 条 WebSocket 流）：
  aggTrade     → CVD, VPIN, LargeTrade
  depth@100ms  → OFI, DepthChange
  bookTicker   → BookImbalance
  markPrice@1s → FundingRate, Basis
  forceOrder   → Liquidation（清算级联检测）
  kline@1m     → TrendContext（实时 K 线）

中频层（REST 采集器 x3）：
  BinanceREST  → OI变化, 多空比, 大户比, 资金费率历史, K线(5档), 24h统计
  Coinglass    → 全网OI分交易所, 全网清算, ETF资金流向
  外部         → 恐慌贪婪指数, Coinalyze全网聚合

特征计算器（14 个）：
  1. CVD           2. OFI           3. VPIN
  4. LargeTrade    5. DepthChange   6. FundingRate
  7. Liquidation   8. OI            9. Sentiment
  10. Trend        11. BookImbalance（内嵌）
  12. VWAP         13. VolumeProfile 14. Absorption
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Optional

import orjson

from ..config import cfg
from ..feeds.agg_trade import AggTrade
from ..feeds.depth import OrderBookSnapshot
from ..feeds.book_ticker import BookTick
from ..feeds.mark_price import MarkPriceTick
from ..feeds.force_order import LiquidationEvent
from ..feeds.kline import KlineUpdate
from .cvd import CVDCalculator
from .ofi import OFICalculator
from .vpin import VPINCalculator
from .large_trade import LargeTradeDetector
from .depth_change import DepthChangeDetector
from .funding import FundingRateTracker
from .liquidation import LiquidationTracker
from .oi_tracker import OITracker
from .sentiment import SentimentTracker
from .trend import TrendTracker
from .vwap import VWAPCalculator
from .volume_profile import VolumeProfileCalculator
from .absorption import AbsorptionDetector

logger = logging.getLogger("feature.engine")


class FeatureEngine:
    """
    特征引擎 v2.1：14 个特征计算器 × N 个币种。
    新增做市商逻辑因子：VWAP / Volume Profile / 吸收检测。
    """

    def __init__(self):
        self._symbols = cfg.WATCH_SYMBOLS

        # ── 每个币种一套计算器（11 个） ──
        self._cvd: dict = {}
        self._ofi: dict = {}
        self._vpin: dict = {}
        self._large: dict = {}
        self._depth_change: dict = {}
        self._funding: dict = {}
        self._liquidation: dict = {}
        self._oi: dict = {}
        self._sentiment: dict = {}
        self._trend: dict = {}
        self._vwap: dict = {}
        self._volume_profile: dict = {}
        self._absorption: dict = {}

        # 最新 book ticker
        self._book_ticks: dict = {}

        # SSE 订阅者
        self._subscribers: list = []

        # 统计
        self._start_time = time.time()
        self._counts = {
            "agg_trade": 0, "depth": 0, "book_ticker": 0,
            "mark_price": 0, "force_order": 0, "kline": 0,
        }

        self._init_calculators()

    def _init_calculators(self) -> None:
        """为每个监控币种初始化 11 个特征计算器"""
        for symbol in self._symbols:
            self._cvd[symbol] = CVDCalculator()
            self._ofi[symbol] = OFICalculator()
            self._vpin[symbol] = VPINCalculator(
                bucket_size=cfg.VPIN_BUCKET_SIZE,
                num_buckets=cfg.VPIN_NUM_BUCKETS,
            )
            self._large[symbol] = LargeTradeDetector(
                threshold_usdt=cfg.LARGE_TRADE_THRESHOLD,
            )
            self._depth_change[symbol] = DepthChangeDetector()
            self._funding[symbol] = FundingRateTracker()
            self._liquidation[symbol] = LiquidationTracker()
            self._oi[symbol] = OITracker()
            self._sentiment[symbol] = SentimentTracker()
            self._trend[symbol] = TrendTracker()
            self._vwap[symbol] = VWAPCalculator()
            self._volume_profile[symbol] = VolumeProfileCalculator()
            self._absorption[symbol] = AbsorptionDetector()

        logger.info(
            f"[FeatureEngine] 已初始化 {len(self._symbols)} 个币种 × 14 特征: {self._symbols}"
        )

    # ══════════════════════════════════════════
    # 实时层回调（WebSocket 驱动）
    # ══════════════════════════════════════════

    async def on_agg_trade(self, symbol: str, trade: AggTrade) -> None:
        self._counts["agg_trade"] += 1
        if symbol not in self._cvd:
            return
        self._cvd[symbol].on_trade(trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms)
        self._vpin[symbol].on_trade(trade.qty_usdt, trade.is_taker_buy)
        large_event = self._large[symbol].on_trade(
            trade.price, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )
        if large_event:
            logger.info(
                f"[大单] {symbol} {'买' if large_event.is_taker_buy else '卖'} "
                f"${large_event.qty_usdt:,.0f} @ {trade.price}"
            )
        # 新增三因子：VWAP / Volume Profile / 吸收检测
        self._vwap[symbol].on_trade(trade.price, trade.qty_usdt, trade.timestamp_ms)
        self._volume_profile[symbol].on_trade(trade.price, trade.qty_usdt, trade.timestamp_ms)
        self._absorption[symbol].on_trade(
            trade.price, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )

    async def on_depth_update(self, symbol: str, snapshot: OrderBookSnapshot) -> None:
        self._counts["depth"] += 1
        if symbol not in self._ofi:
            return
        self._ofi[symbol].on_depth_update(snapshot.bids, snapshot.asks, snapshot.timestamp_ms)
        wall_events = self._depth_change[symbol].on_depth_update(
            snapshot.bids, snapshot.asks, snapshot.timestamp_ms
        )
        for wall in wall_events:
            logger.info(
                f"[假墙] {symbol} {wall.side} @ {wall.price} "
                f"${wall.appeared_qty_usdt:,.0f} 存活 {wall.disappeared_ms}ms"
            )

    async def on_book_tick(self, symbol: str, tick: BookTick) -> None:
        self._counts["book_ticker"] += 1
        self._book_ticks[symbol] = tick

    async def on_mark_price(self, symbol: str, tick: MarkPriceTick) -> None:
        self._counts["mark_price"] += 1
        if symbol in self._funding:
            self._funding[symbol].on_mark_price(
                tick.funding_rate, tick.mark_price, tick.index_price,
                tick.next_funding_time_ms, tick.timestamp_ms,
            )

    async def on_liquidation(self, event: LiquidationEvent) -> None:
        self._counts["force_order"] += 1
        symbol = event.symbol
        if symbol in self._liquidation:
            self._liquidation[symbol].on_liquidation(event)
            if event.qty_usdt >= 100_000:
                logger.info(
                    f"[清算] {symbol} {'多' if event.is_long_liq else '空'}头 "
                    f"${event.qty_usdt:,.0f} @ {event.price}"
                )

    async def on_kline(self, symbol: str, update: KlineUpdate) -> None:
        self._counts["kline"] += 1
        if symbol in self._trend:
            self._trend[symbol].on_kline_update(update)

    # ══════════════════════════════════════════
    # 中频层更新（REST 采集器驱动）
    # ══════════════════════════════════════════

    def update_binance_rest(self, symbol: str, rest_data) -> None:
        """从 BinanceRestCollector 同步全量数据"""
        if symbol not in self._oi:
            return
        # OI
        self._oi[symbol].update_binance_oi(rest_data.oi_usdt, rest_data.oi_change_pct)
        # 多空情绪
        self._sentiment[symbol].update_retail(
            rest_data.ls_ratio, rest_data.long_account_pct, rest_data.short_account_pct
        )
        self._sentiment[symbol].update_whale(
            rest_data.top_ls_ratio, rest_data.top_long_account_pct, rest_data.top_short_account_pct
        )
        self._sentiment[symbol].update_whale_position(
            rest_data.top_position_ratio, rest_data.top_long_position_pct, rest_data.top_short_position_pct
        )
        # K 线 → 趋势
        if rest_data.klines:
            self._trend[symbol].update_klines(rest_data.klines)

    def update_coinglass_data(self, symbol: str, cg_data) -> None:
        """从 Coinglass 同步数据"""
        if symbol in self._oi:
            self._oi[symbol].update_coinglass_oi(
                cg_data.oi_total_usd, cg_data.oi_change_1h,
                cg_data.oi_change_24h, cg_data.oi_by_exchange,
            )
        if symbol in self._liquidation:
            self._liquidation[symbol].update_coinglass(
                cg_data.liq_long_1h, cg_data.liq_short_1h,
            )

    def update_external(self, symbol: str, fng_data, coinalyze_data) -> None:
        """从 ExternalDataCollector 同步数据"""
        if symbol in self._sentiment:
            if fng_data:
                self._sentiment[symbol].update_fear_greed(
                    fng_data.value, fng_data.label, fng_data.trend,
                )
            if coinalyze_data:
                self._sentiment[symbol].update_global_ls(coinalyze_data.agg_ls_ratio)

    # ══════════════════════════════════════════
    # 输出层
    # ══════════════════════════════════════════

    def get_snapshot(self, symbol: Optional[str] = None) -> dict:
        """获取所有特征快照。symbol=None 返回全部币种。"""
        symbols = [symbol] if symbol else self._symbols
        result = {}

        for sym in symbols:
            if sym not in self._cvd:
                continue

            features = {
                "symbol": sym,
                "timestamp": int(time.time() * 1000),
            }

            # 1. CVD
            features["cvd"] = asdict(self._cvd[sym].snapshot())
            # 2. OFI
            features["ofi"] = asdict(self._ofi[sym].snapshot())
            # 3. VPIN
            features["vpin"] = asdict(self._vpin[sym].snapshot())
            # 4. 大单
            large_snap = self._large[sym].snapshot()
            large_dict = asdict(large_snap)
            if large_dict.get("last_large"):
                large_dict["last_large"] = asdict(large_snap.last_large)
            features["large_trade"] = large_dict
            # 5. 深度变化
            depth_snap = self._depth_change[sym].snapshot()
            features["depth_change"] = {
                "bid_change_rate": depth_snap.bid_change_rate,
                "ask_change_rate": depth_snap.ask_change_rate,
                "bid_depth_usdt": depth_snap.bid_depth_usdt,
                "ask_depth_usdt": depth_snap.ask_depth_usdt,
                "depth_imbalance": depth_snap.depth_imbalance,
                "wall_events_30s": depth_snap.wall_events_30s,
                "recent_walls": [asdict(w) for w in depth_snap.recent_walls],
            }
            # 6. Book ticker
            tick = self._book_ticks.get(sym)
            if tick:
                features["book"] = {
                    "bid_price": tick.bid_price,
                    "bid_qty": tick.bid_qty,
                    "ask_price": tick.ask_price,
                    "ask_qty": tick.ask_qty,
                    "spread_pct": tick.spread_pct,
                    "mid_price": tick.mid_price,
                    "book_imbalance_l1": round(
                        (tick.bid_qty - tick.ask_qty) / (tick.bid_qty + tick.ask_qty) * 100
                        if (tick.bid_qty + tick.ask_qty) > 0 else 0, 2
                    ),
                }
            else:
                features["book"] = None
            # 7. 资金费率
            features["funding"] = asdict(self._funding[sym].snapshot())
            # 8. 清算
            features["liquidation"] = asdict(self._liquidation[sym].snapshot())
            # 9. 持仓量
            features["open_interest"] = asdict(self._oi[sym].snapshot())
            # 10. 多空情绪
            features["sentiment"] = asdict(self._sentiment[sym].snapshot())
            # 11. 趋势上下文
            features["trend"] = asdict(self._trend[sym].snapshot())
            # 12. VWAP
            features["vwap"] = asdict(self._vwap[sym].snapshot())
            # 13. Volume Profile
            features["volume_profile"] = asdict(self._volume_profile[sym].snapshot())
            # 14. 吸收检测
            features["absorption"] = asdict(self._absorption[sym].snapshot())

            result[sym] = features

        return result

    def get_status(self) -> dict:
        """系统状态"""
        uptime = time.time() - self._start_time
        total = sum(self._counts.values())
        return {
            "version": "2.0",
            "uptime_s": round(uptime, 1),
            "symbols": self._symbols,
            "symbol_count": len(self._symbols),
            "feature_count": 14,
            "feeds": dict(self._counts),
            "total_messages": total,
            "msg_rate_approx": round(total / max(uptime, 1), 1),
            "data_sources": {
                "ws_streams": 6,
                "rest_binance": True,
                "coinglass": bool(cfg.COINGLASS_API_KEY),
                "coinalyze": bool(cfg.COINALYZE_API_KEY),
                "fear_greed": True,
            },
            "subscribers": len(self._subscribers),
        }

    # ══════════════════════════════════════════
    # SSE 订阅
    # ══════════════════════════════════════════

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        logger.info(f"[SSE] 新订阅者，当前 {len(self._subscribers)} 个")
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)
            logger.info(f"[SSE] 订阅者断开，当前 {len(self._subscribers)} 个")

    async def broadcast_loop(self, interval_ms: int = 200) -> None:
        while True:
            if self._subscribers:
                snapshot = self.get_snapshot()
                data = orjson.dumps(snapshot).decode()
                dead = []
                for q in self._subscribers:
                    try:
                        q.put_nowait(data)
                    except asyncio.QueueFull:
                        dead.append(q)
                for q in dead:
                    self.unsubscribe(q)
            await asyncio.sleep(interval_ms / 1000)
