"""
信号追踪器 — 验证信号质量的核心模块。

职责：
  1. 信号变化时记录当前价格
  2. 定时回查（5分钟/15分钟/60分钟后），检查价格是否按信号方向移动
  3. 统计胜率、平均盈亏比
  4. 数据持久化到 SQLite

这是赚钱路径的 Phase 1——不验证信号就交易等于赌博。

关键指标（决定是否可以上实盘）：
  - 5分钟方向胜率 > 55% → 信号有短期边
  - 15分钟方向胜率 > 52% → 可用于中频交易
  - 胜率 < 50% → 信号无效，需要调整权重
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger("flowedge.tracker")

# 回查时间窗口（秒）
CHECK_WINDOWS = [300, 900, 3600]  # 5分钟, 15分钟, 1小时

# 币安标记价格 API
BINANCE_MARK_PRICE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"


@dataclass
class TrackRecord:
    """单条追踪记录"""
    id: int
    symbol: str
    signal: str           # BUY / STRONG_BUY / SELL / STRONG_SELL
    score: float
    confidence: float
    entry_price: float    # 信号产生时的标记价格
    entry_time_ms: int
    # 回查结果（初始为 None，回查后填入）
    price_5m: Optional[float] = None
    price_15m: Optional[float] = None
    price_1h: Optional[float] = None
    pnl_pct_5m: Optional[float] = None
    pnl_pct_15m: Optional[float] = None
    pnl_pct_1h: Optional[float] = None
    correct_5m: Optional[bool] = None
    correct_15m: Optional[bool] = None
    correct_1h: Optional[bool] = None


class SignalTracker:
    """
    信号追踪器：记录信号变化时的价格，定时回查验证胜率。
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            from ..config import DATA_DIR
            db_path = DATA_DIR / "signal_tracker.db"
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._session: Optional[aiohttp.ClientSession] = None
        self._price_cache: dict[str, tuple[float, float]] = {}  # symbol -> (price, timestamp)

    def _init_db(self) -> None:
        """初始化 SQLite 数据库"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                score REAL NOT NULL,
                confidence REAL NOT NULL,
                entry_price REAL NOT NULL,
                entry_time_ms INTEGER NOT NULL,
                price_5m REAL,
                price_15m REAL,
                price_1h REAL,
                pnl_pct_5m REAL,
                pnl_pct_15m REAL,
                pnl_pct_1h REAL,
                correct_5m INTEGER,
                correct_15m INTEGER,
                correct_1h INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_symbol
            ON signal_tracks(symbol)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracks_entry_time
            ON signal_tracks(entry_time_ms)
        """)
        conn.commit()
        conn.close()
        logger.info(f"[Tracker] 数据库已初始化: {self._db_path}")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _fetch_mark_price(self, symbol: str) -> Optional[float]:
        """从币安获取当前标记价格"""
        # 缓存 1 秒内的价格
        cached = self._price_cache.get(symbol)
        now = time.time()
        if cached and (now - cached[1]) < 1.0:
            return cached[0]

        try:
            session = await self._get_session()
            async with session.get(
                BINANCE_MARK_PRICE_URL,
                params={"symbol": symbol},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    price = float(data["markPrice"])
                    self._price_cache[symbol] = (price, now)
                    return price
        except Exception as e:
            logger.warning(f"[Tracker] 获取 {symbol} 标记价格失败: {e}")
        return None

    async def on_signal_change(
        self,
        symbol: str,
        signal: str,
        score: float,
        confidence: float,
    ) -> None:
        """
        信号变化时调用。记录当前价格，创建追踪记录。
        只追踪有方向的信号（BUY/SELL），跳过 NEUTRAL。
        """
        if signal == "NEUTRAL":
            return

        price = await self._fetch_mark_price(symbol)
        if price is None:
            logger.warning(f"[Tracker] 无法获取 {symbol} 价格，跳过追踪")
            return

        now_ms = int(time.time() * 1000)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            """INSERT INTO signal_tracks
               (symbol, signal, score, confidence, entry_price, entry_time_ms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, signal, score, confidence, price, now_ms),
        )
        conn.commit()
        conn.close()

        logger.info(
            f"[Tracker] 记录信号 {symbol} {signal} "
            f"score={score:+.3f} conf={confidence:.1%} @ ${price:,.2f}"
        )

    async def check_pending(self) -> int:
        """
        回查所有待验证的记录。
        检查哪些记录已过了 5m/15m/1h 窗口，获取当前价格并计算结果。
        返回本次更新的记录数。
        """
        now_ms = int(time.time() * 1000)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row

        # 查找需要回查的记录（有未填充的价格字段）
        rows = conn.execute("""
            SELECT id, symbol, signal, score, entry_price, entry_time_ms,
                   price_5m, price_15m, price_1h
            FROM signal_tracks
            WHERE price_5m IS NULL OR price_15m IS NULL OR price_1h IS NULL
            ORDER BY entry_time_ms ASC
            LIMIT 100
        """).fetchall()

        updated = 0
        for row in rows:
            elapsed_s = (now_ms - row["entry_time_ms"]) / 1000
            entry_price = row["entry_price"]
            is_long = row["signal"] in ("BUY", "STRONG_BUY")

            # 5 分钟回查
            if row["price_5m"] is None and elapsed_s >= 300:
                price = await self._fetch_mark_price(row["symbol"])
                if price:
                    pnl_pct = ((price - entry_price) / entry_price) * 100
                    correct = (pnl_pct > 0) if is_long else (pnl_pct < 0)
                    conn.execute(
                        """UPDATE signal_tracks
                           SET price_5m = ?, pnl_pct_5m = ?, correct_5m = ?
                           WHERE id = ?""",
                        (price, round(pnl_pct, 4), int(correct), row["id"]),
                    )
                    updated += 1

            # 15 分钟回查
            if row["price_15m"] is None and elapsed_s >= 900:
                price = await self._fetch_mark_price(row["symbol"])
                if price:
                    pnl_pct = ((price - entry_price) / entry_price) * 100
                    correct = (pnl_pct > 0) if is_long else (pnl_pct < 0)
                    conn.execute(
                        """UPDATE signal_tracks
                           SET price_15m = ?, pnl_pct_15m = ?, correct_15m = ?
                           WHERE id = ?""",
                        (price, round(pnl_pct, 4), int(correct), row["id"]),
                    )
                    updated += 1

            # 1 小时回查
            if row["price_1h"] is None and elapsed_s >= 3600:
                price = await self._fetch_mark_price(row["symbol"])
                if price:
                    pnl_pct = ((price - entry_price) / entry_price) * 100
                    correct = (pnl_pct > 0) if is_long else (pnl_pct < 0)
                    conn.execute(
                        """UPDATE signal_tracks
                           SET price_1h = ?, pnl_pct_1h = ?, correct_1h = ?
                           WHERE id = ?""",
                        (price, round(pnl_pct, 4), int(correct), row["id"]),
                    )
                    updated += 1

        conn.commit()
        conn.close()
        return updated

    def get_performance(self, symbol: Optional[str] = None) -> dict:
        """
        获取信号胜率统计。

        返回:
            {
                "total_signals": int,
                "windows": {
                    "5m": {"total": N, "correct": N, "win_rate": %, "avg_pnl": %},
                    "15m": {...},
                    "1h": {...},
                },
                "by_signal": {
                    "BUY": {"5m": {...}, "15m": {...}, "1h": {...}},
                    "STRONG_BUY": {...},
                    ...
                },
                "by_symbol": {...},
                "recent": [...],  # 最近 20 条记录
            }
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row

        where = "WHERE 1=1"
        params: list = []
        if symbol:
            where += " AND symbol = ?"
            params.append(symbol)

        # 总体统计
        total = conn.execute(
            f"SELECT COUNT(*) as cnt FROM signal_tracks {where}", params
        ).fetchone()["cnt"]

        # 各窗口胜率
        windows = {}
        for col, label in [("5m", "5m"), ("15m", "15m"), ("1h", "1h")]:
            row = conn.execute(f"""
                SELECT
                    COUNT(correct_{col}) as total,
                    SUM(correct_{col}) as correct,
                    AVG(pnl_pct_{col}) as avg_pnl,
                    AVG(ABS(CASE WHEN pnl_pct_{col} > 0 THEN pnl_pct_{col} END)) as avg_win,
                    AVG(ABS(CASE WHEN pnl_pct_{col} < 0 THEN pnl_pct_{col} END)) as avg_loss
                FROM signal_tracks
                {where} AND correct_{col} IS NOT NULL
            """, params).fetchone()

            t = row["total"] or 0
            c = row["correct"] or 0
            windows[label] = {
                "total": t,
                "correct": c,
                "win_rate": round(c / t * 100, 1) if t > 0 else 0,
                "avg_pnl": round(row["avg_pnl"] or 0, 4),
                "avg_win": round(row["avg_win"] or 0, 4),
                "avg_loss": round(row["avg_loss"] or 0, 4),
            }

        # 按信号类型统计
        by_signal = {}
        for sig in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL"):
            sig_windows = {}
            for col, label in [("5m", "5m"), ("15m", "15m"), ("1h", "1h")]:
                row = conn.execute(f"""
                    SELECT
                        COUNT(correct_{col}) as total,
                        SUM(correct_{col}) as correct,
                        AVG(pnl_pct_{col}) as avg_pnl
                    FROM signal_tracks
                    {where} AND signal = ? AND correct_{col} IS NOT NULL
                """, params + [sig]).fetchone()
                t = row["total"] or 0
                c = row["correct"] or 0
                sig_windows[label] = {
                    "total": t,
                    "correct": c,
                    "win_rate": round(c / t * 100, 1) if t > 0 else 0,
                    "avg_pnl": round(row["avg_pnl"] or 0, 4),
                }
            if any(w["total"] > 0 for w in sig_windows.values()):
                by_signal[sig] = sig_windows

        # 按币种统计
        by_symbol = {}
        symbols_rows = conn.execute(f"""
            SELECT DISTINCT symbol FROM signal_tracks {where}
        """, params).fetchall()
        for sr in symbols_rows:
            sym = sr["symbol"]
            sym_windows = {}
            for col, label in [("5m", "5m"), ("15m", "15m"), ("1h", "1h")]:
                row = conn.execute(f"""
                    SELECT
                        COUNT(correct_{col}) as total,
                        SUM(correct_{col}) as correct,
                        AVG(pnl_pct_{col}) as avg_pnl
                    FROM signal_tracks
                    WHERE symbol = ? AND correct_{col} IS NOT NULL
                """, [sym]).fetchone()
                t = row["total"] or 0
                c = row["correct"] or 0
                sym_windows[label] = {
                    "total": t,
                    "correct": c,
                    "win_rate": round(c / t * 100, 1) if t > 0 else 0,
                    "avg_pnl": round(row["avg_pnl"] or 0, 4),
                }
            by_symbol[sym] = sym_windows

        # 最近 20 条
        recent = conn.execute(f"""
            SELECT symbol, signal, score, confidence, entry_price, entry_time_ms,
                   price_5m, pnl_pct_5m, correct_5m,
                   price_15m, pnl_pct_15m, correct_15m,
                   price_1h, pnl_pct_1h, correct_1h
            FROM signal_tracks {where}
            ORDER BY entry_time_ms DESC LIMIT 20
        """, params).fetchall()

        recent_list = []
        for r in recent:
            recent_list.append({
                "symbol": r["symbol"],
                "signal": r["signal"],
                "score": r["score"],
                "confidence": r["confidence"],
                "entry_price": r["entry_price"],
                "entry_time_ms": r["entry_time_ms"],
                "results": {
                    "5m": {"price": r["price_5m"], "pnl_pct": r["pnl_pct_5m"], "correct": bool(r["correct_5m"]) if r["correct_5m"] is not None else None},
                    "15m": {"price": r["price_15m"], "pnl_pct": r["pnl_pct_15m"], "correct": bool(r["correct_15m"]) if r["correct_15m"] is not None else None},
                    "1h": {"price": r["price_1h"], "pnl_pct": r["pnl_pct_1h"], "correct": bool(r["correct_1h"]) if r["correct_1h"] is not None else None},
                },
            })

        conn.close()

        return {
            "total_signals": total,
            "windows": windows,
            "by_signal": by_signal,
            "by_symbol": by_symbol,
            "recent": recent_list,
        }

    async def tracking_loop(self, interval_s: int = 30) -> None:
        """
        定时回查循环。每 30 秒检查一次待验证记录。
        """
        logger.info(f"[Tracker] 追踪循环启动，间隔 {interval_s}s")
        while True:
            try:
                updated = await self.check_pending()
                if updated > 0:
                    logger.info(f"[Tracker] 回查更新了 {updated} 条记录")
            except Exception as e:
                logger.error(f"[Tracker] 回查异常: {e}", exc_info=True)
            await asyncio.sleep(interval_s)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
