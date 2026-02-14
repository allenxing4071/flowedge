"""
异常检测器 — 识别市场极端状态和关键事件。

不做方向判断，只标记"此刻有什么异常"。
用于：
  1. UI 上弹出告警卡片
  2. 辅助信号引擎调整置信度
  3. 记录异常事件历史

检测维度：
  - 清算级联（cascade liquidation）
  - VPIN 异常飙升（知情交易激增）
  - 资金费率极端（市场过度拥挤）
  - 假墙频繁（盘口操纵）
  - OI 异常变化（大资金进出场）
  - 散户/鲸鱼极端分歧
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from flowedge.optimizer.param_registry import ParamRegistry


@dataclass
class AnomalyEvent:
    """单个异常事件"""
    type: str            # 异常类型标识
    severity: str        # LOW / MEDIUM / HIGH / CRITICAL
    title: str           # 简短中文标题
    description: str     # 中文描述
    metric_name: str     # 触发的指标名
    metric_value: float  # 触发时的指标值
    threshold: float     # 阈值
    timestamp_ms: int    # 发生时间
    symbol: str = ""


@dataclass
class AnomalySnapshot:
    """当前异常状态快照"""
    active_count: int = 0
    active_events: list = field(default_factory=list)
    recent_events: list = field(default_factory=list)  # 最近 5 分钟内的事件
    risk_level: str = "NORMAL"  # NORMAL / ELEVATED / HIGH / EXTREME


class AnomalyDetector:
    """
    异常检测器：扫描特征快照，识别极端状态。
    每次 detect() 调用时全量扫描，不依赖状态。
    维护最近 5 分钟事件历史用于 UI 显示。

    所有检测阈值从 ParamRegistry 读取（唯一数据源），支持热更新。
    """

    # ── 所有检测阈值已迁移到 ParamRegistry，此处不再定义默认值 ──

    def __init__(self, registry: "ParamRegistry", history_window_s: int = 300):
        if not registry:
            raise ValueError("AnomalyDetector 必须传入 ParamRegistry，禁止无 Registry 运行")
        self._history: deque = deque(maxlen=200)
        self._window_s = history_window_s
        self._registry = registry

        self.THRESHOLDS = registry.get_detector_thresholds()
        registry.subscribe(self._on_params_updated)

    def _on_params_updated(self, all_values: dict[str, float]) -> None:
        """ParamRegistry 参数变更回调 — 热更新阈值"""
        if self._registry:
            self.THRESHOLDS = self._registry.get_detector_thresholds()

    def detect(self, features: dict, symbol: str = "") -> AnomalySnapshot:
        """
        扫描特征快照，检测所有异常。

        参数:
            features: 单币种特征字典（来自 engine.get_snapshot()[symbol]）
            symbol: 币种标识

        返回:
            AnomalySnapshot
        """
        now_ms = int(time.time() * 1000)
        events = []

        # 1. VPIN 检测
        vpin_data = features.get("vpin", {})
        vpin_val = vpin_data.get("vpin", 0)
        if vpin_val >= self.THRESHOLDS["vpin_critical"]:
            events.append(AnomalyEvent(
                type="vpin_critical", severity="CRITICAL",
                title="VPIN 极端飙升",
                description=f"知情交易概率 {vpin_val:.3f}，可能有重大未公开消息",
                metric_name="vpin", metric_value=vpin_val,
                threshold=self.THRESHOLDS["vpin_critical"],
                timestamp_ms=now_ms, symbol=symbol,
            ))
        elif vpin_val >= self.THRESHOLDS["vpin_high"]:
            events.append(AnomalyEvent(
                type="vpin_high", severity="MEDIUM",
                title="VPIN 升高",
                description=f"知情交易概率 {vpin_val:.3f}，市场不确定性增加",
                metric_name="vpin", metric_value=vpin_val,
                threshold=self.THRESHOLDS["vpin_high"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # 2. 资金费率检测
        funding = features.get("funding", {})
        rate = abs(funding.get("current_rate", 0))
        if rate >= self.THRESHOLDS["funding_extreme"]:
            direction = "多头" if funding.get("current_rate", 0) > 0 else "空头"
            events.append(AnomalyEvent(
                type="funding_extreme", severity="HIGH",
                title=f"资金费率极端 ({direction}拥挤)",
                description=f"费率 {funding.get('current_rate', 0):.4f}%，{direction}过度拥挤",
                metric_name="funding_rate", metric_value=rate,
                threshold=self.THRESHOLDS["funding_extreme"],
                timestamp_ms=now_ms, symbol=symbol,
            ))
        elif rate >= self.THRESHOLDS["funding_elevated"]:
            events.append(AnomalyEvent(
                type="funding_elevated", severity="LOW",
                title="资金费率偏高",
                description=f"费率 {funding.get('current_rate', 0):.4f}%",
                metric_name="funding_rate", metric_value=rate,
                threshold=self.THRESHOLDS["funding_elevated"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # 3. 清算级联检测
        liq = features.get("liquidation", {})
        cascade = liq.get("cascade_level", "none")
        if cascade in ("major", "extreme"):
            net = liq.get("net_liq_1m", 0)
            direction = "多头" if net > 0 else "空头"
            severity = "CRITICAL" if cascade == "extreme" else "HIGH"
            events.append(AnomalyEvent(
                type="liquidation_cascade", severity=severity,
                title=f"清算级联 ({direction})",
                description=f"{direction}清算 ${abs(net):,.0f} 级联等级={cascade}",
                metric_name="net_liq_1m", metric_value=abs(net),
                threshold=self.THRESHOLDS["liq_major_usdt"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # 4. 假墙检测
        depth = features.get("depth_change", {})
        walls = depth.get("wall_events_30s", 0)
        if walls >= self.THRESHOLDS["wall_frequent"]:
            events.append(AnomalyEvent(
                type="fake_wall", severity="MEDIUM",
                title="频繁假墙出现",
                description=f"30秒内 {walls} 次假墙事件，疑似盘口操纵",
                metric_name="wall_events_30s", metric_value=walls,
                threshold=self.THRESHOLDS["wall_frequent"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # 5. OI 异常变化
        oi = features.get("open_interest", {})
        oi_change = abs(oi.get("oi_change_pct", 0))
        if oi_change >= self.THRESHOLDS["oi_surge_pct"]:
            events.append(AnomalyEvent(
                type="oi_surge", severity="MEDIUM",
                title="持仓量骤变",
                description=f"OI 变化 {oi.get('oi_change_pct', 0):.1f}%，大资金进出场",
                metric_name="oi_change_pct", metric_value=oi_change,
                threshold=self.THRESHOLDS["oi_surge_pct"],
                timestamp_ms=now_ms, symbol=symbol,
            ))
        global_oi = abs(oi.get("global_oi_change_1h", 0))
        if global_oi >= self.THRESHOLDS["oi_global_surge"]:
            events.append(AnomalyEvent(
                type="oi_global_surge", severity="HIGH",
                title="全网持仓量骤变",
                description=f"全网 OI 1h 变化 {oi.get('global_oi_change_1h', 0):.1f}%",
                metric_name="global_oi_change_1h", metric_value=global_oi,
                threshold=self.THRESHOLDS["oi_global_surge"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # 6. 情绪极端
        sent = features.get("sentiment", {})
        fng = sent.get("fear_greed_value", 50)
        if fng <= self.THRESHOLDS["fng_extreme_low"]:
            events.append(AnomalyEvent(
                type="extreme_fear", severity="MEDIUM",
                title="市场极端恐惧",
                description=f"恐慌贪婪指数 {fng}（极端恐惧），可能是抄底机会",
                metric_name="fear_greed", metric_value=fng,
                threshold=self.THRESHOLDS["fng_extreme_low"],
                timestamp_ms=now_ms, symbol=symbol,
            ))
        elif fng >= self.THRESHOLDS["fng_extreme_high"]:
            events.append(AnomalyEvent(
                type="extreme_greed", severity="MEDIUM",
                title="市场极端贪婪",
                description=f"恐慌贪婪指数 {fng}（极端贪婪），注意回调风险",
                metric_name="fear_greed", metric_value=fng,
                threshold=self.THRESHOLDS["fng_extreme_high"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        divergence = abs(sent.get("divergence", 0))
        if divergence >= self.THRESHOLDS["divergence_extreme"]:
            events.append(AnomalyEvent(
                type="sentiment_divergence", severity="LOW",
                title="散户/鲸鱼极端分歧",
                description=f"散户鲸鱼多空比分歧 {sent.get('divergence', 0):.2f}",
                metric_name="divergence", metric_value=divergence,
                threshold=self.THRESHOLDS["divergence_extreme"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # 7. OFI 极端
        ofi = features.get("ofi", {})
        z = abs(ofi.get("z_score_30s", 0))
        if z >= self.THRESHOLDS["ofi_extreme_z"]:
            direction = "买方" if ofi.get("z_score_30s", 0) > 0 else "卖方"
            events.append(AnomalyEvent(
                type="ofi_extreme", severity="MEDIUM",
                title=f"订单流极端不平衡 ({direction})",
                description=f"OFI z-score={ofi.get('z_score_30s', 0):.2f}，{direction}订单流极端",
                metric_name="ofi_z_score", metric_value=z,
                threshold=self.THRESHOLDS["ofi_extreme_z"],
                timestamp_ms=now_ms, symbol=symbol,
            ))

        # ── 写入历史 ──
        for evt in events:
            self._history.append(evt)

        # ── 收集近期事件 ──
        cutoff = now_ms - self._window_s * 1000
        recent = [e for e in self._history if e.timestamp_ms >= cutoff]

        # ── 综合风险等级 ──
        risk_level = self._assess_risk(events)

        return AnomalySnapshot(
            active_count=len(events),
            active_events=events,
            recent_events=recent[-20:],  # 最多 20 条
            risk_level=risk_level,
        )

    def _assess_risk(self, events: list) -> str:
        """根据当前活跃事件评估综合风险等级"""
        if not events:
            return "NORMAL"

        severities = [e.severity for e in events]
        if "CRITICAL" in severities:
            return "EXTREME"
        if severities.count("HIGH") >= 2:
            return "EXTREME"
        if "HIGH" in severities:
            return "HIGH"
        if severities.count("MEDIUM") >= 3:
            return "HIGH"
        if "MEDIUM" in severities:
            return "ELEVATED"
        return "NORMAL"
