"""
AI 评估器 — 用 LLM 分析优化结果，生成人类可读的评估报告。

核心功能：
  1. 最优参数解读：哪些因子权重高/低，意味着什么
  2. 过拟合风险评估：基于 Train/OOS 差距、参数稳定性、Bootstrap 结果
  3. 市场环境适用性：当前参数适合什么行情
  4. 下一轮搜索建议：应该扩大/缩小哪些参数范围

设计原则：
  - 即使无 LLM API，也能基于规则生成基础评估报告
  - LLM 增强模式：将数据发给 DeepSeek/GPT 做深度分析
  - 所有评估结论必须有数据支撑
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .metrics import PerformanceMetrics
from .validator import ValidationResult

logger = logging.getLogger("flowedge.optimizer.ai_evaluator")


@dataclass
class FactorInsight:
    """单个因子的洞察"""
    name: str
    weight: float
    rank: int                   # 权重排名
    interpretation: str         # 解读
    strength: str               # "high" / "medium" / "low"


@dataclass
class OverfitRisk:
    """过拟合风险评估"""
    level: str                  # "low" / "medium" / "high" / "critical"
    score: float                # 0-1（越高越危险）
    factors: list[str]          # 风险因素列表
    mitigations: list[str]      # 缓解建议


@dataclass
class MarketFit:
    """市场环境适用性"""
    best_regime: str            # 最适合的市场环境
    regime_scores: dict[str, float]  # 各环境适用性评分
    explanation: str


@dataclass
class SearchSuggestion:
    """下一轮搜索建议"""
    param_name: str
    current_value: float
    suggested_range: tuple[float, float]
    reason: str


@dataclass
class EvaluationReport:
    """完整评估报告"""
    # 总体评分
    overall_grade: str          # "A" / "B" / "C" / "D" / "F"
    overall_score: float        # 0-100
    summary: str                # 一句话总结

    # 各维度分析
    factor_insights: list[FactorInsight] = field(default_factory=list)
    overfit_risk: Optional[OverfitRisk] = None
    market_fit: Optional[MarketFit] = None
    search_suggestions: list[SearchSuggestion] = field(default_factory=list)

    # 行动建议
    recommendations: list[str] = field(default_factory=list)

    # 原始数据引用
    metrics_summary: dict = field(default_factory=dict)


class AIEvaluator:
    """
    AI 评估器 — 分析优化结果并生成评估报告。

    使用方式（规则模式，无需 LLM）：
        evaluator = AIEvaluator()
        report = evaluator.evaluate(
            params=best_params,
            train_metrics=train_metrics,
            oos_metrics=oos_metrics,
            validation=validation_result,
        )

    使用方式（LLM 增强模式）：
        evaluator = AIEvaluator(llm_client=deepseek_client)
        report = await evaluator.evaluate_with_llm(...)
    """

    # 因子名称 → 中文解读映射
    FACTOR_NAMES = {
        "cvd": "成交量 Delta（买卖力量差）",
        "ofi": "订单流不平衡",
        "book_imbalance": "L1 盘口压力",
        "vpin": "知情交易概率",
        "large_trade": "大单异动",
        "depth_change": "深度变化/假墙",
        "funding_rate": "资金费率",
        "liquidation": "清算级联",
        "oi_change": "持仓量变化",
        "sentiment": "多空情绪",
        "trend": "趋势上下文",
        "kline_pattern": "K线形态",
        "volatility": "波动率",
        "momentum": "动量",
    }

    def __init__(self, llm_client=None):
        self._llm_client = llm_client

    def evaluate(
        self,
        params: dict[str, float],
        train_metrics: Optional[PerformanceMetrics] = None,
        oos_metrics: Optional[PerformanceMetrics] = None,
        validation: Optional[ValidationResult] = None,
        param_stability: Optional[dict[str, float]] = None,
    ) -> EvaluationReport:
        """基于规则的评估（无需 LLM）"""

        # 1. 因子权重分析
        factor_insights = self._analyze_factors(params)

        # 2. 过拟合风险评估
        overfit_risk = self._assess_overfit_risk(
            train_metrics, oos_metrics, validation, param_stability
        )

        # 3. 市场环境适用性
        market_fit = self._assess_market_fit(params)

        # 4. 下一轮搜索建议
        search_suggestions = self._suggest_search(params, param_stability)

        # 5. 综合评分
        overall_score, overall_grade = self._calculate_overall_score(
            oos_metrics, overfit_risk, validation
        )

        # 6. 行动建议
        recommendations = self._generate_recommendations(
            overall_grade, overfit_risk, factor_insights, oos_metrics
        )

        # 7. 总结
        summary = self._generate_summary(overall_grade, oos_metrics, overfit_risk)

        # 指标摘要
        metrics_summary = {}
        if train_metrics:
            metrics_summary["train"] = {
                "sharpe": train_metrics.sharpe_ratio,
                "pnl": train_metrics.total_pnl_pct,
                "win_rate": train_metrics.win_rate,
                "trades": train_metrics.total_trades,
            }
        if oos_metrics:
            metrics_summary["oos"] = {
                "sharpe": oos_metrics.sharpe_ratio,
                "pnl": oos_metrics.total_pnl_pct,
                "win_rate": oos_metrics.win_rate,
                "trades": oos_metrics.total_trades,
                "max_dd": oos_metrics.max_drawdown_pct,
            }

        return EvaluationReport(
            overall_grade=overall_grade,
            overall_score=overall_score,
            summary=summary,
            factor_insights=factor_insights,
            overfit_risk=overfit_risk,
            market_fit=market_fit,
            search_suggestions=search_suggestions,
            recommendations=recommendations,
            metrics_summary=metrics_summary,
        )

    # ── 因子分析 ──

    def _analyze_factors(self, params: dict[str, float]) -> list[FactorInsight]:
        """分析因子权重分布"""
        weights = {
            k.replace("weight_", ""): v
            for k, v in params.items()
            if k.startswith("weight_")
        }

        if not weights:
            return []

        # 按权重排序
        sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        total = sum(weights.values())

        insights = []
        for rank, (name, weight) in enumerate(sorted_weights, 1):
            pct = weight / total * 100 if total > 0 else 0

            if pct >= 15:
                strength = "high"
                interp = f"核心因子（{pct:.1f}%），对信号影响最大"
            elif pct >= 8:
                strength = "medium"
                interp = f"重要因子（{pct:.1f}%），有显著贡献"
            else:
                strength = "low"
                interp = f"辅助因子（{pct:.1f}%），贡献较小"

            factor_cn = self.FACTOR_NAMES.get(name, name)
            interp = f"{factor_cn}: {interp}"

            insights.append(FactorInsight(
                name=name,
                weight=round(weight, 4),
                rank=rank,
                interpretation=interp,
                strength=strength,
            ))

        return insights

    # ── 过拟合风险 ──

    def _assess_overfit_risk(
        self,
        train_metrics: Optional[PerformanceMetrics],
        oos_metrics: Optional[PerformanceMetrics],
        validation: Optional[ValidationResult],
        param_stability: Optional[dict[str, float]],
    ) -> OverfitRisk:
        """评估过拟合风险"""
        risk_score = 0.0
        factors = []
        mitigations = []

        # 检查 1: Train vs OOS Sharpe 衰退
        if train_metrics and oos_metrics and train_metrics.sharpe_ratio > 0:
            decay = 1 - (oos_metrics.sharpe_ratio / train_metrics.sharpe_ratio)
            if decay > 0.7:
                risk_score += 0.4
                factors.append(f"Sharpe 衰退 {decay:.0%}（严重过拟合信号）")
                mitigations.append("使用 Walk-Forward 优化替代单次优化")
            elif decay > 0.4:
                risk_score += 0.2
                factors.append(f"Sharpe 衰退 {decay:.0%}（中度过拟合风险）")
                mitigations.append("增加训练数据量或减少参数维度")

        # 检查 2: 参数稳定性
        if param_stability:
            unstable = [
                name for name, std in param_stability.items()
                if std > 0.05  # 标准差 > 0.05 视为不稳定
            ]
            if len(unstable) > len(param_stability) * 0.5:
                risk_score += 0.3
                factors.append(f"{len(unstable)}/{len(param_stability)} 个参数不稳定")
                mitigations.append("缩小不稳定参数的搜索范围")
            elif unstable:
                risk_score += 0.1
                factors.append(f"{len(unstable)} 个参数轻度不稳定: {', '.join(unstable[:3])}")

        # 检查 3: 验证结果
        if validation:
            if not validation.passed:
                risk_score += 0.2
                failed_layers = [lr.layer for lr in validation.layers if not lr.passed]
                factors.append(f"验证未通过: {', '.join(failed_layers)}")
                mitigations.append("检查验证失败的具体原因，调整优化策略")

        # 检查 4: OOS 交易笔数
        if oos_metrics and oos_metrics.total_trades < 20:
            risk_score += 0.1
            factors.append(f"OOS 交易笔数仅 {oos_metrics.total_trades}（统计不充分）")
            mitigations.append("积累更多信号数据后再评估")

        # 确定风险等级
        risk_score = min(risk_score, 1.0)
        if risk_score >= 0.7:
            level = "critical"
        elif risk_score >= 0.4:
            level = "high"
        elif risk_score >= 0.2:
            level = "medium"
        else:
            level = "low"

        if not factors:
            factors.append("未检测到明显过拟合信号")
        if not mitigations:
            mitigations.append("当前风险可控，可继续使用")

        return OverfitRisk(
            level=level,
            score=round(risk_score, 4),
            factors=factors,
            mitigations=mitigations,
        )

    # ── 市场环境适用性 ──

    def _assess_market_fit(self, params: dict[str, float]) -> MarketFit:
        """基于参数特征推断适用的市场环境"""
        weights = {
            k.replace("weight_", ""): v
            for k, v in params.items()
            if k.startswith("weight_")
        }

        total = sum(weights.values()) or 1

        # 各环境适用性评分（基于因子权重特征）
        scores = {
            "trending": 0.0,    # 趋势行情
            "ranging": 0.0,     # 震荡行情
            "breakout": 0.0,    # 突破行情
            "extreme": 0.0,     # 极端行情
        }

        # 趋势因子
        trend_factors = {"trend", "momentum", "oi_change"}
        trend_weight = sum(weights.get(f, 0) for f in trend_factors) / total
        scores["trending"] = min(trend_weight * 5, 1.0)

        # 震荡因子
        range_factors = {"book_imbalance", "ofi", "depth_change"}
        range_weight = sum(weights.get(f, 0) for f in range_factors) / total
        scores["ranging"] = min(range_weight * 5, 1.0)

        # 突破因子
        break_factors = {"large_trade", "vpin", "volatility"}
        break_weight = sum(weights.get(f, 0) for f in break_factors) / total
        scores["breakout"] = min(break_weight * 5, 1.0)

        # 极端因子
        extreme_factors = {"liquidation", "funding_rate", "sentiment"}
        extreme_weight = sum(weights.get(f, 0) for f in extreme_factors) / total
        scores["extreme"] = min(extreme_weight * 5, 1.0)

        # 四舍五入
        scores = {k: round(v, 4) for k, v in scores.items()}

        best = max(scores, key=scores.get)
        regime_cn = {
            "trending": "趋势行情",
            "ranging": "震荡行情",
            "breakout": "突破行情",
            "extreme": "极端行情",
        }

        explanation = (
            f"当前参数最适合{regime_cn[best]}（得分 {scores[best]:.2f}）。"
            f"趋势因子占比 {trend_weight:.1%}，震荡因子占比 {range_weight:.1%}，"
            f"突破因子占比 {break_weight:.1%}，极端因子占比 {extreme_weight:.1%}。"
        )

        return MarketFit(
            best_regime=best,
            regime_scores=scores,
            explanation=explanation,
        )

    # ── 搜索建议 ──

    def _suggest_search(
        self,
        params: dict[str, float],
        param_stability: Optional[dict[str, float]],
    ) -> list[SearchSuggestion]:
        """基于当前结果建议下一轮搜索范围"""
        suggestions = []

        if not param_stability:
            return suggestions

        for name, std in param_stability.items():
            value = params.get(name, 0)
            if value == 0:
                continue

            cv = std / abs(value) if value != 0 else 0  # 变异系数

            if cv > 0.5:
                # 高变异 → 缩小范围
                new_min = max(value - 2 * std, 0)
                new_max = value + 2 * std
                suggestions.append(SearchSuggestion(
                    param_name=name,
                    current_value=round(value, 4),
                    suggested_range=(round(new_min, 4), round(new_max, 4)),
                    reason=f"变异系数 {cv:.2f} 过高，建议缩小搜索范围",
                ))
            elif cv < 0.1 and std > 0:
                # 低变异 → 可以扩大范围探索
                new_min = max(value - 5 * std, 0)
                new_max = value + 5 * std
                suggestions.append(SearchSuggestion(
                    param_name=name,
                    current_value=round(value, 4),
                    suggested_range=(round(new_min, 4), round(new_max, 4)),
                    reason=f"变异系数 {cv:.2f} 很低，参数已收敛，可适当扩大探索",
                ))

        return suggestions[:10]  # 最多返回 10 条建议

    # ── 综合评分 ──

    def _calculate_overall_score(
        self,
        oos_metrics: Optional[PerformanceMetrics],
        overfit_risk: Optional[OverfitRisk],
        validation: Optional[ValidationResult],
    ) -> tuple[float, str]:
        """计算综合评分（0-100）和等级"""
        score = 50.0  # 基准分

        if oos_metrics:
            # Sharpe 贡献（最多 +20）
            score += min(oos_metrics.sharpe_ratio * 10, 20)
            # 胜率贡献（最多 +10）
            score += min((oos_metrics.win_rate - 0.4) * 50, 10) if oos_metrics.win_rate > 0.4 else 0
            # 回撤惩罚（最多 -15）
            score -= min(abs(oos_metrics.max_drawdown_pct), 15)
            # 盈亏比贡献（最多 +10）
            if oos_metrics.profit_factor > 1:
                score += min((oos_metrics.profit_factor - 1) * 5, 10)

        if overfit_risk:
            # 过拟合风险惩罚（最多 -20）
            score -= overfit_risk.score * 20

        if validation:
            # 验证通过奖励
            if validation.passed:
                score += 10
            else:
                score -= 10

        score = max(0, min(100, score))

        # 等级
        if score >= 80:
            grade = "A"
        elif score >= 65:
            grade = "B"
        elif score >= 50:
            grade = "C"
        elif score >= 35:
            grade = "D"
        else:
            grade = "F"

        return round(score, 1), grade

    # ── 行动建议 ──

    def _generate_recommendations(
        self,
        grade: str,
        overfit_risk: Optional[OverfitRisk],
        factor_insights: list[FactorInsight],
        oos_metrics: Optional[PerformanceMetrics],
    ) -> list[str]:
        """生成行动建议"""
        recs = []

        if grade in ("A", "B"):
            recs.append("参数质量良好，建议进入纸盘验证阶段")
        elif grade == "C":
            recs.append("参数质量一般，建议继续优化或积累更多数据")
        else:
            recs.append("参数质量较差，不建议应用到纸盘")

        if overfit_risk and overfit_risk.level in ("high", "critical"):
            recs.extend(overfit_risk.mitigations)

        # 因子集中度警告
        if factor_insights:
            top = factor_insights[0]
            if top.weight > 0.3:
                recs.append(
                    f"因子权重过于集中在 {top.name}（{top.weight:.1%}），"
                    f"建议检查是否过度依赖单一因子"
                )

        if oos_metrics and oos_metrics.total_trades < 30:
            recs.append("OOS 交易笔数不足 30，统计结论可靠性有限")

        return recs

    # ── 总结 ──

    def _generate_summary(
        self,
        grade: str,
        oos_metrics: Optional[PerformanceMetrics],
        overfit_risk: Optional[OverfitRisk],
    ) -> str:
        """生成一句话总结"""
        parts = [f"评级 {grade}"]

        if oos_metrics:
            parts.append(f"OOS Sharpe={oos_metrics.sharpe_ratio:.2f}")
            parts.append(f"PnL={oos_metrics.total_pnl_pct:.2f}%")

        if overfit_risk:
            parts.append(f"过拟合风险={overfit_risk.level}")

        return "，".join(parts)
