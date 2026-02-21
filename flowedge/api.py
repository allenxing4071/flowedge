"""
FlowEdge API 层 v3.0
提供健康检查、特征快照、SSE 实时流、多因子信号、异常检测、
KKline 对接桥、系统状态等端点。

数据流架构：
  6 条 WS 流 + 3 个 REST 采集器
       ↓
  17 个特征计算器 (FeatureEngine)
       ↓
  多因子评分 + 异常检测 (SignalEngine)
       ↓
  API / SSE / KKline Bridge
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import orjson
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .features.engine import FeatureEngine
from .feeds.agg_trade import AggTradeStream
from .feeds.depth import DepthStream
from .feeds.book_ticker import BookTickerStream
from .feeds.mark_price import MarkPriceStream
from .feeds.force_order import ForceOrderStream
from .feeds.kline import KlineStream
from .feeds.binance_rest import BinanceRestCollector
from .feeds.market_data import MarketDataCollector
from .feeds.external import ExternalDataCollector
from .core.rate_limiter import rate_limiters
from .signals.engine import SignalEngine
from .paper_trader import PaperTrader
from .optimizer.param_registry import ParamRegistry
from .optimizer.data_manager import DataManager
from .optimizer.api import router as optimizer_router, init_optimizer_api, get_scheduler, get_agent
from .config import cfg

logger = logging.getLogger("flowedge.api")

# ── 全局实例 ──
# 参数注册中心（所有可优化参数的唯一数据源）
param_registry = ParamRegistry(data_dir="data/optimizer")
data_manager = DataManager(db_path="data/signal_tracker.db")

engine = FeatureEngine(registry=param_registry)
signal_engine = SignalEngine(registry=param_registry)
paper_trader = PaperTrader(registry=param_registry)
binance_rest = BinanceRestCollector()
market_data = MarketDataCollector()
external_data = ExternalDataCollector()

_feeds = []
_tasks: list = []


async def _data_sync_loop():
    """
    定期将 REST 采集器缓存同步到 FeatureEngine。
    间隔由 cfg.DATA_SYNC_LOOP_S 控制（高频模式 5s，默认 10s）。
    """
    while True:
        await asyncio.sleep(cfg.DATA_SYNC_LOOP_S)
        for symbol in cfg.WATCH_SYMBOLS:
            # 币安 REST 全量数据
            rest = binance_rest.get(symbol)
            if rest.timestamp_ms > 0:
                engine.update_binance_rest(symbol, rest)

            # Coinglass OI + 清算
            cg = market_data.coinglass_data.get(symbol)
            if cg:
                engine.update_coinglass_data(symbol, cg)

            # 外部数据（恐慌贪婪 + Coinalyze）
            fng = external_data.fear_greed
            ca = external_data.coinalyze_data.get(symbol)
            engine.update_external(symbol, fng, ca)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动全部数据流和采集器"""

    # 验证配置
    errors = cfg.validate()
    if errors:
        for e in errors:
            logger.error(f"配置错误: {e}")
        logger.warning("配置不完整，将以演示模式运行（无实时数据）")
        yield
        return

    cfg.ensure_dirs()

    # ── 实时层：6 条 WebSocket 流 ──
    agg_trade = AggTradeStream(cfg.WATCH_SYMBOLS, engine.on_agg_trade)
    depth = DepthStream(cfg.WATCH_SYMBOLS, engine.on_depth_update)
    book_ticker = BookTickerStream(cfg.WATCH_SYMBOLS, engine.on_book_tick)
    mark_price = MarkPriceStream(cfg.WATCH_SYMBOLS, engine.on_mark_price)
    force_order = ForceOrderStream(engine.on_liquidation, watch_symbols=cfg.WATCH_SYMBOLS)
    kline = KlineStream(cfg.WATCH_SYMBOLS, engine.on_kline)
    _feeds.extend([agg_trade, depth, book_ticker, mark_price, force_order, kline])

    # 启动所有任务
    _tasks.append(asyncio.create_task(agg_trade.run()))
    _tasks.append(asyncio.create_task(depth.run()))
    _tasks.append(asyncio.create_task(book_ticker.run()))
    _tasks.append(asyncio.create_task(mark_price.run()))
    _tasks.append(asyncio.create_task(force_order.run()))
    _tasks.append(asyncio.create_task(kline.run()))

    # ── 中频层：3 个 REST 采集器 ──
    _tasks.append(asyncio.create_task(binance_rest.run()))
    _tasks.append(asyncio.create_task(market_data.run()))
    _tasks.append(asyncio.create_task(external_data.run()))

    # ── 数据同步 + SSE 广播 + 信号评估（间隔由 cfg 控制，高频模式更短） ──
    _tasks.append(asyncio.create_task(_data_sync_loop()))
    _tasks.append(asyncio.create_task(
        engine.broadcast_loop(interval_ms=cfg.BROADCAST_INTERVAL_MS)
    ))
    _tasks.append(asyncio.create_task(
        signal_engine.evaluation_loop(engine, interval_ms=cfg.SIGNAL_EVAL_INTERVAL_MS)
    ))
    _tasks.append(asyncio.create_task(
        signal_engine.tracker.tracking_loop(interval_s=cfg.TRACKER_LOOP_S)
    ))

    # ── 纸盘交易：注入引擎 + 启动资金曲线录制 ──
    signal_engine.paper_trader = paper_trader
    _tasks.append(asyncio.create_task(paper_trader.equity_loop(interval_s=cfg.EQUITY_LOOP_S)))

    # ── 自动优化调度器：样本驱动，数据够了立刻触发优化 ──
    scheduler = get_scheduler()
    if scheduler:
        await scheduler.start_background()
        logger.info("[FlowEdge] 优化调度器已启动（样本驱动模式）")

    # ── Agent 总控：定时自动触发进化决策 ──
    agent = get_agent()
    if agent:
        async def _agent_auto_loop():
            """Agent 自动循环：每 6 小时执行一次 plan + run（早期阶段更频繁）"""
            INTERVAL_S = 6 * 3600  # 每 6 小时一次
            # 首次等待 10 分钟，让数据先积累一些
            await asyncio.sleep(600)
            while True:
                try:
                    plan = agent.plan(goal="auto_optimize")
                    action = plan.get("action", "hold")
                    logger.info(f"[Agent] 自动计划: action={action}, reason={plan.get('reason', '')}")
                    if action != "hold":
                        result = agent.run_once(goal="auto_optimize", dry_run=False)
                        logger.info(f"[Agent] 执行完成: {result.get('status', 'unknown')}")
                except Exception as e:
                    logger.error(f"[Agent] 自动循环异常: {e}")
                await asyncio.sleep(INTERVAL_S)

        _tasks.append(asyncio.create_task(_agent_auto_loop()))
        logger.info("[FlowEdge] Agent 总控已启动（每 6h 自动决策）")

    # 数据源状态摘要
    sources = []
    sources.append(f"6 WS 流")
    sources.append(f"币安REST({len(cfg.WATCH_SYMBOLS)}币种)")
    if cfg.COINGLASS_API_KEY:
        sources.append("Coinglass")
    if cfg.COINALYZE_API_KEY:
        sources.append("Coinalyze")
    sources.append("FearGreed")

    mode_note = ""
    if cfg.NANGE_MODE:
        mode_note = " | 南哥打法(高频+跟随+判断方向)"
    if bool(int(param_registry.get("gate_skip_behavior_layer"))):
        mode_note += " L3跳过"
    logger.info(
        f"[FlowEdge v3.0] 已启动 — "
        f"{' + '.join(sources)} | "
        f"监控 {cfg.WATCH_SYMBOLS} | "
        f"17 特征计算器 + 信号引擎 + 订单流可视化"
        + (f" | 高频模式(broadcast={cfg.BROADCAST_INTERVAL_MS}ms eval={cfg.SIGNAL_EVAL_INTERVAL_MS}ms)" if cfg.HIGH_FREQ_MODE else "")
        + mode_note
    )

    yield

    # 关闭
    logger.info("[FlowEdge] 正在关闭...")
    # 停止优化调度器后台任务
    scheduler = get_scheduler()
    if scheduler:
        await scheduler.stop_background()
    for feed in _feeds:
        feed.stop()
    binance_rest.stop()
    market_data.stop()
    external_data.stop()
    for t in _tasks:
        t.cancel()
    logger.info("[FlowEdge] 已关闭")


# ── FastAPI 应用 ──
app = FastAPI(
    title="FlowEdge",
    description="订单流驱动的量化特征引擎 v3.0 — 特征 + 信号 + 对接",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS — 允许前端驾驶舱跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 优化系统 API（注入 paper_trader，进化应用新参数时自动重置纸盘）──
init_optimizer_api(param_registry, data_manager, paper_trader=paper_trader)
app.include_router(optimizer_router)


class OrjsonResponse(JSONResponse):
    media_type = "application/json"
    def render(self, content) -> bytes:
        return orjson.dumps(content)


# ── 端点 ──

@app.get("/health", response_class=OrjsonResponse)
async def health():
    return {"status": "ok", "service": "FlowEdge", "version": "3.0.0"}


@app.get("/features/snapshot", response_class=OrjsonResponse)
async def features_snapshot(
    symbol: Optional[str] = Query(None, description="币种（如 BTCUSDT）")
):
    """获取当前所有特征的快照（含冰山单摘要）"""
    return engine.get_snapshot(symbol=symbol)


@app.get("/features/stream")
async def features_stream(
    symbol: Optional[str] = Query(None, description="币种过滤")
):
    """SSE 实时特征流，每 200ms 推送一次"""
    queue = engine.subscribe()

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                if symbol:
                    try:
                        parsed = orjson.loads(data)
                        if symbol in parsed:
                            data = orjson.dumps({symbol: parsed[symbol]}).decode()
                        else:
                            continue
                    except Exception:
                        pass
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            engine.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/status", response_class=OrjsonResponse)
async def status():
    """系统运行状态（含数据源、特征数、消息速率）"""
    return engine.get_status()


@app.get("/rate-limits", response_class=OrjsonResponse)
async def rate_limit_status():
    """各 API 源速率限制器状态"""
    return rate_limiters.stats()


# ══════════════════════════════════════════
# 信号层端点
# ══════════════════════════════════════════

@app.get("/signals", response_class=OrjsonResponse)
async def signals_all():
    """获取所有币种的当前信号（综合评分 + 因子明细 + 异常）"""
    return signal_engine.get_all_signals()


@app.get("/signals/{symbol}", response_class=OrjsonResponse)
async def signals_symbol(symbol: str):
    """获取单个币种的当前信号"""
    symbol = symbol.upper()
    result = signal_engine.get_signal(symbol)
    if not result:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol} 的信号数据"},
            status_code=404,
        )
    return result


@app.get("/signals/history/{symbol}", response_class=OrjsonResponse)
async def signals_history(
    symbol: str,
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
):
    """获取单币种的信号历史"""
    return signal_engine.get_history(symbol=symbol.upper(), limit=limit)


@app.get("/signals/performance", response_class=OrjsonResponse)
async def signals_performance(
    symbol: str = Query(None, description="按币种筛选（可选）"),
):
    """信号胜率统计 — 回答'信号到底准不准'的核心数据"""
    return signal_engine.tracker.get_performance(
        symbol=symbol.upper() if symbol else None
    )


@app.get("/pusher/status", response_class=OrjsonResponse)
async def pusher_status():
    """半自动推送器状态 — 查看推送配置、统计、最近推送记录"""
    return signal_engine.pusher.get_status()


@app.post("/pusher/config", response_class=OrjsonResponse)
async def pusher_config(updates: dict):
    """更新半自动推送器配置"""
    return signal_engine.pusher.update_config(updates)


@app.get("/signals/stream/all")
async def signals_stream():
    """SSE 信号变化流 — 仅推送信号变化事件"""
    queue = signal_engine.subscribe_signals()

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            signal_engine.unsubscribe_signals(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/gate/status", response_class=OrjsonResponse)
async def gate_status():
    """获取所有币种的门卫状态（四层门卫过滤结果）"""
    return signal_engine.get_gate_status()


@app.get("/quality-board", response_class=OrjsonResponse)
async def quality_board():
    """
    质量看板 — 门卫框架的健康诊断面板。
    包含：漏斗转化、方向分布、拒绝原因、止损对比、交易表现。
    """
    return signal_engine.get_quality_board()


@app.get("/dashboard", response_class=OrjsonResponse)
async def dashboard():
    """
    交易驾驶舱仪表盘数据。
    一次调用返回所有币种的信号概览 + 全局统计。
    专为前端 UI 优化的聚合端点。
    """
    return signal_engine.get_dashboard()


# ══════════════════════════════════════════
# 订单流可视化端点（Tape / DOM / Footprint / 冰山单）
# ══════════════════════════════════════════

@app.get("/orderflow/{symbol}", response_class=OrjsonResponse)
async def orderflow_snapshot(symbol: str):
    """完整订单流可视化数据（Tape + Footprint + 冰山单）"""
    result = engine.get_orderflow_snapshot(symbol)
    if not result:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol.upper()} 的数据"},
            status_code=404,
        )
    return result


@app.get("/orderflow/{symbol}/tape", response_class=OrjsonResponse)
async def orderflow_tape(symbol: str):
    """Tape 逐笔成交流（最近 200 笔 + 实时统计）"""
    result = engine.get_tape(symbol.upper())
    if not result:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol.upper()} 的 Tape 数据"},
            status_code=404,
        )
    return result


@app.get("/orderflow/{symbol}/dom", response_class=OrjsonResponse)
async def orderflow_dom(symbol: str):
    """DOM 订单簿深度热力图（20 档买卖 + 假墙检测）"""
    result = engine.get_dom(symbol.upper())
    if not result:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol.upper()} 的 DOM 数据"},
            status_code=404,
        )
    return result


@app.get("/orderflow/{symbol}/footprint", response_class=OrjsonResponse)
async def orderflow_footprint(symbol: str):
    """Footprint Chart 数据（1 分钟 K 线 × 价格档位买卖量）"""
    result = engine.get_footprint(symbol.upper())
    if not result:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol.upper()} 的 Footprint 数据"},
            status_code=404,
        )
    return result


@app.get("/orderflow/{symbol}/iceberg", response_class=OrjsonResponse)
async def orderflow_iceberg(symbol: str):
    """冰山单 / 单一 ID 推量检测（拆单模式识别）"""
    result = engine.get_iceberg(symbol.upper())
    if not result:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol.upper()} 的冰山单数据"},
            status_code=404,
        )
    return result


@app.get("/orderflow/{symbol}/stream")
async def orderflow_stream(symbol: str):
    """SSE 订单流实时推送（Tape + DOM + Footprint + 冰山单，500ms 间隔）"""
    symbol = symbol.upper()

    async def event_generator():
        try:
            while True:
                data = engine.get_orderflow_snapshot(symbol)
                if data:
                    # 同时附加 DOM 数据
                    data["dom"] = engine.get_dom(symbol)
                    yield f"data: {orjson.dumps(data).decode()}\n\n"
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ══════════════════════════════════════════
# KKline 对接层端点（Phase 3 Bridge）
# ══════════════════════════════════════════

@app.get("/bridge/kkline/{symbol}", response_class=OrjsonResponse)
async def bridge_kkline(symbol: str):
    """
    KKline 兼容情报格式（增强版）。
    KKline 可定期拉取此端点，注入其 DeepSeek 分析器的上下文中，
    让 AI 决策获得 FlowEdge 的微观结构+信号数据+优化系统校准信息。
    """
    symbol = symbol.upper()
    snapshot = engine.get_snapshot(symbol=symbol)
    features = snapshot.get(symbol)
    if not features:
        return OrjsonResponse(
            content={"error": f"未找到 {symbol} 的数据"},
            status_code=404,
        )
    intel = signal_engine.get_kkline_intel(symbol, features)

    # 注入优化系统校准信息
    try:
        calibration = {
            "optimizer_active": param_registry is not None,
            "current_regime": "unknown",
            "param_version": "default",
        }
        if param_registry:
            stats = param_registry.stats()
            calibration["param_version"] = stats.get("snapshots_count", 0)
            calibration["total_params"] = stats.get("total_params", 0)

        intel["optimization_calibration"] = calibration
    except Exception:
        pass  # 优化系统信息为增强项，不影响核心数据

    return intel


# ══════════════════════════════════════════
# 纸盘交易端点（PaperTrader）
# ══════════════════════════════════════════

@app.get("/paper/status", response_class=OrjsonResponse)
async def paper_status():
    """纸盘交易完整状态 — 账户、持仓、统计"""
    return paper_trader.get_status()


@app.get("/paper/trades", response_class=OrjsonResponse)
async def paper_trades(
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
):
    """纸盘交易历史记录"""
    return paper_trader.get_trades(limit=limit)


@app.get("/paper/equity", response_class=OrjsonResponse)
async def paper_equity(
    limit: int = Query(1440, ge=1, le=10000, description="数据点数"),
):
    """纸盘资金曲线"""
    return paper_trader.get_equity_curve(limit=limit)


@app.post("/paper/config", response_class=OrjsonResponse)
async def paper_config(updates: dict):
    """更新纸盘交易配置"""
    return paper_trader.update_config(updates)


@app.get("/paper/signal-log", response_class=OrjsonResponse)
async def paper_signal_log(
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
):
    """纸盘信号决策日志 — 记录每次信号变化的决策过程"""
    return paper_trader.get_signal_log(limit=limit)


@app.post("/paper/reset", response_class=OrjsonResponse)
async def paper_reset():
    """重置纸盘账户（清空所有数据，恢复初始资金）"""
    return paper_trader.reset()
