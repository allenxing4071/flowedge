"""
纸盘交易模块 — 用真实数据模拟交易，验证信号能不能赚钱。

核心逻辑：
  1. 信号变化时，按真实标记价格"虚拟开仓"
  2. 模拟滑点（0.05%）和手续费（0.04%）
  3. 持仓期间追踪实时盈亏、止损检测
  4. 信号反转或止损时"虚拟平仓"
  5. 所有交易记录存入 SQLite，输出成绩单

关键指标：
  - 胜率、盈亏比、最大回撤
  - 累计资金曲线
  - Sharpe Ratio

数据流：
  SignalEngine.evaluate() → 信号变化 → PaperTrader.on_signal_change()
  MarkPrice 实时更新 → PaperTrader.on_price_update() → 止损/止盈检测

平仓逻辑（错了马上出、对了拿住）：
  - NEUTRAL 时：浮盈 > 0 视为跟对 → 不平仓，等止盈/追踪止盈；浮亏或未知 → 最短 min_hold_wrong_s 后平（signal_neutral_wrong）
  - 反转信号：仍用 min_hold*0.4 保护后平；价格层：止损/固定止盈/追踪止盈照常

「跟对做市商」定义（事后度量）：
  - 开仓方向来自门卫 L2 suggested_side（价值区/VWAP/突破等），非 scorer 的 score 方向
  - 跟对：多单且 exit_price > entry_price，或空单且 exit_price < entry_price；跟错：反之
  - 代码内无实时的「当前这笔跟没跟对」判断，仅平仓后用交易记录 net_pnl / 价格方向统计
"""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import aiohttp

from .config import cfg

logger = logging.getLogger("flowedge.paper")

# 币安标记价格 API
BINANCE_MARK_PRICE_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"


# ═══════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════

@dataclass
class PaperPosition:
    """虚拟持仓"""
    symbol: str
    side: str               # LONG / SHORT
    entry_price: float
    quantity: float          # 数量（以标的计）
    notional: float          # 名义价值 USDT
    leverage: int
    margin: float            # 占用保证金
    entry_time: float        # unix timestamp
    signal: str              # 触发信号
    score: float
    confidence: float
    stop_loss_price: float   # 止损价
    stop_loss_pct: float = 2.0     # 止损百分比（动态或固定）
    take_profit_pct: float = 1.5   # 止盈百分比（动态或固定）
    sl_source: str = "固定配置"     # 止损来源（门卫动态 / 固定配置）
    # 实时更新
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    max_pnl_pct: float = 0.0       # 持仓期间最高盈利%
    min_pnl_pct: float = 0.0       # 持仓期间最大回撤%


@dataclass
class PaperTrade:
    """已完成的虚拟交易"""
    id: int
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    notional: float
    leverage: int
    entry_time: float
    exit_time: float
    signal_entry: str
    signal_exit: str
    score_entry: float
    confidence_entry: float
    # 盈亏
    gross_pnl: float          # 毛利（不含费用）
    fee_cost: float           # 手续费
    slippage_cost: float      # 滑点成本
    net_pnl: float            # 净利
    net_pnl_pct: float        # 净利 %
    max_pnl_pct: float        # 持仓期间最高盈利%
    min_pnl_pct: float        # 持仓期间最大回撤%
    duration_s: float         # 持仓时长（秒）
    exit_reason: str          # signal_reverse / stop_loss / manual
    sl_source: str = "固定配置"  # 止损来源（门卫动态 / 固定配置）


@dataclass
class PaperConfig:
    """纸盘交易配置"""
    enabled: bool = True
    initial_balance: float = 10000.0    # 初始资金 USDT
    leverage: int = 10                   # 默认杠杆
    position_pct: float = 10.0           # 单仓占比 %（占总资产）
    stop_loss_pct: float = 2.0           # 止损 %（价格变动，10x 杠杆 = 20% 保证金亏损）
    take_profit_pct: float = 1.5         # 固定止盈 %（价格变动，10x 杠杆 = 15% 保证金盈利）
    trailing_activate_pct: float = 0.8   # 追踪止盈激活 %（价格涨 0.8% 后启动追踪）
    trailing_callback_pct: float = 40.0  # 追踪止盈回撤比例 %（从最高盈利回撤 40% 时平仓）
    slippage_pct: float = 0.02           # 模拟滑点 %（BTC/ETH 主流币实际约 0.01-0.02%）
    fee_pct: float = 0.02               # 单边手续费 %（币安 maker 0.02%，保守取 0.02%）
    cooldown_s: float = 120.0            # 开仓冷却期（秒），防止 whipsaw 连续反转
    min_hold_s: float = 300.0            # 最低持仓时间（秒），NEUTRAL 且「对了」时拿住用
    min_hold_wrong_s: float = 15.0      # 「错了马上出」：NEUTRAL 且浮亏时，持仓≥此秒数即可平
    # 触发条件
    min_confidence: float = 0.40         # 最低置信度（过滤低一致性信号）
    min_entry_score: float = 0.30        # 最低入场评分绝对值（只进强信号单）
    entry_signals: list = field(default_factory=lambda: [
        "STRONG_BUY", "BUY", "STRONG_SELL", "SELL"
    ])


@dataclass
class PaperStats:
    """纸盘交易统计"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_net_pnl: float = 0.0
    total_fee_cost: float = 0.0
    total_slippage_cost: float = 0.0
    avg_pnl_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0       # 总盈利 / 总亏损
    max_drawdown_pct: float = 0.0    # 最大回撤 %
    sharpe_ratio: float = 0.0        # 年化 Sharpe
    current_balance: float = 0.0
    equity: float = 0.0              # 余额 + 未实现盈亏
    return_pct: float = 0.0          # 总收益率 %


# ═══════════════════════════════════════════
# PaperTrader 核心
# ═══════════════════════════════════════════

class PaperTrader:
    """纸盘交易引擎"""

    def __init__(self, db_path: Optional[Path] = None):
        self.config = PaperConfig()
        self.config.min_hold_s = cfg.PAPER_MIN_HOLD_S  # 高频模式 120s，默认 300s
        self.config.min_hold_wrong_s = cfg.PAPER_MIN_HOLD_WRONG_S  # 错了马上出：NEUTRAL 且浮亏时最短 15s
        self.config.min_confidence = cfg.PAPER_MIN_CONFIDENCE   # 放开 0.15，缩紧时调大
        self.config.min_entry_score = cfg.PAPER_MIN_ENTRY_SCORE   # 放开 0.05，缩紧时调大
        self.config.cooldown_s = cfg.PAPER_COOLDOWN_S             # 放开 30s，缩紧时调大
        self._db_path = db_path or (Path("data") / "paper_trades.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # 虚拟账户
        self._balance: float = self.config.initial_balance
        self._positions: dict[str, PaperPosition] = {}
        # 冷却期记录：symbol → 上次开仓时间
        self._last_open_time: dict[str, float] = {}
        # 信号决策日志（内存环形缓冲，最近 100 条）
        self._signal_log: list[dict] = []
        self._signal_log_max = 100

        # 初始化数据库
        self._init_db()
        self._load_state()

    # ──────────────────────────────────────
    # 信号决策日志
    # ──────────────────────────────────────

    def _log_signal(
        self,
        symbol: str,
        signal: str,
        score: float,
        confidence: float,
        action: str,
        reason: str,
        extra: dict | None = None,
    ):
        """
        记录一条信号决策日志。
        action: open / close / hold / skip
        """
        entry = {
            "ts": time.time(),
            "symbol": symbol,
            "signal": signal,
            "score": round(score, 4),
            "confidence": round(confidence, 3),
            "action": action,
            "reason": reason,
        }
        if extra:
            entry["detail"] = extra
        self._signal_log.append(entry)
        # 环形缓冲：超出上限时裁剪
        if len(self._signal_log) > self._signal_log_max:
            self._signal_log = self._signal_log[-self._signal_log_max:]

    def get_signal_log(self, limit: int = 50) -> list[dict]:
        """返回最近 N 条信号决策日志（最新在前）"""
        return list(reversed(self._signal_log[-limit:]))

    # ──────────────────────────────────────
    # 统一 equity 计算（修复保证金重复扣减 bug）
    # ──────────────────────────────────────

    def _calc_equity(self) -> float:
        """
        正确的 equity = 可用余额 + 持仓保证金 + 未实现盈亏
        （类似交易所的 Account Equity，不因保证金锁定而显示虚假亏损）
        """
        margin_in_positions = sum(p.margin for p in self._positions.values())
        unrealized = sum(p.unrealized_pnl for p in self._positions.values())
        return self._balance + margin_in_positions + unrealized

    def _calc_unrealized(self) -> float:
        """持仓未实现盈亏合计"""
        return sum(p.unrealized_pnl for p in self._positions.values())

    # ──────────────────────────────────────
    # 数据库
    # ──────────────────────────────────────

    def _init_db(self):
        """创建表结构"""
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional REAL NOT NULL,
                leverage INTEGER NOT NULL,
                entry_time REAL NOT NULL,
                exit_time REAL NOT NULL,
                signal_entry TEXT NOT NULL,
                signal_exit TEXT NOT NULL,
                score_entry REAL NOT NULL,
                confidence_entry REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                fee_cost REAL NOT NULL,
                slippage_cost REAL NOT NULL,
                net_pnl REAL NOT NULL,
                net_pnl_pct REAL NOT NULL,
                max_pnl_pct REAL NOT NULL,
                min_pnl_pct REAL NOT NULL,
                duration_s REAL NOT NULL,
                exit_reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS paper_equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                balance REAL NOT NULL,
                equity REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                position_count INTEGER NOT NULL
            );
        """)
        # 兼容迁移：为已存在的表添加 sl_source 列（SQLite 不支持 IF NOT EXISTS）
        try:
            conn.execute("ALTER TABLE paper_trades ADD COLUMN sl_source TEXT DEFAULT '固定配置'")
        except Exception:
            pass  # 列已存在，忽略
        conn.close()

    def _save_state(self):
        """保存余额到数据库"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "INSERT OR REPLACE INTO paper_state (key, value) VALUES (?, ?)",
            ("balance", str(self._balance))
        )
        conn.commit()
        conn.close()

    def _load_state(self):
        """从数据库恢复余额；并与交易记录一致化（balance = initial + sum(已平仓 net_pnl)）。"""
        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT value FROM paper_state WHERE key = ?", ("balance",)
        ).fetchone()
        if row:
            self._balance = float(row[0])
        else:
            self._balance = self.config.initial_balance

        # 一致性：余额应等于 初始 + 所有已平仓交易的 net_pnl 之和（重启后无持仓，无占用保证金）
        sum_row = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM paper_trades"
        ).fetchone()
        conn.close()
        if sum_row is not None:
            sum_pnl = float(sum_row[0])
            expected_balance = self.config.initial_balance + sum_pnl
            if abs(self._balance - expected_balance) > 0.02:
                logger.warning(
                    f"[纸盘] 余额与交易记录不一致: 当前 balance={self._balance:.2f} "
                    f"预期 initial+sum(net_pnl)={expected_balance:.2f}，已按交易记录修正"
                )
                self._balance = expected_balance
                self._save_state()

    def _save_trade(self, trade: PaperTrade):
        """保存已完成交易"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            INSERT INTO paper_trades (
                symbol, side, entry_price, exit_price, quantity, notional,
                leverage, entry_time, exit_time, signal_entry, signal_exit,
                score_entry, confidence_entry, gross_pnl, fee_cost,
                slippage_cost, net_pnl, net_pnl_pct, max_pnl_pct,
                min_pnl_pct, duration_s, exit_reason, sl_source
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.symbol, trade.side, trade.entry_price, trade.exit_price,
            trade.quantity, trade.notional, trade.leverage,
            trade.entry_time, trade.exit_time,
            trade.signal_entry, trade.signal_exit,
            trade.score_entry, trade.confidence_entry,
            trade.gross_pnl, trade.fee_cost, trade.slippage_cost,
            trade.net_pnl, trade.net_pnl_pct,
            trade.max_pnl_pct, trade.min_pnl_pct,
            trade.duration_s, trade.exit_reason,
            trade.sl_source,
        ))
        conn.commit()
        conn.close()

    def _save_equity_snapshot(self):
        """记录资金曲线快照"""
        unrealized = self._calc_unrealized()
        equity = self._calc_equity()
        conn = sqlite3.connect(str(self._db_path))
        conn.execute(
            "INSERT INTO paper_equity (timestamp, balance, equity, unrealized_pnl, position_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (time.time(), self._balance, equity, unrealized, len(self._positions))
        )
        conn.commit()
        conn.close()

    # ──────────────────────────────────────
    # 信号触发（入口）
    # ──────────────────────────────────────

    async def on_signal_change(
        self,
        symbol: str,
        signal: str,
        score: float,
        confidence: float,
        gate_result=None,
    ):
        """
        信号变化时调用（由 SignalEngine 触发）。

        参数:
            gate_result: EntryGate 的 GateResult，包含动态止损止盈建议。
                         如果为 None，使用默认固定止损止盈。

        逻辑：
          - 有持仓且信号反转 → 平仓
          - 无持仓且信号为买/卖 → 开仓（使用门卫建议的动态止损止盈）
          - 有持仓且信号同向 → 不操作
        """
        if not self.config.enabled:
            return

        existing = self._positions.get(symbol)

        # 判断信号方向
        is_buy = signal in ("STRONG_BUY", "BUY")
        is_sell = signal in ("STRONG_SELL", "SELL")
        is_neutral = signal == "NEUTRAL"

        if existing:
            # 有仓位 — 检查是否需要平仓
            should_close = False
            close_reason = ""
            held_s = time.time() - existing.entry_time

            if is_neutral:
                # 错了马上出、对了拿住：NEUTRAL 时看浮盈浮亏
                # 浮盈 > 0 → 视为跟对，拿住不平仓，等止盈/追踪止盈
                # 浮亏或未知(≤0) → 视为错了，最短 min_hold_wrong_s 后即平
                pnl_pct = getattr(existing, "unrealized_pnl_pct", 0.0) or 0.0
                if pnl_pct > 0:
                    should_close = False
                    self._log_signal(symbol, signal, score, confidence,
                                     "hold", f"中性信号但浮盈+{pnl_pct:.2f}%，对了拿住不平仓")
                    logger.debug(
                        f"[纸盘] {symbol} NEUTRAL 浮盈 {pnl_pct:.2f}%，拿住不平仓"
                    )
                elif held_s >= self.config.min_hold_wrong_s:
                    should_close = True
                    close_reason = "signal_neutral_wrong"
                else:
                    should_close = False
                    self._log_signal(symbol, signal, score, confidence,
                                     "hold", f"中性信号浮亏/未知，最短{self.config.min_hold_wrong_s:.0f}s后平（已{held_s:.0f}s）")
                    logger.debug(
                        f"[纸盘] {symbol} NEUTRAL 浮亏/未知，{held_s:.0f}s < {self.config.min_hold_wrong_s:.0f}s，继续持有"
                    )
            elif existing.side == "LONG" and is_sell:
                # 信号反转 — 也需要最低持仓保护（防止信号抖动导致秒级反转亏损）
                # 但使用较短的保护时间（min_hold 的 40%），因为反转信号比中性更强
                reverse_min = self.config.min_hold_s * 0.4
                if held_s >= reverse_min:
                    should_close = True
                    close_reason = "signal_reverse"
                else:
                    self._log_signal(symbol, signal, score, confidence,
                                     "hold", f"反转信号(SELL)但持仓仅{held_s:.0f}s（需≥{reverse_min:.0f}s）")
                    logger.debug(
                        f"[纸盘] {symbol} 反转信号(SELL)但持仓仅{held_s:.0f}s "
                        f"(需≥{reverse_min:.0f}s)，继续持有"
                    )
            elif existing.side == "SHORT" and is_buy:
                reverse_min = self.config.min_hold_s * 0.4
                if held_s >= reverse_min:
                    should_close = True
                    close_reason = "signal_reverse"
                else:
                    self._log_signal(symbol, signal, score, confidence,
                                     "hold", f"反转信号(BUY)但持仓仅{held_s:.0f}s（需≥{reverse_min:.0f}s）")
                    logger.debug(
                        f"[纸盘] {symbol} 反转信号(BUY)但持仓仅{held_s:.0f}s "
                        f"(需≥{reverse_min:.0f}s)，继续持有"
                    )
            else:
                # 同向信号 — 继续持有
                self._log_signal(symbol, signal, score, confidence,
                                 "hold", f"同向信号，继续持有 {existing.side}")

            if should_close:
                self._log_signal(symbol, signal, score, confidence,
                                 "close", f"平仓: {close_reason}（持仓{held_s:.0f}s）")
                await self._close_position(symbol, signal, close_reason)

                # 反转开仓（不是中性时）
                if not is_neutral and signal in self.config.entry_signals:
                    if confidence >= self.config.min_confidence:
                        await self._open_position(symbol, signal, score, confidence, gate_result)
        else:
            # 无仓位 — 检查是否开仓
            if signal in self.config.entry_signals:
                if confidence >= self.config.min_confidence:
                    await self._open_position(symbol, signal, score, confidence, gate_result)
                else:
                    self._log_signal(symbol, signal, score, confidence,
                                     "skip", f"置信度不足: {confidence:.2f} < {self.config.min_confidence}")
            else:
                self._log_signal(symbol, signal, score, confidence,
                                 "skip", f"非入场信号: {signal}")

    # ──────────────────────────────────────
    # 价格更新（止损检测）
    # ──────────────────────────────────────

    def on_price_update(self, symbol: str, mark_price: float):
        """
        实时价格更新（由 FeatureEngine 转发）。
        用于更新未实现盈亏和检测止损。
        """
        pos = self._positions.get(symbol)
        if not pos:
            return

        pos.mark_price = mark_price

        # 计算未实现盈亏
        if pos.side == "LONG":
            pnl_pct = (mark_price - pos.entry_price) / pos.entry_price * 100
        else:
            pnl_pct = (pos.entry_price - mark_price) / pos.entry_price * 100

        pos.unrealized_pnl = pos.notional * pnl_pct / 100
        pos.unrealized_pnl_pct = pnl_pct
        pos.max_pnl_pct = max(pos.max_pnl_pct, pnl_pct)
        pos.min_pnl_pct = min(pos.min_pnl_pct, pnl_pct)

        # ── 止损 / 止盈检测（优先级：止损 > 固定止盈 > 追踪止盈）──
        # 使用仓位上的动态止损止盈（由门卫计算），而非全局配置
        pos_sl_pct = pos.stop_loss_pct
        pos_tp_pct = pos.take_profit_pct
        close_reason = ""

        # 1. 止损：浮亏超过阈值（使用仓位级别的动态止损）
        if pnl_pct <= -pos_sl_pct:
            close_reason = "stop_loss"
            logger.warning(
                f"[纸盘止损] {symbol} {pos.side} 亏损 {pnl_pct:.2f}% "
                f"触发止损线 -{pos_sl_pct}%({pos.sl_source})"
            )

        # 2. 固定止盈：浮盈达到目标（使用仓位级别的动态止盈）
        elif pos_tp_pct > 0 and pnl_pct >= pos_tp_pct:
            close_reason = "take_profit"
            logger.info(
                f"[纸盘止盈] {symbol} {pos.side} 盈利 {pnl_pct:.2f}% "
                f"触发止盈线 +{pos_tp_pct}%({pos.sl_source})"
            )

        # 3. 追踪止盈：浮盈曾超过激活阈值，且从最高点回撤超过比例
        elif (
            self.config.trailing_activate_pct > 0
            and pos.max_pnl_pct >= self.config.trailing_activate_pct
            and pnl_pct > 0  # 仍在盈利区（不与止损冲突）
        ):
            # 回撤比例 = (最高盈利 - 当前盈利) / 最高盈利 * 100
            drawback_pct = (pos.max_pnl_pct - pnl_pct) / pos.max_pnl_pct * 100
            if drawback_pct >= self.config.trailing_callback_pct:
                close_reason = "trailing_stop"
                logger.info(
                    f"[纸盘追踪止盈] {symbol} {pos.side} "
                    f"最高盈利 {pos.max_pnl_pct:.2f}% → 当前 {pnl_pct:.2f}% "
                    f"回撤 {drawback_pct:.0f}% ≥ {self.config.trailing_callback_pct}%"
                )

        # 触发平仓
        if close_reason:
            task = asyncio.ensure_future(
                self._close_position(symbol, close_reason.upper(), close_reason)
            )
            self._bg_tasks = getattr(self, '_bg_tasks', set())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)

    # ──────────────────────────────────────
    # 开仓 / 平仓
    # ──────────────────────────────────────

    async def _open_position(
        self, symbol: str, signal: str, score: float, confidence: float,
        gate_result=None,
    ):
        """虚拟开仓（支持门卫动态止损止盈）"""
        if symbol in self._positions:
            return  # 已有仓位

        # 入场评分过滤（绝对值必须 >= 阈值，避免弱信号入场）
        if abs(score) < self.config.min_entry_score:
            self._log_signal(symbol, signal, score, confidence,
                             "skip", f"评分不足: |{score:.3f}| < {self.config.min_entry_score}")
            logger.debug(
                f"[纸盘] {symbol} 评分|{score:.3f}| < {self.config.min_entry_score}，不开仓"
            )
            return

        # 冷却期检查（防止 whipsaw 连续反转）
        now = time.time()
        last_open = self._last_open_time.get(symbol, 0)
        if now - last_open < self.config.cooldown_s:
            remaining = self.config.cooldown_s - (now - last_open)
            self._log_signal(symbol, signal, score, confidence,
                             "skip", f"冷却中，剩余 {remaining:.0f}s")
            logger.debug(f"[纸盘] {symbol} 冷却中，剩余 {remaining:.0f}s")
            return

        # 获取实时标记价格
        mark_price = await self._fetch_mark_price(symbol)
        if not mark_price:
            logger.warning(f"[纸盘] 无法获取 {symbol} 标记价格，跳过开仓")
            return

        # 计算仓位（基于真实 equity）
        equity = self._calc_equity()
        margin = equity * self.config.position_pct / 100
        notional = margin * self.config.leverage
        quantity = notional / mark_price

        # 模拟滑点（买入价偏高，卖出价偏低）
        side = "LONG" if signal in ("STRONG_BUY", "BUY") else "SHORT"
        if side == "LONG":
            entry_price = mark_price * (1 + self.config.slippage_pct / 100)
        else:
            entry_price = mark_price * (1 - self.config.slippage_pct / 100)

        # 扣除保证金 + 开仓手续费
        fee = notional * self.config.fee_pct / 100
        total_cost = margin + fee
        if total_cost > self._balance:
            logger.warning(f"[纸盘] {symbol} 余额不足: 需要${total_cost:.2f} 可用${self._balance:.2f}")
            return
        self._balance -= total_cost

        # ── 动态止损止盈（门卫建议 > 固定配置）──
        if gate_result and gate_result.passed:
            actual_sl_pct = gate_result.suggested_stop_loss_pct
            actual_tp_pct = gate_result.suggested_take_profit_pct
            sl_source = "门卫动态"
        else:
            actual_sl_pct = self.config.stop_loss_pct
            actual_tp_pct = self.config.take_profit_pct
            sl_source = "固定配置"

        # 止损价
        if side == "LONG":
            stop_loss = entry_price * (1 - actual_sl_pct / 100)
        else:
            stop_loss = entry_price * (1 + actual_sl_pct / 100)

        pos = PaperPosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            notional=notional,
            leverage=self.config.leverage,
            margin=margin,
            entry_time=time.time(),
            signal=signal,
            score=score,
            confidence=confidence,
            stop_loss_price=stop_loss,
            stop_loss_pct=actual_sl_pct,
            take_profit_pct=actual_tp_pct,
            sl_source=sl_source,
            mark_price=mark_price,
        )
        self._positions[symbol] = pos
        self._last_open_time[symbol] = time.time()
        self._save_state()

        # 记录开仓日志
        self._log_signal(symbol, signal, score, confidence, "open",
                         f"开仓 {side} 价格=${entry_price:.2f} 仓位=${notional:.0f} "
                         f"杠杆={self.config.leverage}x 止损=${stop_loss:.2f}({sl_source}) "
                         f"止盈={actual_tp_pct:.2f}%",
                         extra={
                             "side": side,
                             "entry_price": round(entry_price, 2),
                             "stop_loss": round(stop_loss, 2),
                             "stop_loss_pct": actual_sl_pct,
                             "take_profit_pct": actual_tp_pct,
                             "sl_source": sl_source,
                             "notional": round(notional, 2),
                             "margin": round(margin, 2),
                             "leverage": self.config.leverage,
                         })

        logger.info(
            f"[纸盘开仓] {symbol} {side} 价格=${entry_price:.2f} "
            f"仓位=${notional:.0f} 杠杆={self.config.leverage}x "
            f"止损=${stop_loss:.2f}({sl_source} {actual_sl_pct:.2f}%) "
            f"止盈={actual_tp_pct:.2f}% 信号={signal} "
            f"conf={confidence:.1%} 手续费=${fee:.2f}"
        )

    async def _close_position(self, symbol: str, signal: str, reason: str):
        """虚拟平仓"""
        pos = self._positions.get(symbol)
        if not pos:
            return

        # 获取实时标记价格
        mark_price = await self._fetch_mark_price(symbol)
        if not mark_price:
            mark_price = pos.mark_price  # fallback
        if not mark_price:
            logger.warning(f"[纸盘] 无法获取 {symbol} 平仓价格")
            return

        # 模拟滑点（平多偏低，平空偏高）
        if pos.side == "LONG":
            exit_price = mark_price * (1 - self.config.slippage_pct / 100)
        else:
            exit_price = mark_price * (1 + self.config.slippage_pct / 100)

        # 计算盈亏
        if pos.side == "LONG":
            gross_pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.quantity

        # 平仓手续费
        fee = pos.notional * self.config.fee_pct / 100
        slippage_cost = pos.notional * self.config.slippage_pct / 100 * 2  # 开+平

        net_pnl = gross_pnl - fee
        net_pnl_pct = net_pnl / pos.margin * 100 if pos.margin else 0

        # 更新余额
        self._balance += pos.margin + net_pnl
        del self._positions[symbol]
        self._save_state()

        now = time.time()
        duration = now - pos.entry_time

        # 保存交易记录
        trade = PaperTrade(
            id=0,
            symbol=symbol,
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=pos.quantity,
            notional=pos.notional,
            leverage=pos.leverage,
            entry_time=pos.entry_time,
            exit_time=now,
            signal_entry=pos.signal,
            signal_exit=signal,
            score_entry=pos.score,
            confidence_entry=pos.confidence,
            gross_pnl=gross_pnl,
            fee_cost=fee,
            slippage_cost=slippage_cost,
            net_pnl=net_pnl,
            net_pnl_pct=net_pnl_pct,
            max_pnl_pct=pos.max_pnl_pct,
            min_pnl_pct=pos.min_pnl_pct,
            duration_s=duration,
            exit_reason=reason,
            sl_source=pos.sl_source,
        )
        self._save_trade(trade)
        self._save_equity_snapshot()

        result = "盈利" if net_pnl >= 0 else "亏损"
        logger.info(
            f"[纸盘平仓] {symbol} {pos.side} {result} "
            f"入场=${pos.entry_price:.2f} 出场=${exit_price:.2f} "
            f"净利=${net_pnl:.2f}({net_pnl_pct:+.2f}%) "
            f"持仓={duration:.0f}s 原因={reason}"
        )

    # ──────────────────────────────────────
    # 标记价格
    # ──────────────────────────────────────

    async def _fetch_mark_price(self, symbol: str) -> Optional[float]:
        """从币安获取标记价格"""
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    BINANCE_MARK_PRICE_URL,
                    params={"symbol": symbol},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return float(data["markPrice"])
        except Exception as e:
            logger.warning(f"获取标记价格失败: {e}")
        return None

    # ──────────────────────────────────────
    # 资金曲线录制（定时调用）
    # ──────────────────────────────────────

    async def equity_loop(self, interval_s: int = 60):
        """每分钟记录一次资金曲线快照"""
        while True:
            try:
                self._save_equity_snapshot()
            except Exception as e:
                logger.warning(f"资金曲线记录失败: {e}")
            await asyncio.sleep(interval_s)

    # ──────────────────────────────────────
    # 查询接口
    # ──────────────────────────────────────

    def get_status(self) -> dict:
        """获取纸盘交易完整状态"""
        unrealized = self._calc_unrealized()
        equity = self._calc_equity()
        stats = self._calc_stats()

        positions = []
        for p in self._positions.values():
            positions.append({
                "symbol": p.symbol,
                "side": p.side,
                "entry_price": round(p.entry_price, 2),
                "mark_price": round(p.mark_price, 2),
                "quantity": round(p.quantity, 6),
                "notional": round(p.notional, 2),
                "leverage": p.leverage,
                "unrealized_pnl": round(p.unrealized_pnl, 2),
                "unrealized_pnl_pct": round(p.unrealized_pnl_pct, 2),
                "stop_loss_price": round(p.stop_loss_price, 2),
                "signal": p.signal,
                "confidence": round(p.confidence, 3),
                "duration_s": round(time.time() - p.entry_time),
            })

        return {
            "config": asdict(self.config),
            "account": {
                "initial_balance": self.config.initial_balance,
                "balance": round(self._balance, 2),
                "equity": round(equity, 2),
                "unrealized_pnl": round(unrealized, 2),
                "return_pct": round((equity - self.config.initial_balance) / self.config.initial_balance * 100, 2),
                "total_pnl_usdt": round(equity - self.config.initial_balance, 2),
            },
            "positions": positions,
            "stats": asdict(stats),
        }

    def get_trades(self, limit: int = 50) -> list[dict]:
        """获取历史交易记录"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM paper_trades ORDER BY exit_time DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_equity_curve(self, limit: int = 1440) -> list[dict]:
        """获取资金曲线（默认最近 24 小时，每分钟一个点）"""
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM paper_equity ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in reversed(rows)]

    def _calc_stats(self) -> PaperStats:
        """计算统计指标"""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT net_pnl, net_pnl_pct, fee_cost, slippage_cost FROM paper_trades"
        ).fetchall()
        conn.close()

        equity = self._calc_equity()
        stats = PaperStats(
            current_balance=round(self._balance, 2),
            equity=round(equity, 2),
        )

        if not rows:
            return stats

        pnls = [r[0] for r in rows]
        pnl_pcts = [r[1] for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        stats.total_trades = len(rows)
        stats.winning_trades = len(wins)
        stats.losing_trades = len(losses)
        stats.win_rate = round(len(wins) / len(rows) * 100, 1) if rows else 0
        stats.total_net_pnl = round(sum(pnls), 2)
        stats.total_fee_cost = round(sum(r[2] for r in rows), 2)
        stats.total_slippage_cost = round(sum(r[3] for r in rows), 2)
        stats.avg_pnl_pct = round(sum(pnl_pcts) / len(pnl_pcts), 2) if pnl_pcts else 0
        stats.avg_win_pct = round(sum(p for p in pnl_pcts if p > 0) / len(wins), 2) if wins else 0
        stats.avg_loss_pct = round(sum(p for p in pnl_pcts if p <= 0) / len(losses), 2) if losses else 0
        stats.return_pct = round(
            (equity - self.config.initial_balance) / self.config.initial_balance * 100, 2
        )

        # 盈亏比
        total_win = sum(wins) if wins else 0
        total_loss = abs(sum(losses)) if losses else 0
        stats.profit_factor = round(total_win / total_loss, 2) if total_loss > 0 else float('inf')

        # 最大回撤
        peak = self.config.initial_balance
        max_dd = 0
        running_balance = self.config.initial_balance
        for pnl in pnls:
            running_balance += pnl
            peak = max(peak, running_balance)
            dd = (peak - running_balance) / peak * 100
            max_dd = max(max_dd, dd)
        stats.max_drawdown_pct = round(max_dd, 2)

        # 简化 Sharpe（假设日频，年化）
        if len(pnl_pcts) >= 2:
            mean_r = sum(pnl_pcts) / len(pnl_pcts)
            var_r = sum((r - mean_r) ** 2 for r in pnl_pcts) / (len(pnl_pcts) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 0
            # 假设平均每天 N 笔，年化 = sqrt(252*N)
            if std_r > 0:
                stats.sharpe_ratio = round(mean_r / std_r * math.sqrt(252), 2)

        return stats

    # ──────────────────────────────────────
    # 配置更新 / 重置
    # ──────────────────────────────────────

    def update_config(self, updates: dict) -> dict:
        """更新配置"""
        for key, val in updates.items():
            if hasattr(self.config, key):
                setattr(self.config, key, val)
        return self.get_status()

    def reset(self):
        """重置纸盘账户（清空所有数据）"""
        self._positions.clear()
        self._balance = self.config.initial_balance
        self._signal_log.clear()

        conn = sqlite3.connect(str(self._db_path))
        conn.execute("DELETE FROM paper_trades")
        conn.execute("DELETE FROM paper_equity")
        conn.execute("DELETE FROM paper_state")
        conn.commit()
        conn.close()

        self._save_state()
        logger.info(f"[纸盘] 已重置，初始资金 ${self.config.initial_balance}")
        return self.get_status()
