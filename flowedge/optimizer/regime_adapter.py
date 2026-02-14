"""
市场环境自适应参数切换器 — 根据当前市场环境自动选择最优参数组。

核心思路：
  不同市场环境（趋势/震荡/突破/极端）适合不同的参数配置。
  本模块维护多组参数（每种环境一组），根据实时检测到的市场环境自动切换。

工作流程：
  1. 为每种市场环境独立优化参数（或手动配置）
  2. 实时接收市场环境信号（来自 SignalEngine 的 regime 检测）
  3. 当环境切换时，自动将对应参数组应用到 ParamRegistry
  4. 支持平滑过渡（避免频繁切换）

市场环境分类：
  - trending:  趋势行情（方向明确，动量强）
  - ranging:   震荡行情（无明确方向，波动小）
  - breakout:  突破行情（波动率突增，成交量放大）
  - extreme:   极端行情（清算级联、恐慌/贪婪极值）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .param_registry import ParamRegistry

logger = logging.getLogger("flowedge.optimizer.regime_adapter")


# 支持的市场环境类型
REGIME_TYPES = ("trending", "ranging", "breakout", "extreme")


@dataclass
class RegimeParamSet:
    """单个环境的参数组"""
    regime: str                         # 环境类型
    params: dict[str, float]            # 参数集
    label: str = ""                     # 描述标签
    optimized_at: Optional[str] = None  # 优化时间
    sharpe: float = 0.0                 # 该环境下的回测 Sharpe


@dataclass
class RegimeSwitchEvent:
    """环境切换事件"""
    timestamp: float                    # Unix 时间戳
    from_regime: str
    to_regime: str
    confidence: float                   # 切换置信度
    applied: bool                       # 是否已应用参数


@dataclass
class AdapterConfig:
    """自适应配置"""
    # 切换阈值
    min_confidence: float = 0.6         # 最低切换置信度
    cooldown_s: int = 300               # 切换冷却时间（秒）
    # 平滑过渡
    use_blending: bool = True           # 是否使用参数混合过渡
    blend_ratio: float = 0.7            # 新环境参数占比（0.7 = 70% 新 + 30% 旧）
    # 默认环境
    default_regime: str = "ranging"     # 无法判断时使用的默认环境


class RegimeAdapter:
    """
    市场环境自适应参数切换器。

    使用方式：
        adapter = RegimeAdapter(registry)
        # 注册各环境的参数组
        adapter.register_params("trending", trending_params)
        adapter.register_params("ranging", ranging_params)
        # 当检测到环境变化时调用
        adapter.on_regime_change("trending", confidence=0.8)
    """

    def __init__(
        self,
        registry: ParamRegistry,
        config: Optional[AdapterConfig] = None,
    ):
        self._registry = registry
        self._config = config or AdapterConfig()
        self._param_sets: dict[str, RegimeParamSet] = {}
        self._current_regime: str = self._config.default_regime
        self._last_switch_time: float = 0
        self._switch_history: list[RegimeSwitchEvent] = []

    @property
    def current_regime(self) -> str:
        return self._current_regime

    @property
    def registered_regimes(self) -> list[str]:
        return list(self._param_sets.keys())

    def register_params(
        self,
        regime: str,
        params: dict[str, float],
        label: str = "",
        sharpe: float = 0.0,
    ):
        """注册一个环境的参数组"""
        if regime not in REGIME_TYPES:
            logger.warning(f"未知环境类型: {regime}，允许的类型: {REGIME_TYPES}")

        self._param_sets[regime] = RegimeParamSet(
            regime=regime,
            params=params,
            label=label or f"{regime}_params",
            sharpe=sharpe,
        )
        logger.info(f"[RegimeAdapter] 已注册 {regime} 参数组（{len(params)} 个参数）")

    def on_regime_change(
        self,
        new_regime: str,
        confidence: float = 1.0,
    ) -> bool:
        """
        接收市场环境变化信号。

        参数:
            new_regime: 新的市场环境
            confidence: 判断置信度 (0-1)

        返回:
            是否实际执行了参数切换
        """
        # 同一环境不切换
        if new_regime == self._current_regime:
            return False

        # 置信度不足
        if confidence < self._config.min_confidence:
            logger.debug(
                f"[RegimeAdapter] 环境切换信号 {self._current_regime}→{new_regime} "
                f"置信度不足: {confidence:.2f} < {self._config.min_confidence}"
            )
            return False

        # 冷却期检查
        now = time.time()
        if now - self._last_switch_time < self._config.cooldown_s:
            remaining = self._config.cooldown_s - (now - self._last_switch_time)
            logger.debug(
                f"[RegimeAdapter] 冷却期内，剩余 {remaining:.0f}s"
            )
            return False

        # 检查是否有对应参数组
        if new_regime not in self._param_sets:
            logger.warning(f"[RegimeAdapter] 无 {new_regime} 参数组，跳过切换")
            event = RegimeSwitchEvent(
                timestamp=now,
                from_regime=self._current_regime,
                to_regime=new_regime,
                confidence=confidence,
                applied=False,
            )
            self._switch_history.append(event)
            return False

        # 执行切换
        old_regime = self._current_regime
        new_params = self._param_sets[new_regime].params

        if self._config.use_blending and old_regime in self._param_sets:
            # 平滑过渡：混合新旧参数
            old_params = self._param_sets[old_regime].params
            blended = self._blend_params(old_params, new_params, self._config.blend_ratio)
            applied_params = blended
        else:
            applied_params = new_params

        # 应用到 registry
        self._registry.apply_and_save(
            applied_params,
            label=f"regime_{new_regime}_{int(now)}",
        )

        self._current_regime = new_regime
        self._last_switch_time = now

        event = RegimeSwitchEvent(
            timestamp=now,
            from_regime=old_regime,
            to_regime=new_regime,
            confidence=confidence,
            applied=True,
        )
        self._switch_history.append(event)

        logger.info(
            f"[RegimeAdapter] 环境切换: {old_regime} → {new_regime} "
            f"(置信度 {confidence:.2f}，已应用 {len(applied_params)} 个参数)"
        )
        return True

    def _blend_params(
        self,
        old_params: dict[str, float],
        new_params: dict[str, float],
        ratio: float,
    ) -> dict[str, float]:
        """混合新旧参数（平滑过渡）"""
        blended = {}
        all_keys = set(old_params.keys()) | set(new_params.keys())

        for key in all_keys:
            has_old = key in old_params
            has_new = key in new_params

            if has_old and has_new:
                old_val = old_params[key]
                new_val = new_params[key]
                blended[key] = round(new_val * ratio + old_val * (1 - ratio), 6)
            elif has_new:
                blended[key] = round(new_params[key], 6)
            else:
                blended[key] = round(old_params[key], 6)

        return blended

    def get_status(self) -> dict:
        """获取自适应状态"""
        now = time.time()
        cooldown_remaining = max(0, self._config.cooldown_s - (now - self._last_switch_time))

        return {
            "current_regime": self._current_regime,
            "registered_regimes": list(self._param_sets.keys()),
            "cooldown_remaining_s": round(cooldown_remaining),
            "total_switches": len(self._switch_history),
            "config": {
                "min_confidence": self._config.min_confidence,
                "cooldown_s": self._config.cooldown_s,
                "use_blending": self._config.use_blending,
                "blend_ratio": self._config.blend_ratio,
            },
            "param_sets": {
                regime: {
                    "label": ps.label,
                    "n_params": len(ps.params),
                    "sharpe": ps.sharpe,
                }
                for regime, ps in self._param_sets.items()
            },
        }

    def get_switch_history(self, limit: int = 20) -> list[dict]:
        """获取切换历史"""
        events = self._switch_history[-limit:]
        return [
            {
                "timestamp": e.timestamp,
                "from_regime": e.from_regime,
                "to_regime": e.to_regime,
                "confidence": e.confidence,
                "applied": e.applied,
            }
            for e in reversed(events)
        ]
