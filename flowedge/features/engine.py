"""
FeatureEngine — 特征引擎聚合层 v3.0
持有所有 17 个特征计算器，提供统一的快照输出和 SSE 订阅接口。

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

特征计算器（17 个）：
  1. CVD           2. OFI           3. VPIN
  4. LargeTrade    5. DepthChange   6. FundingRate
  7. Liquidation   8. OI            9. Sentiment
  10. Trend        11. BookImbalance（内嵌）
  12. VWAP         13. VolumeProfile 14. Absorption
  15. Tape（逐笔缓冲）  16. Footprint（价位分桶）  17. IcebergDetector（冰山单推量）
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
from .tape_buffer import TapeBuffer
from .footprint import FootprintAggregator
from .iceberg_detector import IcebergDetector

logger = logging.getLogger("feature.engine")


class FeatureEngine:
    """
    特征引擎 v3.0：17 个特征计算器 × N 个币种。
    新增可视化数据层：Tape / Footprint / 冰山单检测。
    """

    def __init__(self, registry=None):
        self._symbols = cfg.WATCH_SYMBOLS
        self._registry = registry

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

        # ── 可视化数据层（Tape / Footprint / 冰山单） ──
        self._tape: dict = {}
        self._footprint: dict = {}
        self._iceberg: dict = {}

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
        """为每个监控币种初始化 17 个特征计算器，参数从 Registry 读取"""
        reg = self._registry
        # 从 Registry 读取特征参数（如有），否则用 config.py 兜底（仅限非优化参数）
        if reg:
            vpin_bucket = reg.get("feat_vpin_bucket_size")
            vpin_buckets = int(reg.get("feat_vpin_num_buckets"))
            lt_threshold = reg.get("feat_large_trade_threshold")
            lt_window = int(reg.get("feat_large_trade_window_ms"))
            ofi_levels = int(reg.get("feat_ofi_levels"))
            ofi_capacity = int(reg.get("feat_ofi_capacity"))
            cvd_capacity = int(reg.get("feat_cvd_capacity"))
            liq_window = int(reg.get("feat_liq_window_5m_ms"))
            funding_history = int(reg.get("feat_funding_history_size"))
        else:
            vpin_bucket = cfg.VPIN_BUCKET_SIZE
            vpin_buckets = cfg.VPIN_NUM_BUCKETS
            lt_threshold = cfg.LARGE_TRADE_THRESHOLD
            lt_window = 30000
            ofi_levels = 5
            ofi_capacity = 90000
            cvd_capacity = 60000
            liq_window = 300000
            funding_history = 300

        for symbol in self._symbols:
            self._cvd[symbol] = CVDCalculator(capacity=cvd_capacity)
            self._ofi[symbol] = OFICalculator(levels=ofi_levels, capacity=ofi_capacity)
            self._vpin[symbol] = VPINCalculator(
                bucket_size=vpin_bucket,
                num_buckets=vpin_buckets,
            )
            self._large[symbol] = LargeTradeDetector(
                threshold_usdt=lt_threshold,
                window_ms=lt_window,
            )
            self._depth_change[symbol] = DepthChangeDetector()
            self._funding[symbol] = FundingRateTracker(history_size=funding_history)
            self._liquidation[symbol] = LiquidationTracker(window_5m=liq_window)
            self._oi[symbol] = OITracker()
            self._sentiment[symbol] = SentimentTracker()
            self._trend[symbol] = TrendTracker()
            self._vwap[symbol] = VWAPCalculator()
            self._volume_profile[symbol] = VolumeProfileCalculator()
            self._absorption[symbol] = AbsorptionDetector()
            # 可视化数据层
            self._tape[symbol] = TapeBuffer(
                large_threshold_usdt=lt_threshold,
            )
            self._footprint[symbol] = FootprintAggregator()
            self._iceberg[symbol] = IcebergDetector(
                large_threshold_usdt=cfg.LARGE_TRADE_THRESHOLD,
            )

        logger.info(
            f"[FeatureEngine] 已初始化 {len(self._symbols)} 个币种 × 17 特征: {self._symbols}"
        )

    # ══════════════════════════════════════════
    # 实时层回调（WebSocket 驱动）
    # ══════════════════════════════════════════

    async def on_agg_trade(self, symbol: str, trade: AggTrade) -> None:
        self._counts["agg_trade"] += 1
        if symbol not in self._cvd:
            return
        self._cvd[symbol].on_trade(trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms, trade.price)
        self._vpin[symbol].on_trade(trade.qty_usdt, trade.is_taker_buy)
        large_event = self._large[symbol].on_trade(
            trade.price, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )
        if large_event:
            logger.info(
                f"[大单] {symbol} {'买' if large_event.is_taker_buy else '卖'} "
                f"${large_event.qty_usdt:,.0f} @ {trade.price}"
            )
        # VWAP / Volume Profile / 吸收检测
        self._vwap[symbol].on_trade(trade.price, trade.qty_usdt, trade.timestamp_ms)
        self._volume_profile[symbol].on_trade(trade.price, trade.qty_usdt, trade.timestamp_ms)
        self._absorption[symbol].on_trade(
            trade.price, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )
        # 可视化数据层：Tape / Footprint / 冰山单
        self._tape[symbol].on_trade(
            trade.price, trade.qty, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )
        self._footprint[symbol].on_trade(
            trade.price, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )
        iceberg = self._iceberg[symbol].on_trade(
            trade.price, trade.qty_usdt, trade.is_taker_buy, trade.timestamp_ms
        )
        if iceberg:
            logger.info(
                f"[冰山单] {symbol} {'买' if iceberg.is_taker_buy else '卖'} "
                f"${iceberg.total_qty_usdt:,.0f} ({iceberg.trade_count}笔 {iceberg.pattern}) "
                f"置信度 {iceberg.confidence:.0%}"
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
            # 15. 冰山单检测（轻量摘要，不含完整 trades）
            ice_snap = self._iceberg[sym].snapshot()
            features["iceberg"] = {
                "buy_hidden_usdt": ice_snap.buy_hidden_usdt,
                "sell_hidden_usdt": ice_snap.sell_hidden_usdt,
                "net_hidden_usdt": ice_snap.net_hidden_usdt,
                "cluster_count_60s": ice_snap.cluster_count_60s,
                "active_count": len(ice_snap.active_clusters),
            }

            result[sym] = features

        return result

    # ══════════════════════════════════════════
    # 可视化数据端点（Tape / DOM / Footprint）
    # ══════════════════════════════════════════

    def get_tape(self, symbol: str) -> dict | None:
        """获取 Tape 逐笔成交数据"""
        buf = self._tape.get(symbol)
        if not buf:
            return None
        snap = buf.snapshot()
        return {
            "trades": snap.trades,
            "stats_10s": snap.stats_10s,
            "stats_60s": snap.stats_60s,
        }

    def get_dom(self, symbol: str) -> dict | None:
        """获取 DOM（订单簿深度）热力图数据"""
        dc = self._depth_change.get(symbol)
        tick = self._book_ticks.get(symbol)
        if not dc or not tick:
            return None

        snap = dc.snapshot()
        bids = [
            {"price": lv.price, "qty": lv.qty, "usdt": lv.qty_usdt}
            for lv in (snap.bid_levels or [])
        ]
        asks = [
            {"price": lv.price, "qty": lv.qty, "usdt": lv.qty_usdt}
            for lv in (snap.ask_levels or [])
        ]
        return {
            "bid_price": tick.bid_price,
            "ask_price": tick.ask_price,
            "mid_price": tick.mid_price,
            "spread_pct": tick.spread_pct,
            "bid_depth_usdt": snap.bid_depth_usdt,
            "ask_depth_usdt": snap.ask_depth_usdt,
            "imbalance": snap.depth_imbalance,
            "bids": bids,
            "asks": asks,
            "wall_events": snap.wall_events_30s,
            "fake_walls": [asdict(w) for w in snap.recent_walls],
        }

    def get_footprint(self, symbol: str) -> dict | None:
        """获取 Footprint Chart 数据"""
        fp = self._footprint.get(symbol)
        if not fp:
            return None
        snap = fp.snapshot()
        return {
            "current_bar": snap.current_bar,
            "recent_bars": snap.recent_bars,
            "tick_size": snap.tick_size,
        }

    def get_iceberg(self, symbol: str) -> dict | None:
        """获取冰山单检测完整数据"""
        det = self._iceberg.get(symbol)
        if not det:
            return None
        snap = det.snapshot()
        return {
            "active_clusters": [
                {
                    "start_ms": c.start_ms,
                    "end_ms": c.end_ms,
                    "price_avg": c.price_avg,
                    "total_qty_usdt": c.total_qty_usdt,
                    "trade_count": c.trade_count,
                    "side": "BUY" if c.is_taker_buy else "SELL",
                    "pattern": c.pattern,
                    "confidence": c.confidence,
                }
                for c in snap.active_clusters
            ],
            "recent_clusters": [
                {
                    "start_ms": c.start_ms,
                    "end_ms": c.end_ms,
                    "price_avg": c.price_avg,
                    "total_qty_usdt": c.total_qty_usdt,
                    "trade_count": c.trade_count,
                    "side": "BUY" if c.is_taker_buy else "SELL",
                    "pattern": c.pattern,
                    "confidence": c.confidence,
                }
                for c in snap.recent_clusters
            ],
            "buy_hidden_usdt": snap.buy_hidden_usdt,
            "sell_hidden_usdt": snap.sell_hidden_usdt,
            "net_hidden_usdt": snap.net_hidden_usdt,
            "cluster_count_60s": snap.cluster_count_60s,
        }

    def get_orderflow_snapshot(self, symbol: str) -> dict | None:
        """获取完整的订单流可视化数据（Tape + DOM + Footprint + 冰山单）"""
        symbol = symbol.upper()
        if symbol not in self._cvd:
            return None
        return {
            "symbol": symbol,
            "timestamp": int(time.time() * 1000),
            "tape": self.get_tape(symbol),
            "footprint": self.get_footprint(symbol),
            "iceberg": self.get_iceberg(symbol),
        }

    def get_status(self) -> dict:
        """系统状态"""
        uptime = time.time() - self._start_time
        total = sum(self._counts.values())
        return {
            "version": "3.0",
            "uptime_s": round(uptime, 1),
            "symbols": self._symbols,
            "symbol_count": len(self._symbols),
            "feature_count": 17,
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
