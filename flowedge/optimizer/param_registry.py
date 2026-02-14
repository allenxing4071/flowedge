"""
参数注册中心 — 所有可优化参数的唯一数据源。

核心职责：
  1. 统一管理 ~80 个可优化参数（因子权重、信号阈值、门卫阈值、特征参数等）
  2. 提供 get/set/get_all/get_space 接口
  3. 支持快照/回滚（每次变更前自动保存）
  4. 变更时通知订阅者（scorer/gate/detector 热更新）
  5. 为 Optuna 提供参数搜索空间定义

设计原则：
  - 所有参数都有 name, value, min, max, type, group, description
  - 参数持久化到 JSON 文件（data/optimizer/params.json）
  - 快照保存到 data/optimizer/snapshots/
  - 向后兼容：如果 JSON 文件不存在，使用默认值（与当前硬编码值一致）
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# ── 参数定义 ──

@dataclass
class ParamDef:
    """单个可优化参数的定义"""
    name: str               # 唯一标识，如 "weight_cvd"
    value: float            # 当前值
    min_val: float          # 最小值（Optuna 搜索下界）
    max_val: float          # 最大值（Optuna 搜索上界）
    step: Optional[float]   # 步长（None 表示连续）
    param_type: str         # "float" / "int" / "bool"
    group: str              # 参数组：weights / signal_thresholds / gate / detector / feature / paper
    description: str        # 中文说明
    is_optimizable: bool = True  # 是否参与优化

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ParamDef":
        return cls(**d)


# ── 默认参数定义 ──
# 与当前硬编码值完全一致，确保向后兼容

def _build_default_params() -> dict[str, ParamDef]:
    """构建所有默认参数定义"""
    params: dict[str, ParamDef] = {}

    # ═══════════════════════════════════════
    # Group 1: 因子权重 (14 个，归一化约束)
    # ═══════════════════════════════════════
    weight_defs = {
        "weight_cvd":            (0.12, 0.01, 0.30, "CVD 成交量delta权重"),
        "weight_ofi":            (0.12, 0.01, 0.30, "OFI 订单流不平衡权重"),
        "weight_book_imbalance": (0.08, 0.01, 0.20, "L1 盘口压力权重"),
        "weight_large_trade":    (0.10, 0.01, 0.25, "大单资金流权重"),
        "weight_depth_change":   (0.06, 0.01, 0.15, "深度变化+假墙权重"),
        "weight_vwap":           (0.08, 0.01, 0.20, "VWAP 偏离度权重"),
        "weight_volume_profile": (0.07, 0.01, 0.20, "Volume Profile 权重"),
        "weight_absorption":     (0.10, 0.01, 0.25, "吸收检测权重"),
        "weight_funding":        (0.08, 0.01, 0.20, "资金费率权重"),
        "weight_liquidation":    (0.06, 0.01, 0.15, "清算级联权重"),
        "weight_sentiment":      (0.05, 0.01, 0.15, "多空情绪权重"),
        "weight_trend":          (0.05, 0.01, 0.15, "趋势一致性权重"),
        "weight_vpin":           (0.02, 0.00, 0.10, "VPIN 知情交易权重"),
        "weight_oi":             (0.01, 0.00, 0.10, "持仓量变化权重"),
    }
    for name, (val, lo, hi, desc) in weight_defs.items():
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=0.01, param_type="float", group="weights", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 2: 信号阈值 (入场/退出/反转)
    # ═══════════════════════════════════════
    signal_defs = {
        # 入场阈值
        "thresh_strong_buy":     (0.40, 0.20, 0.60, "强买入场阈值"),
        "thresh_buy":            (0.15, 0.05, 0.35, "买入场阈值"),
        "thresh_sell":           (-0.15, -0.35, -0.05, "卖入场阈值"),
        "thresh_strong_sell":    (-0.40, -0.60, -0.20, "强卖入场阈值"),
        # 退出阈值（滞后区）
        "thresh_buy_exit":       (0.08, 0.02, 0.14, "买退出阈值"),
        "thresh_sell_exit":      (-0.08, -0.14, -0.02, "卖退出阈值"),
        "thresh_strong_buy_exit": (0.25, 0.15, 0.38, "强买退出阈值"),
        "thresh_strong_sell_exit": (-0.25, -0.38, -0.15, "强卖退出阈值"),
        # 反转阈值
        "thresh_buy_to_sell":    (-0.25, -0.40, -0.15, "买→卖反转阈值"),
        "thresh_sell_to_buy":    (0.25, 0.15, 0.40, "卖→买反转阈值"),
    }
    for name, (val, lo, hi, desc) in signal_defs.items():
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=0.01, param_type="float", group="signal_thresholds", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 3: 环境乘数 (5 环境 × 2 类别)
    # ═══════════════════════════════════════
    regime_defs = {
        "regime_trending_micro":  (1.2, 0.5, 2.0, "趋势环境-微观因子乘数"),
        "regime_trending_macro":  (0.8, 0.3, 1.5, "趋势环境-宏观因子乘数"),
        "regime_breakout_micro":  (1.3, 0.5, 2.0, "突破环境-微观因子乘数"),
        "regime_breakout_macro":  (0.7, 0.3, 1.5, "突破环境-宏观因子乘数"),
        "regime_ranging_micro":   (0.8, 0.3, 1.5, "震荡环境-微观因子乘数"),
        "regime_ranging_macro":   (1.2, 0.5, 2.0, "震荡环境-宏观因子乘数"),
        "regime_extreme_micro":   (0.5, 0.1, 1.0, "极端环境-微观因子乘数"),
        "regime_extreme_macro":   (0.5, 0.1, 1.0, "极端环境-宏观因子乘数"),
        "regime_unclear_micro":   (0.7, 0.3, 1.2, "不明确环境-微观因子乘数"),
        "regime_unclear_macro":   (0.7, 0.3, 1.2, "不明确环境-宏观因子乘数"),
    }
    for name, (val, lo, hi, desc) in regime_defs.items():
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=0.1, param_type="float", group="regime_multipliers", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 4: 门卫阈值
    # ═══════════════════════════════════════
    gate_defs = {
        # Layer 1: 环境分类
        "gate_trending_min_alignment":  (3.0, 1.5, 5.0, "趋势最小对齐分数"),
        "gate_ranging_max_alignment":   (1.0, 0.3, 2.0, "震荡最大对齐分数"),
        "gate_extreme_band_width":      (2.0, 1.0, 4.0, "极端环境波动带宽%"),
        "gate_breakout_min_alignment":  (2.0, 1.0, 4.0, "突破最小对齐分数"),
        # Layer 2: 位置过滤
        "gate_vwap_near_pct":           (0.3, 0.1, 0.8, "VWAP 附近判定%"),
        "gate_poc_near_pct":            (0.15, 0.05, 0.40, "POC 附近判定%"),
        "gate_va_edge_threshold":       (0.1, 0.03, 0.25, "VA 边缘判定比例"),
        "gate_hvn_near_pct":            (0.2, 0.05, 0.50, "HVN 附近判定%"),
        # Layer 3: 行为确认
        "gate_min_absorption_events":   (3, 1, 10, "最小吸收事件数"),
        "gate_min_large_trade_flow":    (50000, 10000, 200000, "最小大单净流$"),
        # Layer 4: 方向确认（初始放宽，让纸盘积累数据，后续由优化引擎自动收紧）
        "gate_min_score":               (0.10, 0.05, 0.50, "门卫最小|score|"),
        "gate_min_confidence":          (0.25, 0.10, 0.80, "门卫最小置信度"),
        # Layer 2 补充
        "gate_vwap_band_near_pct":      (0.15, 0.05, 0.50, "VWAP带边界附近判定%"),
        # 止损止盈
        "gate_max_stop_loss_pct":       (2.0, 0.5, 5.0, "最大止损%"),
        "gate_min_stop_loss_pct":       (0.3, 0.1, 1.0, "最小止损%"),
        "gate_stop_loss_buffer_pct":    (0.1, 0.02, 0.30, "止损缓冲%"),
        # Layer 0: 时间节点过滤（布尔用 0/1 表示）
        "gate_time_filter_enabled":     (1, 0, 1, "30分钟节点过滤开关(0关/1开)"),
        "gate_time_filter_minutes_before": (5, 1, 10, "节点前禁区分钟数"),
        "gate_time_filter_minutes_after":  (2, 1, 5, "节点后禁区分钟数"),
        # 南哥打法：跳过 L3 行为层（布尔用 0/1 表示）
        "gate_skip_behavior_layer":     (0, 0, 1, "跳过行为确认层(0否/1是)"),
    }
    for name, (val, lo, hi, desc) in gate_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else (0.01 if hi - lo < 1 else 0.1)
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="gate", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 5: 异常检测阈值
    # ═══════════════════════════════════════
    detector_defs = {
        "detect_vpin_high":          (0.65, 0.40, 0.85, "VPIN 高阈值"),
        "detect_vpin_critical":      (0.80, 0.60, 0.95, "VPIN 极端阈值"),
        "detect_funding_elevated":   (0.03, 0.01, 0.06, "资金费率偏高阈值%"),
        "detect_funding_extreme":    (0.08, 0.04, 0.15, "资金费率极端阈值%"),
        "detect_wall_frequent":      (3, 1, 8, "假墙频繁阈值(30s内次数)"),
        "detect_oi_surge_pct":       (3.0, 1.0, 8.0, "OI 骤变阈值%"),
        "detect_oi_global_surge":    (5.0, 2.0, 12.0, "全网OI骤变阈值%"),
        "detect_fng_extreme_low":    (15, 5, 25, "极端恐惧阈值"),
        "detect_fng_extreme_high":   (85, 75, 95, "极端贪婪阈值"),
        "detect_divergence_extreme": (0.5, 0.2, 0.8, "散户鲸鱼分歧阈值"),
        "detect_ofi_extreme_z":      (3.0, 2.0, 5.0, "OFI 极端z-score阈值"),
    }
    for name, (val, lo, hi, desc) in detector_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else 0.01
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="detector", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 6: 特征计算参数
    # ═══════════════════════════════════════
    feature_defs = {
        "feat_vpin_bucket_size":       (100000, 20000, 500000, "VPIN 桶大小(USDT)"),
        "feat_vpin_num_buckets":       (50, 20, 100, "VPIN 桶数量"),
        "feat_large_trade_threshold":  (50000, 10000, 200000, "大单阈值(USDT)"),
        "feat_absorption_threshold":   (50000, 10000, 200000, "吸收检测阈值"),
        "feat_absorption_min_volume":  (10000, 2000, 50000, "吸收最小成交量(USDT)"),
        "feat_vp_bin_size_pct":        (0.01, 0.005, 0.05, "VP 价格桶大小%"),
        "feat_vp_value_area_pct":      (0.70, 0.50, 0.90, "VP 价值区域覆盖%"),
        "feat_wall_threshold_usdt":    (200000, 50000, 500000, "假墙检测阈值(USDT)"),
        "feat_wall_max_lifetime_ms":   (5000, 2000, 15000, "假墙最大存活时间(ms)"),
    }
    for name, (val, lo, hi, desc) in feature_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else (0.001 if hi - lo < 0.1 else 0.01)
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="feature", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 7: 纸盘交易参数
    # ═══════════════════════════════════════
    paper_defs = {
        "paper_leverage":              (10, 1, 50, "纸盘杠杆"),
        "paper_position_pct":          (10.0, 2.0, 30.0, "纸盘仓位占比%"),
        "paper_stop_loss_pct":         (2.0, 0.3, 5.0, "纸盘默认止损%"),
        "paper_take_profit_pct":       (1.5, 0.5, 5.0, "纸盘默认止盈%"),
        "paper_trailing_activate_pct": (0.8, 0.3, 2.0, "追踪止损激活%"),
        "paper_trailing_callback_pct": (40.0, 20.0, 70.0, "追踪止损回调%"),
        "paper_slippage_pct":          (0.02, 0.01, 0.10, "模拟滑点%"),
        "paper_fee_pct":               (0.02, 0.01, 0.05, "单边手续费%"),
        "paper_cooldown_s":            (60.0, 30.0, 600.0, "开仓冷却秒"),
        "paper_min_hold_s":            (180.0, 60.0, 1800.0, "最小持仓秒"),
        "paper_min_hold_wrong_s":      (45.0, 10.0, 300.0, "NEUTRAL浮亏最短持仓秒"),
        "paper_min_confidence":        (0.20, 0.10, 0.80, "纸盘最小置信度"),
        "paper_min_entry_score":       (0.05, 0.02, 0.50, "纸盘最小入场|score|"),
    }
    for name, (val, lo, hi, desc) in paper_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else (0.01 if hi - lo < 5 else 1.0)
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="paper", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 8: 置信度计算参数
    # ═══════════════════════════════════════
    confidence_defs = {
        "conf_bullish_threshold":      (0.1, 0.01, 0.30, "看多因子判定阈值"),
        "conf_bearish_threshold":      (-0.1, -0.30, -0.01, "看空因子判定阈值"),
        "conf_dominant_boost_count":   (8, 5, 12, "一致性加强触发因子数"),
        "conf_dominant_boost_mult":    (1.2, 1.0, 1.5, "一致性加强乘数"),
        "conf_anomaly_penalty":        (0.7, 0.3, 1.0, "异常风险置信度惩罚系数"),
    }
    for name, (val, lo, hi, desc) in confidence_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else 0.01
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="confidence", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 9: 评分函数内部参数（scorer.py 各因子灵敏度/系数/阈值）
    # ═══════════════════════════════════════
    scorer_defs = {
        # CVD 因子
        "score_cvd_min_volume":         (100, 10, 1000, "CVD最低成交量过滤"),
        "score_cvd_trend_scale":        (0.3, 0.1, 0.8, "CVD趋势缩放系数"),
        "score_cvd_base_weight":        (0.8, 0.5, 1.0, "CVD基础比率权重"),
        "score_cvd_trend_weight":       (0.2, 0.0, 0.5, "CVD趋势权重"),
        "score_cvd_div_threshold":      (0.1, 0.01, 0.3, "CVD背离触发阈值"),
        "score_cvd_div_base_weight":    (0.8, 0.5, 1.0, "CVD背离时基础权重"),
        "score_cvd_div_weight":         (0.2, 0.0, 0.5, "CVD背离信号权重"),
        # OFI 因子
        "score_ofi_core_sensitivity":   (0.5, 0.1, 2.0, "OFI核心30s灵敏度"),
        "score_ofi_long_sensitivity":   (0.4, 0.1, 2.0, "OFI长期5m灵敏度"),
        "score_ofi_trend_sensitivity":  (0.8, 0.2, 2.0, "OFI趋势灵敏度"),
        "score_ofi_core_weight":        (0.6, 0.3, 0.8, "OFI核心权重"),
        "score_ofi_long_weight":        (0.2, 0.0, 0.4, "OFI长期权重"),
        "score_ofi_trend_weight":       (0.2, 0.0, 0.4, "OFI趋势权重"),
        "score_ofi_agree_boost":        (0.3, 0.1, 0.6, "OFI一致性加成系数"),
        "score_ofi_disagree_mult":      (0.7, 0.3, 1.0, "OFI不一致削弱系数"),
        # 大单因子
        "score_lt_min_total":           (10000, 1000, 50000, "大单最低总量过滤"),
        "score_lt_count_boost_rate":    (0.05, 0.01, 0.15, "大单数量加成速率"),
        "score_lt_count_boost_max":     (0.2, 0.05, 0.5, "大单数量加成上限"),
        # 深度变化因子
        "score_depth_sensitivity":      (2.0, 0.5, 5.0, "深度不平衡灵敏度"),
        "score_depth_wall_threshold":   (2, 1, 10, "假墙降权触发次数"),
        "score_depth_wall_mult":        (0.5, 0.1, 0.9, "假墙降权系数"),
        # 资金费率因子
        "score_funding_sensitivity":    (3.0, 1.0, 8.0, "费率灵敏度(×100)"),
        "score_funding_extreme_mult":   (1.5, 1.0, 2.5, "极端费率加成系数"),
        # 清算因子
        "score_liq_min_net":            (1000, 100, 10000, "清算最低净值过滤"),
        "score_liq_scale":              (500000, 100000, 2000000, "清算归一化分母"),
        "score_liq_sensitivity":        (1.0, 0.3, 3.0, "清算灵敏度"),
        # 情绪因子
        "score_sent_fng_sensitivity":   (1.5, 0.5, 3.0, "恐慌贪婪灵敏度"),
        "score_sent_retail_sensitivity": (2.0, 0.5, 4.0, "散户多空比灵敏度"),
        "score_sent_whale_sensitivity": (2.0, 0.5, 4.0, "鲸鱼多空比灵敏度"),
        "score_sent_fng_weight":        (0.3, 0.1, 0.5, "恐慌贪婪权重"),
        "score_sent_retail_weight":     (0.3, 0.1, 0.5, "散户权重"),
        "score_sent_whale_weight":      (0.4, 0.1, 0.6, "鲸鱼权重"),
        # 趋势因子
        "score_trend_vol_threshold":    (0.2, 0.05, 0.5, "放量确认最低score"),
        "score_trend_vol_boost":        (1.2, 1.0, 1.5, "放量确认加成系数"),
        # VPIN 因子
        "score_vpin_low_threshold":     (0.3, 0.1, 0.5, "VPIN低阈值(无信号)"),
        "score_vpin_penalty_rate":      (0.5, 0.1, 1.0, "VPIN不确定性惩罚速率"),
        "score_vpin_direction_scale":   (0.5, 0.1, 1.0, "VPIN方向信号缩放"),
        # OI 因子
        "score_oi_min_change":          (0.5, 0.1, 2.0, "OI最低变化%过滤"),
        "score_oi_sensitivity":         (0.5, 0.1, 2.0, "OI灵敏度"),
        "score_oi_global_threshold":    (2.0, 0.5, 5.0, "全网OI确认阈值%"),
        "score_oi_global_sensitivity":  (0.3, 0.1, 1.0, "全网OI灵敏度"),
        "score_oi_local_weight":        (0.6, 0.3, 0.8, "OI本地权重"),
        "score_oi_global_weight":       (0.4, 0.2, 0.7, "OI全网权重"),
        # VWAP 因子
        "score_vwap_mr_sensitivity":    (1.0, 0.3, 3.0, "VWAP均值回归灵敏度"),
        "score_vwap_mr_divisor":        (0.5, 0.1, 1.5, "VWAP均值回归除数"),
        "score_vwap_trend_threshold":   (0.3, 0.1, 0.8, "VWAP趋势确认阈值%"),
        "score_vwap_trend_sensitivity": (0.8, 0.3, 2.0, "VWAP趋势灵敏度"),
        "score_vwap_trend_divisor":     (0.5, 0.1, 1.5, "VWAP趋势除数"),
        "score_vwap_mr_weight":         (0.6, 0.3, 0.8, "VWAP均值回归权重"),
        "score_vwap_trend_weight":      (0.4, 0.2, 0.7, "VWAP趋势权重"),
        # Volume Profile 因子
        "score_vp_min_volume":          (1000, 100, 10000, "VP最低成交量过滤"),
        "score_vp_va_sensitivity":      (0.5, 0.1, 2.0, "VP价值区内灵敏度"),
        "score_vp_va_divisor":          (0.3, 0.1, 1.0, "VP价值区内除数"),
        "score_vp_breakout_sensitivity": (1.0, 0.3, 3.0, "VP突破灵敏度"),
        "score_vp_breakout_divisor":    (0.5, 0.1, 1.5, "VP突破除数"),
        # 吸收因子
        "score_abs_event_threshold":    (3, 1, 10, "吸收事件加成触发数"),
        "score_abs_event_boost_rate":   (0.05, 0.01, 0.15, "吸收事件加成速率"),
        "score_abs_event_boost_max":    (0.3, 0.1, 0.6, "吸收事件加成上限"),
    }
    for name, (val, lo, hi, desc) in scorer_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else (0.01 if hi - lo < 5 else 0.1)
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="scorer", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 10: 门卫补充参数（entry_gate.py 内残留硬编码）
    # ═══════════════════════════════════════
    gate_extra_defs = {
        "gate_trending_min_band_width": (0.5, 0.1, 2.0, "趋势环境最低band_width%"),
        "gate_ranging_min_absorption":  (2, 1, 10, "震荡环境最低吸收事件数"),
        "gate_signal_strong_buy":       (0.40, 0.20, 0.60, "门卫STRONG_BUY阈值"),
        "gate_signal_strong_sell":      (-0.40, -0.60, -0.20, "门卫STRONG_SELL阈值"),
        "gate_default_take_profit_pct": (1.5, 0.5, 5.0, "门卫默认止盈%"),
        "gate_min_take_profit_pct":     (0.5, 0.1, 1.5, "门卫最小止盈%"),
        "gate_ranging_max_tp_pct":      (1.0, 0.3, 2.0, "震荡环境止盈上限%"),
    }
    for name, (val, lo, hi, desc) in gate_extra_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else 0.01
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="gate", description=desc,
        )

    # ═══════════════════════════════════════
    # Group 11: 特征计算器参数（features/ 各计算器构造函数参数）
    # ═══════════════════════════════════════
    feature_defs = {
        # VPIN
        "feat_vpin_bucket_size":    (100000, 10000, 500000, "VPIN桶大小(USDT)"),
        "feat_vpin_num_buckets":    (50, 20, 200, "VPIN桶数量"),
        # 大单检测
        "feat_large_trade_threshold": (50000, 5000, 200000, "大单阈值(USDT)"),
        "feat_large_trade_window_ms": (30000, 10000, 120000, "大单统计窗口(ms)"),
        # OFI
        "feat_ofi_levels":          (5, 3, 20, "OFI订单簿深度档位"),
        "feat_ofi_capacity":        (90000, 30000, 300000, "OFI缓冲区容量"),
        # CVD
        "feat_cvd_capacity":        (60000, 20000, 200000, "CVD缓冲区容量"),
        # 清算
        "feat_liq_window_5m_ms":    (300000, 60000, 600000, "清算统计窗口(ms)"),
        # 资金费率
        "feat_funding_history_size": (300, 60, 1000, "费率历史窗口(秒)"),
    }
    for name, (val, lo, hi, desc) in feature_defs.items():
        ptype = "int" if isinstance(val, int) else "float"
        step = 1 if ptype == "int" else 0.1
        params[name] = ParamDef(
            name=name, value=val, min_val=lo, max_val=hi,
            step=step, param_type=ptype, group="features", description=desc,
        )

    return params


# ── 参数注册中心 ──

class ParamRegistry:
    """
    参数注册中心 — 所有可优化参数的唯一数据源。

    使用方式：
        registry = ParamRegistry(data_dir="data/optimizer")
        weight_cvd = registry.get("weight_cvd")
        registry.set("weight_cvd", 0.15)
        registry.save()

    与 Optuna 集成：
        space = registry.get_search_space(groups=["weights", "signal_thresholds"])
        # → 返回 {name: ParamDef} 供 Optuna trial.suggest_*
    """

    def __init__(self, data_dir: str = "data/optimizer"):
        self._data_dir = Path(data_dir)
        self._params_file = self._data_dir / "params.json"
        self._snapshots_dir = self._data_dir / "snapshots"
        self._history_file = self._data_dir / "versions.jsonl"
        self._params: dict[str, ParamDef] = _build_default_params()
        self._subscribers: list[Callable[[dict[str, float]], None]] = []
        self._dirty = False
        self._version = 1
        self._updated_at = ""
        self._last_label = ""

        # 确保目录存在
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)

        # 尝试从文件加载（覆盖默认值）
        self._load()
        if not self._updated_at:
            self._updated_at = self._now_iso()

    # ── 基础读写 ──

    def get(self, name: str) -> float:
        """获取参数值"""
        if name not in self._params:
            raise KeyError(f"未知参数: {name}")
        p = self._params[name]
        if p.param_type == "int":
            return int(p.value)
        return p.value

    def get_def(self, name: str) -> ParamDef:
        """获取参数完整定义"""
        if name not in self._params:
            raise KeyError(f"未知参数: {name}")
        return self._params[name]

    def set(self, name: str, value: float) -> None:
        """设置参数值（带范围检查）"""
        if name not in self._params:
            raise KeyError(f"未知参数: {name}")
        p = self._params[name]
        # 范围检查
        if value < p.min_val or value > p.max_val:
            raise ValueError(
                f"参数 {name} 值 {value} 超出范围 [{p.min_val}, {p.max_val}]"
            )
        if p.param_type == "int":
            value = int(value)
        if p.value == value:
            return
        p.value = value
        self._dirty = True

    def set_batch(self, updates: dict[str, float]) -> None:
        """批量设置参数"""
        for name, value in updates.items():
            self.set(name, value)

    def get_all(self) -> dict[str, float]:
        """获取所有参数的当前值"""
        result = {}
        for name, p in self._params.items():
            result[name] = int(p.value) if p.param_type == "int" else p.value
        return result

    def get_group(self, group: str) -> dict[str, float]:
        """获取指定组的参数值"""
        return {
            name: (int(p.value) if p.param_type == "int" else p.value)
            for name, p in self._params.items()
            if p.group == group
        }

    def get_all_defs(self) -> dict[str, ParamDef]:
        """获取所有参数定义（含范围等元信息）"""
        return deepcopy(self._params)

    def get_groups(self) -> dict[str, list[str]]:
        """获取所有参数组及其参数名列表"""
        groups: dict[str, list[str]] = {}
        for name, p in self._params.items():
            groups.setdefault(p.group, []).append(name)
        return groups

    # ── 因子权重专用接口 ──

    def get_weights_dict(self) -> dict[str, float]:
        """
        获取因子权重字典（与 scorer.py DEFAULT_WEIGHTS 格式兼容）。
        key 去掉 "weight_" 前缀。
        """
        return {
            name.replace("weight_", ""): p.value
            for name, p in self._params.items()
            if p.group == "weights"
        }

    def get_signal_thresholds(self) -> dict:
        """获取信号阈值（与 scorer.py 格式兼容）"""
        return {
            "strong_buy":  self.get("thresh_strong_buy"),
            "buy":         self.get("thresh_buy"),
            "sell":        self.get("thresh_sell"),
            "strong_sell": self.get("thresh_strong_sell"),
        }

    def get_exit_thresholds(self) -> dict:
        """获取退出阈值"""
        return {
            "buy_exit":         self.get("thresh_buy_exit"),
            "sell_exit":        self.get("thresh_sell_exit"),
            "strong_buy_exit":  self.get("thresh_strong_buy_exit"),
            "strong_sell_exit": self.get("thresh_strong_sell_exit"),
        }

    def get_reversal_thresholds(self) -> dict:
        """获取反转阈值"""
        return {
            "buy_to_sell": self.get("thresh_buy_to_sell"),
            "sell_to_buy": self.get("thresh_sell_to_buy"),
        }

    def get_regime_multipliers(self) -> dict:
        """获取环境乘数（与 scorer.py REGIME_MULTIPLIERS 格式兼容）"""
        return {
            "trending":  {"micro": self.get("regime_trending_micro"),  "macro": self.get("regime_trending_macro")},
            "breakout":  {"micro": self.get("regime_breakout_micro"),  "macro": self.get("regime_breakout_macro")},
            "ranging":   {"micro": self.get("regime_ranging_micro"),   "macro": self.get("regime_ranging_macro")},
            "extreme":   {"micro": self.get("regime_extreme_micro"),   "macro": self.get("regime_extreme_macro")},
            "unclear":   {"micro": self.get("regime_unclear_micro"),   "macro": self.get("regime_unclear_macro")},
        }

    def get_detector_thresholds(self) -> dict:
        """获取异常检测阈值（与 detector.py THRESHOLDS 格式兼容）"""
        return {
            "vpin_high":          self.get("detect_vpin_high"),
            "vpin_critical":      self.get("detect_vpin_critical"),
            "funding_elevated":   self.get("detect_funding_elevated"),
            "funding_extreme":    self.get("detect_funding_extreme"),
            "wall_frequent":      int(self.get("detect_wall_frequent")),
            "oi_surge_pct":       self.get("detect_oi_surge_pct"),
            "oi_global_surge":    self.get("detect_oi_global_surge"),
            "fng_extreme_low":    int(self.get("detect_fng_extreme_low")),
            "fng_extreme_high":   int(self.get("detect_fng_extreme_high")),
            "divergence_extreme": self.get("detect_divergence_extreme"),
            "ofi_extreme_z":      self.get("detect_ofi_extreme_z"),
        }

    # ── Optuna 搜索空间 ──

    def get_search_space(
        self,
        groups: Optional[list[str]] = None,
        exclude: Optional[list[str]] = None,
    ) -> dict[str, ParamDef]:
        """
        获取 Optuna 搜索空间。

        参数:
            groups: 只包含指定组（None = 全部可优化参数）
            exclude: 排除指定参数名

        返回:
            {param_name: ParamDef} — 供 Optuna trial.suggest_* 使用
        """
        exclude = set(exclude or [])
        space = {}
        for name, p in self._params.items():
            if not p.is_optimizable:
                continue
            if groups and p.group not in groups:
                continue
            if name in exclude:
                continue
            space[name] = deepcopy(p)
        return space

    # ── 快照与回滚 ──

    def snapshot(self, label: str = "") -> str:
        """
        保存当前参数快照。

        返回: 快照文件名
        """
        ts = int(time.time())
        label_part = f"_{label}" if label else ""
        filename = f"snapshot_v{self._version}_{ts}{label_part}.json"
        filepath = self._snapshots_dir / filename

        data = {
            "timestamp": ts,
            "version": self._version,
            "updated_at": self._updated_at,
            "label": label,
            "params": {name: p.to_dict() for name, p in self._params.items()},
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filename

    def rollback(self, snapshot_name: str) -> None:
        """从快照恢复参数"""
        filepath = self._snapshots_dir / snapshot_name
        if not filepath.exists():
            raise FileNotFoundError(f"快照不存在: {snapshot_name}")

        before = self.get_all()
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        for name, pdict in data["params"].items():
            if name in self._params:
                self._params[name] = ParamDef.from_dict(pdict)

        self._dirty = True
        change_set = self._build_change_set(before, self.get_all())
        self.save(
            label=f"rollback:{snapshot_name}",
            source="rollback",
            change_set=change_set,
        )
        self._notify_subscribers()

    def list_snapshots(self) -> list[dict]:
        """列出所有快照"""
        snapshots = []
        for f in sorted(self._snapshots_dir.glob("snapshot_*.json"), reverse=True):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                snapshots.append({
                    "filename": f.name,
                    "timestamp": data.get("timestamp", 0),
                    "version": data.get("version"),
                    "label": data.get("label", ""),
                })
            except Exception:
                continue
        return snapshots

    # ── 订阅机制 ──

    def subscribe(self, callback: Callable[[dict[str, float]], None]) -> None:
        """注册参数变更回调"""
        self._subscribers.append(callback)

    def _notify_subscribers(self) -> None:
        """通知所有订阅者参数已变更"""
        values = self.get_all()
        for cb in self._subscribers:
            try:
                cb(values)
            except Exception:
                pass  # 订阅者异常不影响主流程

    # ── 持久化 ──

    def save(
        self,
        label: str = "",
        source: str = "manual",
        change_set: Optional[dict[str, dict[str, float]]] = None,
    ) -> None:
        """保存参数到文件，并在有变更时记录版本元信息"""
        if self._dirty:
            self._version += 1
            self._updated_at = self._now_iso()
            if label:
                self._last_label = label

        data = {
            "_meta": {
                "schema_version": "v2",
                "version": self._version,
                "updated_at": self._updated_at,
                "last_label": self._last_label,
            },
            "params": {name: p.to_dict() for name, p in self._params.items()},
        }
        with open(self._params_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if change_set:
            self._append_history(
                label=label,
                source=source,
                change_set=change_set,
            )
        self._dirty = False

    def _load(self) -> None:
        """从文件加载参数（覆盖默认值）"""
        if not self._params_file.exists():
            return  # 文件不存在，使用默认值

        try:
            with open(self._params_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict) and "params" in data:
                meta = data.get("_meta", {})
                self._version = int(meta.get("version", self._version))
                self._updated_at = meta.get("updated_at", self._updated_at)
                self._last_label = meta.get("last_label", self._last_label)
                params_data = data.get("params", {})
            else:
                # 兼容旧版本数据结构（直接是 {param_name: {...}}）
                params_data = data

            for name, pdict in params_data.items():
                if name in self._params:
                    # 只更新 value，保留默认的 min/max/description 等元信息
                    self._params[name].value = pdict.get("value", self._params[name].value)
        except Exception:
            pass  # 加载失败，使用默认值

    def apply_and_save(self, updates: dict[str, float], label: str = "") -> str:
        """
        原子操作：先快照 → 再更新 → 再保存 → 通知订阅者。

        返回: 快照文件名（用于回滚）
        """
        before = self.get_all()
        snapshot_name = self.snapshot(label=label or "before_update")
        self.set_batch(updates)
        change_set = self._build_change_set(before, self.get_all())
        self.save(
            label=label or "apply_update",
            source="apply_and_save",
            change_set=change_set,
        )
        self._notify_subscribers()
        return snapshot_name

    def get_version_info(self) -> dict[str, Any]:
        """返回当前参数版本信息"""
        return {
            "version": self._version,
            "updated_at": self._updated_at,
            "last_label": self._last_label,
            "params_file": str(self._params_file),
            "history_file": str(self._history_file),
        }

    def get_change_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """返回最近参数变更历史（新到旧）"""
        if limit <= 0:
            return []
        history = self._read_history()
        if not history:
            return []
        return list(reversed(history[-limit:]))

    def get_changed_keys_between_versions(
        self,
        from_version: int,
        to_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        获取两个版本之间发生变化的参数 key（轻量对比）。

        说明：
          - 仅返回 changed keys，不返回完整 old/new diff
          - from_version <= to_version
          - to_version 为空时，默认当前版本
        """
        current_version = self._version
        target_version = to_version if to_version is not None else current_version

        if from_version < 1 or target_version < 1:
            raise ValueError("版本号必须 >= 1")
        if from_version > target_version:
            raise ValueError("from_version 不能大于 to_version")
        if target_version > current_version:
            raise ValueError(
                f"to_version={target_version} 超出当前版本 {current_version}"
            )
        if from_version == target_version:
            return {
                "from_version": from_version,
                "to_version": target_version,
                "changed_keys": [],
                "changes_count": 0,
            }

        changed_keys: set[str] = set()
        for item in self._read_history():
            version = int(item.get("version", 0))
            if from_version < version <= target_version:
                changes = item.get("changes", {})
                if isinstance(changes, dict):
                    changed_keys.update(changes.keys())

        keys = sorted(changed_keys)
        return {
            "from_version": from_version,
            "to_version": target_version,
            "changed_keys": keys,
            "changes_count": len(keys),
        }

    # ── 统计信息 ──

    def stats(self) -> dict:
        """返回参数统计摘要"""
        groups = self.get_groups()
        return {
            "total_params": len(self._params),
            "groups": {g: len(names) for g, names in groups.items()},
            "optimizable": sum(1 for p in self._params.values() if p.is_optimizable),
            "dirty": self._dirty,
            "version": self._version,
            "updated_at": self._updated_at,
            "last_label": self._last_label,
            "params_file": str(self._params_file),
            "history_file": str(self._history_file),
            "history_count": len(self._read_history()),
            "snapshots_count": len(list(self._snapshots_dir.glob("snapshot_*.json"))),
        }

    def _build_change_set(
        self,
        before: dict[str, float],
        after: dict[str, float],
    ) -> dict[str, dict[str, float]]:
        """构建参数差异映射（old/new）"""
        changes: dict[str, dict[str, float]] = {}
        all_keys = set(before.keys()) | set(after.keys())
        for key in all_keys:
            old_val = before.get(key)
            new_val = after.get(key)
            if old_val != new_val:
                changes[key] = {
                    "old": old_val,
                    "new": new_val,
                }
        return changes

    def _append_history(
        self,
        label: str,
        source: str,
        change_set: dict[str, dict[str, float]],
    ) -> None:
        """追加写入参数变更历史"""
        record = {
            "timestamp": int(time.time()),
            "timestamp_iso": self._now_iso(),
            "version": self._version,
            "label": label,
            "source": source,
            "changes_count": len(change_set),
            "changes": change_set,
        }
        with open(self._history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_history(self) -> list[dict[str, Any]]:
        """读取变更历史（旧到新）"""
        if not self._history_file.exists():
            return []

        history: list[dict[str, Any]] = []
        try:
            with open(self._history_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        history.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return []
        return history

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
