"""
信号推送器 — 强信号自动提交到 KKline 半自动审批系统。

职责：
  1. 监听信号引擎的信号变化
  2. 当信号满足条件（STRONG_BUY/STRONG_SELL + 置信度 >= 阈值）时
  3. 自动通过 KKline API 创建 pending_signal
  4. 用户在 KKline Admin 或 FlowEdge 驾驶舱审批/拒绝

安全边界（不可绕过）：
  - 默认为 semi-auto 模式（用户审批后才执行）
  - 置信度阈值默认 60%，可通过 API 调整
  - 同一币种在同方向不重复推送（冷却 15 分钟）
  - 所有推送记录可追溯
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import aiohttp

logger = logging.getLogger("flowedge.pusher")

# 默认配置
DEFAULT_CONFIDENCE_THRESHOLD = 0.60  # 置信度阈值
DEFAULT_COOLDOWN_S = 900             # 15 分钟冷却
STRONG_SIGNALS = {"STRONG_BUY", "STRONG_SELL"}


@dataclass
class PusherConfig:
    """推送器配置（可通过 API 动态调整）"""
    enabled: bool = True
    mode: str = "semi-auto"          # semi-auto（审批后执行）或 auto（直接执行，需用户开启）
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    cooldown_s: int = DEFAULT_COOLDOWN_S
    # 允许推送的信号类型（默认只推送强信号）
    push_signals: set = field(default_factory=lambda: {"STRONG_BUY", "STRONG_SELL"})
    # 默认交易参数
    default_leverage: int = 10
    default_position_pct: float = 5.0  # 账户余额的 5%
    default_stop_loss_pct: float = 2.0


@dataclass
class PushRecord:
    """推送记录"""
    symbol: str
    signal: str
    score: float
    confidence: float
    push_time: float
    kkline_signal_id: Optional[int] = None
    success: bool = False
    error: Optional[str] = None


class SignalPusher:
    """
    信号推送器：强信号 → KKline pending_signals
    """

    def __init__(
        self,
        kkline_url: Optional[str] = None,
        opus_key: Optional[str] = None,
    ):
        import os
        self._kkline_url = kkline_url or os.getenv("KKLINE_URL", "https://kk.kline007.top")
        self._opus_key = opus_key or os.getenv("OPUS_CONTROL_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None
        self._config = PusherConfig()
        # 冷却追踪：{(symbol, side): last_push_timestamp}
        self._cooldowns: dict[tuple[str, str], float] = {}
        # 推送历史
        self._history: list[PushRecord] = []
        self._push_count = 0
        self._success_count = 0

    @property
    def config(self) -> PusherConfig:
        return self._config

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Content-Type": "application/json",
                    "X-Opus-Key": self._opus_key,
                }
            )
        return self._session

    def _check_cooldown(self, symbol: str, side: str) -> bool:
        """检查是否在冷却期内"""
        key = (symbol, side)
        last_push = self._cooldowns.get(key, 0)
        return (time.time() - last_push) >= self._config.cooldown_s

    def _record_push(self, symbol: str, side: str) -> None:
        """记录推送时间"""
        self._cooldowns[(symbol, side)] = time.time()

    async def on_signal_change(
        self,
        symbol: str,
        signal: str,
        score: float,
        confidence: float,
    ) -> Optional[PushRecord]:
        """
        信号变化时调用。检查是否满足推送条件，满足则推送到 KKline。
        """
        if not self._config.enabled:
            return None

        # 只推送配置中允许的信号类型
        if signal not in self._config.push_signals:
            return None

        # 置信度检查
        if confidence < self._config.confidence_threshold:
            logger.debug(
                f"[Pusher] {symbol} {signal} 置信度 {confidence:.1%} < "
                f"阈值 {self._config.confidence_threshold:.1%}，跳过"
            )
            return None

        # 确定方向
        side = "long" if "BUY" in signal else "short"

        # 冷却检查
        if not self._check_cooldown(symbol, side):
            logger.debug(f"[Pusher] {symbol} {side} 在冷却期内，跳过")
            return None

        # 推送到 KKline
        record = PushRecord(
            symbol=symbol,
            signal=signal,
            score=score,
            confidence=confidence,
            push_time=time.time(),
        )

        try:
            session = await self._get_session()

            payload = {
                "symbol": symbol,
                "side": side,
                "confidence": round(confidence, 3),
                "leverage": self._config.default_leverage,
                "position_pct": self._config.default_position_pct,
                "signal_data": {
                    "source": "flowedge",
                    "signal": signal,
                    "score": round(score, 4),
                    "confidence": round(confidence, 3),
                    "mode": self._config.mode,
                },
            }

            # 调用 KKline 的信号提交 API
            # KKline 内部的 ws_monitor 会使用 db.insert_pending_signal
            # 我们需要通过 KKline 提供的外部 API 来提交
            # 使用 /api/trade/open 预提交模式（semi-auto 时只创建 pending，不执行）
            url = f"{self._kkline_url}/api/signals/submit"
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    record.success = True
                    record.kkline_signal_id = data.get("signal_id")
                    self._record_push(symbol, side)
                    self._success_count += 1
                    logger.info(
                        f"[Pusher] 推送成功: {symbol} {signal} → KKline "
                        f"signal_id={record.kkline_signal_id} "
                        f"conf={confidence:.1%}"
                    )
                elif resp.status == 404:
                    # KKline 尚未部署 /api/signals/submit 端点
                    # 回退：直接以标准格式调用 trade/open（如果是 auto 模式）
                    record.error = "KKline 信号提交接口不可用（404），请确认 KKline 已更新"
                    logger.warning(f"[Pusher] {record.error}")
                else:
                    body = await resp.text()
                    record.error = f"HTTP {resp.status}: {body[:200]}"
                    logger.warning(f"[Pusher] 推送失败: {record.error}")

        except Exception as e:
            record.error = str(e)
            logger.warning(f"[Pusher] 推送异常: {e}")

        self._push_count += 1
        self._history.append(record)
        # 保留最近 200 条
        if len(self._history) > 200:
            self._history = self._history[-200:]

        return record

    def get_status(self) -> dict:
        """获取推送器状态"""
        return {
            "enabled": self._config.enabled,
            "mode": self._config.mode,
            "confidence_threshold": self._config.confidence_threshold,
            "cooldown_s": self._config.cooldown_s,
            "push_signals": list(self._config.push_signals),
            "default_leverage": self._config.default_leverage,
            "default_position_pct": self._config.default_position_pct,
            "default_stop_loss_pct": self._config.default_stop_loss_pct,
            "stats": {
                "total_pushes": self._push_count,
                "successful": self._success_count,
                "failed": self._push_count - self._success_count,
            },
            "recent_pushes": [
                {
                    "symbol": r.symbol,
                    "signal": r.signal,
                    "score": r.score,
                    "confidence": r.confidence,
                    "success": r.success,
                    "error": r.error,
                    "kkline_signal_id": r.kkline_signal_id,
                    "push_time": r.push_time,
                }
                for r in reversed(self._history[-10:])
            ],
        }

    def update_config(self, updates: dict) -> dict:
        """更新推送器配置"""
        cfg = self._config
        if "enabled" in updates:
            cfg.enabled = bool(updates["enabled"])
        if "mode" in updates:
            if updates["mode"] in ("semi-auto", "auto"):
                cfg.mode = updates["mode"]
        if "confidence_threshold" in updates:
            v = float(updates["confidence_threshold"])
            cfg.confidence_threshold = max(0.1, min(1.0, v))
        if "cooldown_s" in updates:
            cfg.cooldown_s = max(60, int(updates["cooldown_s"]))
        if "default_leverage" in updates:
            cfg.default_leverage = max(1, min(20, int(updates["default_leverage"])))
        if "default_position_pct" in updates:
            cfg.default_position_pct = max(1.0, min(10.0, float(updates["default_position_pct"])))
        if "default_stop_loss_pct" in updates:
            cfg.default_stop_loss_pct = max(0.5, min(15.0, float(updates["default_stop_loss_pct"])))

        logger.info(f"[Pusher] 配置已更新: enabled={cfg.enabled} mode={cfg.mode} "
                     f"threshold={cfg.confidence_threshold:.1%}")
        return self.get_status()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
