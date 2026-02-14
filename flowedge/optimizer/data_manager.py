"""
数据管理器 — 为回测引擎和优化器提供训练/验证/测试数据集。

核心职责：
  1. 从 signal_tracker.db 提取历史信号数据
  2. 时间序列分期（Train / Validation / Test）— 不能随机分割
  3. Walk-Forward 滚动窗口数据生成
  4. 数据质量检查（最小样本量、缺失值、异常值）
  5. 因子明细数据管理（扩展的 signal_factor_details 表）

设计原则：
  - 时间序列数据只能按时间顺序分割，禁止随机分割（防止未来信息泄露）
  - Walk-Forward 窗口：训练期 → 验证期 → 滑动，模拟真实交易
  - 最小样本量检查：每个窗口至少 30 笔信号
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SignalRecord:
    """单条信号记录（用于回测）"""
    id: int
    symbol: str
    signal: str          # BUY / STRONG_BUY / SELL / STRONG_SELL
    score: float
    confidence: float
    entry_price: float
    entry_time_ms: int
    price_5m: Optional[float] = None
    price_15m: Optional[float] = None
    price_1h: Optional[float] = None
    pnl_pct_5m: Optional[float] = None
    pnl_pct_15m: Optional[float] = None
    pnl_pct_1h: Optional[float] = None
    correct_5m: Optional[int] = None
    correct_15m: Optional[int] = None
    correct_1h: Optional[int] = None
    # 因子明细（如果有）
    factor_scores: Optional[dict[str, float]] = None
    regime: Optional[str] = None


@dataclass
class DataSplit:
    """数据分期结果"""
    train: list[SignalRecord]
    validation: list[SignalRecord]
    test: Optional[list[SignalRecord]] = None
    train_start_ms: int = 0
    train_end_ms: int = 0
    val_start_ms: int = 0
    val_end_ms: int = 0
    test_start_ms: int = 0
    test_end_ms: int = 0


@dataclass
class WalkForwardWindow:
    """Walk-Forward 单个窗口"""
    window_idx: int
    train: list[SignalRecord]
    validation: list[SignalRecord]
    train_start_ms: int
    train_end_ms: int
    val_start_ms: int
    val_end_ms: int


@dataclass
class DataQualityReport:
    """数据质量报告"""
    total_records: int
    records_with_5m: int
    records_with_15m: int
    records_with_1h: int
    records_with_factors: int
    symbols: list[str]
    date_range_days: float
    min_sample_ok: bool      # 是否满足最小样本量
    issues: list[str] = field(default_factory=list)


class DataManager:
    """
    数据管理器 — 为回测和优化提供数据。

    使用方式：
        dm = DataManager(db_path="data/signal_tracker.db")
        report = dm.quality_check()
        split = dm.time_split(train_pct=0.7, val_pct=0.15, test_pct=0.15)
        windows = dm.walk_forward_splits(train_days=30, val_days=7, n_splits=6)
    """

    # 最小样本量（低于此值优化结果不可靠）
    MIN_SAMPLES_TOTAL = 30
    MIN_SAMPLES_PER_WINDOW = 20

    def __init__(self, db_path: str = "data/signal_tracker.db"):
        self._db_path = Path(db_path)
        # 如果主数据库不存在，尝试最新备份
        if not self._db_path.exists():
            backup_dir = Path("data/backups")
            if backup_dir.exists():
                backups = sorted(backup_dir.iterdir(), reverse=True)
                for b in backups:
                    candidate = b / "signal_tracker.db"
                    if candidate.exists():
                        self._db_path = candidate
                        break

    def _connect(self) -> sqlite3.Connection:
        """创建数据库连接"""
        if not self._db_path.exists():
            raise FileNotFoundError(f"信号数据库不存在: {self._db_path}")
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── 数据加载 ──

    def load_all(
        self,
        symbol: Optional[str] = None,
        min_time_ms: Optional[int] = None,
        max_time_ms: Optional[int] = None,
        require_1h: bool = True,
    ) -> list[SignalRecord]:
        """
        加载信号记录。

        参数:
            symbol: 过滤币种（None = 全部）
            min_time_ms: 最早时间戳
            max_time_ms: 最晚时间戳
            require_1h: 是否要求有 1h 回查数据（推荐 True，确保数据完整）
        """
        conn = self._connect()
        try:
            query = "SELECT * FROM signal_tracks WHERE 1=1"
            params: list = []

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            if min_time_ms:
                query += " AND entry_time_ms >= ?"
                params.append(min_time_ms)
            if max_time_ms:
                query += " AND entry_time_ms <= ?"
                params.append(max_time_ms)
            if require_1h:
                query += " AND price_1h IS NOT NULL"

            query += " ORDER BY entry_time_ms ASC"

            rows = conn.execute(query, params).fetchall()
            records = [self._row_to_record(r) for r in rows]
            if not records:
                return records

            # 补充因子明细（供回测重算 score 使用）
            factor_map = self._load_factor_details(conn, [r.id for r in records])
            for rec in records:
                detail = factor_map.get(rec.id)
                if detail:
                    rec.factor_scores = detail.get("scores")
                    rec.regime = detail.get("regime")
            return records
        finally:
            conn.close()

    def _load_factor_details(
        self,
        conn: sqlite3.Connection,
        track_ids: list[int],
    ) -> dict[int, dict]:
        """
        读取 signal_factor_details，返回:
            {track_id: {"scores": {factor_name: factor_score}, "regime": str|None}}
        """
        if not track_ids:
            return {}

        result: dict[int, dict] = {}
        # SQLite 变量数量有限，分批查询更稳妥
        batch_size = 800

        for i in range(0, len(track_ids), batch_size):
            batch = track_ids[i : i + batch_size]
            placeholders = ",".join("?" for _ in batch)
            query = (
                "SELECT track_id, factor_name, factor_score, regime "
                f"FROM signal_factor_details WHERE track_id IN ({placeholders}) "
                "ORDER BY id ASC"
            )
            try:
                rows = conn.execute(query, batch).fetchall()
            except sqlite3.OperationalError:
                # 兼容老库：若该表不存在，直接返回空映射
                return {}

            for row in rows:
                item = result.setdefault(
                    row["track_id"],
                    {"scores": {}, "regime": None},
                )
                item["scores"][row["factor_name"]] = row["factor_score"]
                if not item["regime"] and row["regime"]:
                    item["regime"] = row["regime"]

        return result

    def _row_to_record(self, row: sqlite3.Row) -> SignalRecord:
        """将数据库行转换为 SignalRecord"""
        return SignalRecord(
            id=row["id"],
            symbol=row["symbol"],
            signal=row["signal"],
            score=row["score"],
            confidence=row["confidence"],
            entry_price=row["entry_price"],
            entry_time_ms=row["entry_time_ms"],
            price_5m=row["price_5m"],
            price_15m=row["price_15m"],
            price_1h=row["price_1h"],
            pnl_pct_5m=row["pnl_pct_5m"],
            pnl_pct_15m=row["pnl_pct_15m"],
            pnl_pct_1h=row["pnl_pct_1h"],
            correct_5m=row["correct_5m"],
            correct_15m=row["correct_15m"],
            correct_1h=row["correct_1h"],
        )

    # ── 数据分期 ──

    def time_split(
        self,
        records: Optional[list[SignalRecord]] = None,
        train_pct: float = 0.70,
        val_pct: float = 0.15,
        test_pct: float = 0.15,
    ) -> DataSplit:
        """
        按时间顺序分割数据（禁止随机分割）。

        参数:
            records: 信号记录列表（None = 自动加载全部）
            train_pct: 训练集比例
            val_pct: 验证集比例
            test_pct: 测试集比例
        """
        if records is None:
            records = self.load_all()

        if not records:
            return DataSplit(train=[], validation=[], test=[])

        # 按时间排序（应该已经排好了，但确保一下）
        records = sorted(records, key=lambda r: r.entry_time_ms)

        n = len(records)
        train_end = int(n * train_pct)
        val_end = int(n * (train_pct + val_pct))

        train = records[:train_end]
        val = records[train_end:val_end]
        test = records[val_end:]

        return DataSplit(
            train=train,
            validation=val,
            test=test,
            train_start_ms=train[0].entry_time_ms if train else 0,
            train_end_ms=train[-1].entry_time_ms if train else 0,
            val_start_ms=val[0].entry_time_ms if val else 0,
            val_end_ms=val[-1].entry_time_ms if val else 0,
            test_start_ms=test[0].entry_time_ms if test else 0,
            test_end_ms=test[-1].entry_time_ms if test else 0,
        )

    def walk_forward_splits(
        self,
        records: Optional[list[SignalRecord]] = None,
        train_days: int = 30,
        val_days: int = 7,
        n_splits: Optional[int] = None,
        step_days: Optional[int] = None,
    ) -> list[WalkForwardWindow]:
        """
        Walk-Forward 滚动窗口分割。

        参数:
            records: 信号记录列表
            train_days: 训练窗口天数
            val_days: 验证窗口天数
            n_splits: 窗口数量（None = 自动计算最大窗口数）
            step_days: 滑动步长天数（None = val_days，即不重叠）
        """
        if records is None:
            records = self.load_all()

        if not records:
            return []

        records = sorted(records, key=lambda r: r.entry_time_ms)
        step_days = step_days or val_days

        ms_per_day = 86400 * 1000
        train_ms = train_days * ms_per_day
        val_ms = val_days * ms_per_day
        step_ms = step_days * ms_per_day

        start_ms = records[0].entry_time_ms
        end_ms = records[-1].entry_time_ms

        windows: list[WalkForwardWindow] = []
        idx = 0
        cursor = start_ms

        while True:
            train_start = cursor
            train_end = cursor + train_ms
            val_start = train_end
            val_end = val_start + val_ms

            if val_end > end_ms:
                break  # 数据不够了

            train_data = [r for r in records if train_start <= r.entry_time_ms < train_end]
            val_data = [r for r in records if val_start <= r.entry_time_ms < val_end]

            # 跳过样本量不足的窗口
            if len(train_data) >= self.MIN_SAMPLES_PER_WINDOW and len(val_data) >= 5:
                windows.append(WalkForwardWindow(
                    window_idx=idx,
                    train=train_data,
                    validation=val_data,
                    train_start_ms=train_start,
                    train_end_ms=train_end,
                    val_start_ms=val_start,
                    val_end_ms=val_end,
                ))
                idx += 1

            cursor += step_ms

            if n_splits and idx >= n_splits:
                break

        return windows

    # ── 数据质量检查 ──

    def quality_check(
        self,
        records: Optional[list[SignalRecord]] = None,
    ) -> DataQualityReport:
        """
        数据质量检查。

        返回:
            DataQualityReport — 包含数据量、完整度、问题列表
        """
        if records is None:
            try:
                records = self.load_all(require_1h=False)
            except FileNotFoundError:
                return DataQualityReport(
                    total_records=0, records_with_5m=0, records_with_15m=0,
                    records_with_1h=0, records_with_factors=0,
                    symbols=[], date_range_days=0, min_sample_ok=False,
                    issues=["信号数据库不存在"],
                )

        if not records:
            return DataQualityReport(
                total_records=0, records_with_5m=0, records_with_15m=0,
                records_with_1h=0, records_with_factors=0,
                symbols=[], date_range_days=0, min_sample_ok=False,
                issues=["无信号记录"],
            )

        total = len(records)
        with_5m = sum(1 for r in records if r.price_5m is not None)
        with_15m = sum(1 for r in records if r.price_15m is not None)
        with_1h = sum(1 for r in records if r.price_1h is not None)
        with_factors = sum(1 for r in records if r.factor_scores is not None)
        symbols = list(set(r.symbol for r in records))

        time_range_ms = records[-1].entry_time_ms - records[0].entry_time_ms
        date_range_days = time_range_ms / (86400 * 1000) if time_range_ms > 0 else 0

        issues = []
        if total < self.MIN_SAMPLES_TOTAL:
            issues.append(f"样本量不足: {total} < {self.MIN_SAMPLES_TOTAL}")
        if with_1h < total * 0.5:
            issues.append(f"1h 回查数据不完整: {with_1h}/{total} ({with_1h/total*100:.0f}%)")
        if with_factors == 0:
            issues.append("无因子明细数据（需要扩展 signal_tracker 后积累）")
        if date_range_days < 3:
            issues.append(f"数据时间跨度太短: {date_range_days:.1f} 天")

        return DataQualityReport(
            total_records=total,
            records_with_5m=with_5m,
            records_with_15m=with_15m,
            records_with_1h=with_1h,
            records_with_factors=with_factors,
            symbols=symbols,
            date_range_days=round(date_range_days, 1),
            min_sample_ok=total >= self.MIN_SAMPLES_TOTAL,
            issues=issues,
        )

    # ── 统计摘要 ──

    def summary(self) -> dict:
        """返回数据摘要（用于 API）"""
        report = self.quality_check()
        return {
            "db_path": str(self._db_path),
            "db_exists": self._db_path.exists(),
            "total_records": report.total_records,
            "records_with_1h": report.records_with_1h,
            "records_with_factors": report.records_with_factors,
            "symbols": report.symbols,
            "date_range_days": report.date_range_days,
            "min_sample_ok": report.min_sample_ok,
            "issues": report.issues,
        }
