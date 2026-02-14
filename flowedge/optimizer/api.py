"""
优化系统 API 端点 — 参数管理、数据查询、回测、优化控制、验证、调度、A/B 测试。

端点列表：
  GET  /optimizer/params          — 获取所有参数（含范围等元信息）
  GET  /optimizer/params/values   — 获取所有参数当前值
  PUT  /optimizer/params          — 批量更新参数
  GET  /optimizer/params/groups   — 获取参数分组
  POST /optimizer/params/snapshot — 创建参数快照
  GET  /optimizer/params/snapshots — 列出所有快照
  GET  /optimizer/params/version  — 获取参数版本元信息
  GET  /optimizer/params/history  — 获取参数变更历史
  GET  /optimizer/params/diff     — 获取版本间 changed keys
  POST /optimizer/params/rollback — 回滚到指定快照
  GET  /optimizer/data/summary    — 数据摘要
  GET  /optimizer/data/quality    — 数据质量报告
  POST /optimizer/backtest        — 执行回测
  POST /optimizer/run             — 启动优化
  GET  /optimizer/status          — 优化状态
  GET  /optimizer/results         — 最近优化结果
  POST /optimizer/apply           — 应用最优参数到 registry
  POST /optimizer/validate        — 三层验证指定参数
  GET  /optimizer/scheduler/status — 调度器状态
  POST /optimizer/scheduler/run   — 手动触发一次调度
  GET  /optimizer/scheduler/history — 调度历史
  POST /optimizer/scheduler/start — 启动后台定时调度
  POST /optimizer/scheduler/stop  — 停止后台定时调度
  POST /optimizer/ab-test         — 执行 A/B 对照测试
  POST /optimizer/evaluate        — AI 评估最近优化结果
  GET  /optimizer/factor-ic       — 因子 IC 分析
  POST /optimizer/evolve          — 执行一轮进化
  GET  /optimizer/evolution/status — 进化引擎状态
  GET  /optimizer/evolution/history — 进化历史
  GET  /optimizer/evolution/trend — 进化趋势
  POST /optimizer/evolution/approve — 人工确认应用
  GET  /optimizer/agent/status    — 总控 Agent 状态
  GET  /optimizer/agent/config    — 总控 Agent 配置
  PUT  /optimizer/agent/config    — 更新总控 Agent 配置
  POST /optimizer/agent/plan      — 生成总控 Agent 执行计划
  POST /optimizer/agent/run       — 执行总控 Agent 一轮计划
  GET  /optimizer/regime/status   — 环境自适应状态
  POST /optimizer/regime/register — 注册环境参数组
  POST /optimizer/regime/switch   — 手动切换环境
  GET  /optimizer/regime/history  — 环境切换历史
  GET  /optimizer/stats           — 优化系统统计
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

logger = logging.getLogger("flowedge.optimizer.api")

router = APIRouter(prefix="/optimizer", tags=["optimizer"])

# 全局引用（在 api.py 主文件中初始化后注入）
_registry = None
_data_manager = None
_optimizer = None
_validator = None
_scheduler = None
_ab_tester = None
_evaluator = None
_evolution = None
_regime_adapter = None
_agent = None


def get_scheduler():
    """获取调度器实例（供 lifespan 启动后台定时用）"""
    return _scheduler


def get_agent():
    """获取 Agent 实例（供 lifespan 启动后台定时用）"""
    return _agent


def init_optimizer_api(registry, data_manager, paper_trader=None):
    """初始化优化 API（由主 api.py 调用）"""
    global _registry, _data_manager, _optimizer, _validator, _scheduler, _ab_tester
    _registry = registry
    _data_manager = data_manager

    from .optimizer import FlowEdgeOptimizer
    from .validator import ParamValidator
    from .scheduler import OptimizationScheduler
    from .ab_test import ABTester

    from .ai_evaluator import AIEvaluator

    _optimizer = FlowEdgeOptimizer(registry, data_manager)
    _validator = ParamValidator(registry=registry)
    _scheduler = OptimizationScheduler(registry, data_manager, _optimizer)
    _ab_tester = ABTester(registry=registry)

    from .evolution import EvolutionEngine
    from .regime_adapter import RegimeAdapter
    from .agent_controller import OptimizationAgentController

    global _evaluator, _evolution, _regime_adapter, _agent
    _evaluator = AIEvaluator()
    _evolution = EvolutionEngine(registry, data_manager, _optimizer, paper_trader=paper_trader)
    _regime_adapter = RegimeAdapter(registry)
    _agent = OptimizationAgentController(
        data_manager=data_manager,
        scheduler=_scheduler,
        evolution=_evolution,
        data_dir="data/optimizer",
    )


# ── 请求模型 ──

class ParamUpdateRequest(BaseModel):
    """参数更新请求"""
    params: dict[str, float]
    label: str = ""  # 快照标签（可选）


class SnapshotRequest(BaseModel):
    """快照请求"""
    label: str = ""


class RollbackRequest(BaseModel):
    """回滚请求"""
    snapshot_name: str


class BacktestRequest(BaseModel):
    """回测请求"""
    params: Optional[dict[str, float]] = None  # None = 使用当前 registry 参数
    exit_window: str = "1h"
    min_score: float = 0.15
    min_confidence: float = 0.30
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 1.5
    symbol: Optional[str] = None


class OptimizeRequest(BaseModel):
    """优化请求"""
    mode: str = "single"                    # single / multi
    n_trials: int = 100
    param_groups: list[str] = ["weights", "signal_thresholds"]
    objective_metric: str = "sharpe_ratio"
    exit_window: str = "1h"
    train_pct: float = 0.7


class ValidateRequest(BaseModel):
    """验证请求"""
    params: dict[str, float]
    train_pct: float = 0.7
    min_sharpe: float = 0.5
    max_drawdown: float = 15.0


class SchedulerRunRequest(BaseModel):
    """手动触发调度请求"""
    n_trials: int = 100
    param_groups: list[str] = ["weights", "signal_thresholds", "gate"]
    auto_apply: bool = False                # 手动触发默认不自动应用


class ABTestRequest(BaseModel):
    """A/B 测试请求"""
    groups: list[dict]                      # [{"name": "A", "params": {...}}, ...]
    exit_window: str = "1h"
    symbol: Optional[str] = None


class AgentConfigRequest(BaseModel):
    """总控 Agent 配置更新请求（全部字段可选）"""
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    timeout_s: Optional[float] = None
    max_retries: Optional[int] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    daily_budget_usd: Optional[float] = None
    max_calls_per_day: Optional[int] = None
    min_samples: Optional[int] = None
    allow_auto_apply: Optional[bool] = None
    enable_external_research: Optional[bool] = None


class AgentRunRequest(BaseModel):
    """总控 Agent 计划/执行请求"""
    goal: str = "auto_optimize"
    dry_run: bool = False
    background: bool = True


# ── 参数管理端点 ──

@router.get("/params")
async def get_params(group: Optional[str] = None):
    """获取所有参数（含范围等元信息）"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    defs = _registry.get_all_defs()
    result = {}
    for name, pdef in defs.items():
        if group and pdef.group != group:
            continue
        result[name] = {
            "value": pdef.value,
            "min": pdef.min_val,
            "max": pdef.max_val,
            "step": pdef.step,
            "type": pdef.param_type,
            "group": pdef.group,
            "description": pdef.description,
            "optimizable": pdef.is_optimizable,
        }
    return {"params": result, "total": len(result)}


@router.get("/params/values")
async def get_param_values(group: Optional[str] = None):
    """获取所有参数当前值（简洁格式）"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    if group:
        values = _registry.get_group(group)
    else:
        values = _registry.get_all()
    return {"values": values, "total": len(values)}


@router.put("/params")
async def update_params(req: ParamUpdateRequest):
    """
    批量更新参数。
    自动创建快照（用于回滚），然后更新参数并通知订阅者。
    """
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    try:
        snapshot_name = _registry.apply_and_save(req.params, label=req.label)
        version_info = _registry.get_version_info()
        return {
            "success": True,
            "updated": len(req.params),
            "snapshot": snapshot_name,
            "version": version_info["version"],
            "message": f"已更新 {len(req.params)} 个参数，快照: {snapshot_name}",
        }
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.get("/params/groups")
async def get_param_groups():
    """获取参数分组"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    groups = _registry.get_groups()
    return {
        "groups": {g: {"count": len(names), "params": names} for g, names in groups.items()},
        "total_groups": len(groups),
    }


@router.post("/params/snapshot")
async def create_snapshot(req: SnapshotRequest):
    """创建参数快照"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    filename = _registry.snapshot(label=req.label)
    return {"success": True, "filename": filename}


@router.get("/params/snapshots")
async def list_snapshots():
    """列出所有快照"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    snapshots = _registry.list_snapshots()
    return {"snapshots": snapshots, "total": len(snapshots)}


@router.get("/params/version")
async def get_param_version():
    """获取参数版本元信息"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    return _registry.get_version_info()


@router.get("/params/history")
async def get_param_history(limit: int = 20):
    """获取参数变更历史（新到旧）"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    safe_limit = max(1, min(limit, 200))
    history = _registry.get_change_history(limit=safe_limit)
    return {"history": history, "total": len(history), "limit": safe_limit}


@router.get("/params/diff")
async def get_param_diff(from_version: int, to_version: Optional[int] = None):
    """获取版本间参数变化 key（仅返回 changed keys）"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    try:
        return _registry.get_changed_keys_between_versions(
            from_version=from_version,
            to_version=to_version,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/params/rollback")
async def rollback_params(req: RollbackRequest):
    """回滚到指定快照"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    try:
        _registry.rollback(req.snapshot_name)
        version_info = _registry.get_version_info()
        return {
            "success": True,
            "version": version_info["version"],
            "message": f"已回滚到快照: {req.snapshot_name}",
        }
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ── 数据管理端点 ──

@router.get("/data/summary")
async def get_data_summary():
    """数据摘要"""
    if not _data_manager:
        raise HTTPException(500, "数据管理器未初始化")

    return _data_manager.summary()


@router.get("/data/quality")
async def get_data_quality():
    """数据质量报告"""
    if not _data_manager:
        raise HTTPException(500, "数据管理器未初始化")

    report = _data_manager.quality_check()
    return {
        "total_records": report.total_records,
        "records_with_5m": report.records_with_5m,
        "records_with_15m": report.records_with_15m,
        "records_with_1h": report.records_with_1h,
        "records_with_factors": report.records_with_factors,
        "symbols": report.symbols,
        "date_range_days": report.date_range_days,
        "min_sample_ok": report.min_sample_ok,
        "issues": report.issues,
    }


# ── 回测端点 ──

@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    """执行回测（使用指定参数或当前 registry 参数）"""
    if not _data_manager or not _registry:
        raise HTTPException(500, "优化系统未初始化")

    from .backtester import SignalBacktester, BacktestConfig

    records = _data_manager.load_all(symbol=req.symbol, require_1h=True)
    if not records:
        raise HTTPException(400, "无可用信号数据")

    bt = SignalBacktester(registry=_registry)
    config = BacktestConfig(
        exit_window=req.exit_window,
        min_score=req.min_score,
        min_confidence=req.min_confidence,
        stop_loss_pct=req.stop_loss_pct,
        take_profit_pct=req.take_profit_pct,
    )
    result = bt.run(records, params=req.params, config=config)

    return {
        "total_signals": result.total_signals,
        "filtered_signals": result.filtered_signals,
        "traded_signals": result.traded_signals,
        "metrics": {
            "total_trades": result.metrics.total_trades,
            "win_rate": result.metrics.win_rate,
            "total_pnl_pct": result.metrics.total_pnl_pct,
            "avg_pnl_pct": result.metrics.avg_pnl_pct,
            "sharpe_ratio": result.metrics.sharpe_ratio,
            "max_drawdown_pct": result.metrics.max_drawdown_pct,
            "profit_factor": result.metrics.profit_factor,
            "calmar_ratio": result.metrics.calmar_ratio,
            "expectancy": result.metrics.expectancy,
            "max_consecutive_wins": result.metrics.max_consecutive_wins,
            "max_consecutive_losses": result.metrics.max_consecutive_losses,
            "ic_score": result.metrics.ic_score,
        },
    }


# ── 优化端点 ──

@router.post("/run")
async def start_optimization(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """启动参数优化（后台执行）"""
    if not _optimizer:
        raise HTTPException(500, "优化器未初始化")
    if _optimizer.is_running:
        raise HTTPException(409, "优化正在运行中，请等待完成")

    from .optimizer import OptimizationConfig
    from .backtester import BacktestConfig

    records = _data_manager.load_all(require_1h=True)
    if not records:
        raise HTTPException(400, "无可用信号数据")

    # 数据分割
    split = _data_manager.time_split(
        records,
        train_pct=req.train_pct,
        val_pct=1.0 - req.train_pct,
        test_pct=0.0,
    )

    opt_config = OptimizationConfig(
        mode=req.mode,
        n_trials=req.n_trials,
        param_groups=req.param_groups,
        objective_metric=req.objective_metric,
        backtest_config=BacktestConfig(exit_window=req.exit_window),
    )

    def _run_optimization():
        try:
            _optimizer.run(split.train, opt_config)
            logger.info(f"优化完成: {_optimizer.last_result.study_name if _optimizer.last_result else 'unknown'}")
        except RuntimeError as e:
            logger.warning(f"优化被跳过: {e}")
        except Exception as e:
            logger.error(f"优化失败: {e}")

    background_tasks.add_task(_run_optimization)

    return {
        "message": "优化已启动（后台执行）",
        "mode": req.mode,
        "n_trials": req.n_trials,
        "train_size": len(split.train),
        "param_groups": req.param_groups,
    }


@router.get("/status")
async def get_optimization_status():
    """获取优化状态"""
    if not _optimizer:
        raise HTTPException(500, "优化器未初始化")
    return _optimizer.get_status()


@router.get("/results")
async def get_optimization_results():
    """获取最近优化结果"""
    if not _optimizer:
        raise HTTPException(500, "优化器未初始化")

    result = _optimizer.last_result
    if not result:
        return {"message": "暂无优化结果"}

    response = {
        "study_name": result.study_name,
        "mode": result.mode,
        "n_trials_completed": result.n_trials_completed,
        "best_value": result.best_value,
        "elapsed_s": result.elapsed_s,
        "best_params": result.best_params,
    }

    if result.best_metrics:
        response["best_metrics"] = {
            "total_trades": result.best_metrics.total_trades,
            "win_rate": result.best_metrics.win_rate,
            "total_pnl_pct": result.best_metrics.total_pnl_pct,
            "sharpe_ratio": result.best_metrics.sharpe_ratio,
            "max_drawdown_pct": result.best_metrics.max_drawdown_pct,
            "profit_factor": result.best_metrics.profit_factor,
        }

    if result.param_stability:
        response["param_stability"] = result.param_stability

    if result.wf_results:
        response["walk_forward"] = result.wf_results

    return response


@router.post("/apply")
async def apply_optimization_results():
    """将最近优化的最优参数应用到 registry"""
    if not _optimizer or not _registry:
        raise HTTPException(500, "优化系统未初始化")

    result = _optimizer.last_result
    if not result or not result.best_params:
        raise HTTPException(400, "暂无可应用的优化结果")

    snapshot_name = _registry.apply_and_save(
        result.best_params,
        label=f"optimizer_{result.study_name}",
    )

    return {
        "success": True,
        "applied_params": len(result.best_params),
        "snapshot": snapshot_name,
        "message": f"已应用 {len(result.best_params)} 个最优参数，快照: {snapshot_name}",
    }


# ── 验证端点 ──

@router.post("/validate")
async def validate_params(req: ValidateRequest):
    """对指定参数执行三层验证"""
    if not _validator or not _data_manager:
        raise HTTPException(500, "优化系统未初始化")

    records = _data_manager.load_all(require_1h=True)
    if not records:
        raise HTTPException(400, "无可用信号数据")

    # 分割数据
    split = _data_manager.time_split(records, train_pct=req.train_pct, val_pct=0, test_pct=1.0 - req.train_pct)

    from .validator import ValidationThresholds, ParamValidator
    thresholds = ValidationThresholds(min_sharpe=req.min_sharpe, max_drawdown=req.max_drawdown)
    validator = ParamValidator(registry=_registry, thresholds=thresholds)

    result = validator.validate(
        params=req.params,
        oos_data=split.test,
    )

    return {
        "passed": result.passed,
        "overall_score": result.overall_score,
        "layers": [
            {
                "layer": lr.layer,
                "passed": lr.passed,
                "score": lr.score,
                "warnings": lr.warnings,
                "details": {k: v for k, v in lr.details.items() if k != "trades"},
            }
            for lr in result.layers
        ],
        "recommendations": result.recommendations,
    }


# ── 调度器端点 ──

@router.get("/scheduler/status")
async def get_scheduler_status():
    """获取调度器状态"""
    if not _scheduler:
        raise HTTPException(500, "调度器未初始化")
    return _scheduler.get_status()


@router.post("/scheduler/run")
async def trigger_scheduler_run(req: SchedulerRunRequest, background_tasks: BackgroundTasks):
    """手动触发一次优化调度"""
    if not _scheduler:
        raise HTTPException(500, "调度器未初始化")
    if _scheduler.is_running:
        raise HTTPException(409, "调度器正在运行中")
    if _optimizer and _optimizer.is_running:
        raise HTTPException(409, "优化器正在运行中，请稍后再触发调度")

    from .scheduler import SchedulerConfig
    cfg = SchedulerConfig(
        n_trials=req.n_trials,
        param_groups=req.param_groups,
        auto_apply=req.auto_apply,
    )

    def _run():
        try:
            _scheduler.run_once(config=cfg)
        except Exception as e:
            logger.error(f"调度执行失败: {e}")

    background_tasks.add_task(_run)

    return {
        "message": "调度已触发（后台执行）",
        "n_trials": req.n_trials,
        "auto_apply": req.auto_apply,
    }


@router.get("/scheduler/history")
async def get_scheduler_history(limit: int = 10):
    """获取调度历史"""
    if not _scheduler:
        raise HTTPException(500, "调度器未初始化")
    return {"history": _scheduler.get_history(limit=limit)}


@router.post("/scheduler/start")
async def start_scheduler_background():
    """启动后台定时调度"""
    if not _scheduler:
        raise HTTPException(500, "调度器未初始化")
    await _scheduler.start_background()
    return {"message": "后台调度已启动", "interval_days": _scheduler._config.interval_days}


@router.post("/scheduler/stop")
async def stop_scheduler_background():
    """停止后台定时调度"""
    if not _scheduler:
        raise HTTPException(500, "调度器未初始化")
    await _scheduler.stop_background()
    return {"message": "后台调度已停止"}


# ── A/B 测试端点 ──

@router.post("/ab-test")
async def run_ab_test(req: ABTestRequest):
    """执行 A/B 对照测试"""
    if not _ab_tester or not _data_manager:
        raise HTTPException(500, "优化系统未初始化")

    records = _data_manager.load_all(symbol=req.symbol, require_1h=True)
    if not records:
        raise HTTPException(400, "无可用信号数据")

    from .ab_test import ABGroup
    from .backtester import BacktestConfig

    groups = []
    for g in req.groups:
        if "name" not in g or "params" not in g:
            raise HTTPException(400, "每个 group 必须包含 name 和 params")
        groups.append(ABGroup(
            name=g["name"],
            params=g["params"],
            description=g.get("description", ""),
        ))

    if len(groups) < 2:
        raise HTTPException(400, "至少需要 2 个参数组")

    config = BacktestConfig(exit_window=req.exit_window)
    result = _ab_tester.run(data=records, groups=groups, config=config)

    return {
        "best_group": result.best_group,
        "summary": result.summary,
        "groups": [
            {
                "name": g.name,
                "sharpe_ratio": g.metrics.sharpe_ratio,
                "total_pnl_pct": g.metrics.total_pnl_pct,
                "win_rate": g.metrics.win_rate,
                "total_trades": g.metrics.total_trades,
                "max_drawdown_pct": g.metrics.max_drawdown_pct,
                "profit_factor": g.metrics.profit_factor,
            }
            for g in result.groups
        ],
        "comparisons": [
            {
                "group_a": c.group_a,
                "group_b": c.group_b,
                "sharpe_diff": c.sharpe_diff,
                "pnl_diff": c.pnl_diff,
                "p_value_sharpe": c.p_value_sharpe,
                "p_value_pnl": c.p_value_pnl,
                "significant": c.significant,
                "recommendation": c.recommendation,
            }
            for c in result.comparisons
        ],
    }


# ── AI 评估端点 ──

@router.post("/evaluate")
async def evaluate_optimization():
    """AI 评估最近一次优化结果"""
    if not _evaluator or not _optimizer:
        raise HTTPException(500, "优化系统未初始化")

    opt_result = _optimizer.last_result
    if not opt_result or not opt_result.best_params:
        raise HTTPException(400, "暂无可评估的优化结果")

    report = _evaluator.evaluate(
        params=opt_result.best_params,
        train_metrics=opt_result.best_metrics,
        param_stability=opt_result.param_stability,
    )

    return {
        "overall_grade": report.overall_grade,
        "overall_score": report.overall_score,
        "summary": report.summary,
        "factor_insights": [
            {
                "name": f.name,
                "weight": f.weight,
                "rank": f.rank,
                "interpretation": f.interpretation,
                "strength": f.strength,
            }
            for f in report.factor_insights
        ],
        "overfit_risk": {
            "level": report.overfit_risk.level,
            "score": report.overfit_risk.score,
            "factors": report.overfit_risk.factors,
            "mitigations": report.overfit_risk.mitigations,
        } if report.overfit_risk else None,
        "market_fit": {
            "best_regime": report.market_fit.best_regime,
            "regime_scores": report.market_fit.regime_scores,
            "explanation": report.market_fit.explanation,
        } if report.market_fit else None,
        "search_suggestions": [
            {
                "param_name": s.param_name,
                "current_value": s.current_value,
                "suggested_range": list(s.suggested_range),
                "reason": s.reason,
            }
            for s in report.search_suggestions
        ],
        "recommendations": report.recommendations,
        "metrics_summary": report.metrics_summary,
    }


@router.get("/factor-ic")
async def get_factor_ic_analysis():
    """因子 IC（信息系数）分析 — 各因子得分与实际收益的相关性"""
    if not _data_manager:
        raise HTTPException(500, "数据管理器未初始化")

    records = _data_manager.load_all(require_1h=True)
    if not records:
        raise HTTPException(400, "无可用信号数据")

    # 收集有因子明细的记录
    records_with_factors = [r for r in records if r.factor_scores]
    if not records_with_factors:
        return {"message": "无因子明细数据，需要系统运行积累数据", "factors": []}

    # 计算每个因子的 IC
    from .metrics import _pearson_corr

    # 收集所有因子名
    all_factors = set()
    for r in records_with_factors:
        all_factors.update(r.factor_scores.keys())

    # 计算实际收益（使用 1h 价格）
    pnls = []
    for r in records_with_factors:
        if r.price_1h and r.entry_price and r.entry_price > 0:
            pnl = (r.price_1h - r.entry_price) / r.entry_price * 100
            pnls.append(pnl)
        else:
            pnls.append(0)

    factor_ics = []
    for factor in sorted(all_factors):
        scores = [r.factor_scores.get(factor, 0) for r in records_with_factors]
        ic = _pearson_corr(scores, pnls)
        abs_ic = abs(ic)

        if abs_ic >= 0.1:
            quality = "strong"
        elif abs_ic >= 0.05:
            quality = "moderate"
        else:
            quality = "weak"

        factor_ics.append({
            "factor": factor,
            "ic": round(ic, 4),
            "abs_ic": round(abs_ic, 4),
            "quality": quality,
            "n_samples": len(scores),
        })

    # 按 |IC| 排序
    factor_ics.sort(key=lambda x: x["abs_ic"], reverse=True)

    return {
        "total_records": len(records_with_factors),
        "factors": factor_ics,
    }


# ── 进化引擎端点 ──

@router.post("/evolve")
async def run_evolution(background_tasks: BackgroundTasks):
    """执行一轮完整进化（后台执行）"""
    if not _evolution:
        raise HTTPException(500, "进化引擎未初始化")
    if _optimizer and _optimizer.is_running:
        raise HTTPException(409, "优化器正在运行中，请稍后再触发进化")

    def _run():
        try:
            _evolution.evolve()
        except RuntimeError as e:
            logger.warning(f"进化被跳过: {e}")
        except Exception as e:
            logger.error(f"进化执行失败: {e}")

    background_tasks.add_task(_run)
    return {"message": "进化已触发（后台执行）"}


@router.get("/evolution/status")
async def get_evolution_status():
    """获取进化引擎状态"""
    if not _evolution:
        raise HTTPException(500, "进化引擎未初始化")
    return _evolution.get_status()


@router.get("/evolution/history")
async def get_evolution_history(limit: int = 10):
    """获取进化历史"""
    if not _evolution:
        raise HTTPException(500, "进化引擎未初始化")
    return {"history": _evolution.get_history(limit=limit)}


@router.get("/evolution/trend")
async def get_evolution_trend():
    """获取进化趋势"""
    if not _evolution:
        raise HTTPException(500, "进化引擎未初始化")
    return _evolution.get_evolution_trend()


@router.post("/evolution/approve")
async def approve_evolution(cycle_id: str):
    """人工确认并应用指定进化周期的参数"""
    if not _evolution:
        raise HTTPException(500, "进化引擎未初始化")
    result = _evolution.approve_and_apply(cycle_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


# ── 总控 Agent 端点 ──

def _model_to_dict(model: BaseModel) -> dict:
    """兼容 Pydantic v1/v2 的 dict 导出"""
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=True)
    return model.dict(exclude_none=True)


@router.get("/agent/status")
async def get_agent_status():
    """获取总控 Agent 状态"""
    if not _agent:
        raise HTTPException(500, "总控 Agent 未初始化")
    return _agent.get_status()


@router.get("/agent/config")
async def get_agent_config():
    """获取总控 Agent 配置"""
    if not _agent:
        raise HTTPException(500, "总控 Agent 未初始化")
    return _agent.get_config()


@router.put("/agent/config")
async def update_agent_config(req: AgentConfigRequest):
    """更新总控 Agent 配置"""
    if not _agent:
        raise HTTPException(500, "总控 Agent 未初始化")
    try:
        return _agent.update_config(_model_to_dict(req))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/agent/plan")
async def generate_agent_plan(req: AgentRunRequest):
    """生成总控 Agent 执行计划（不执行）"""
    if not _agent:
        raise HTTPException(500, "总控 Agent 未初始化")
    return _agent.plan(goal=req.goal)


@router.post("/agent/run")
async def run_agent_cycle(req: AgentRunRequest, background_tasks: BackgroundTasks):
    """执行总控 Agent 一轮计划（默认后台）"""
    if not _agent:
        raise HTTPException(500, "总控 Agent 未初始化")
    if _optimizer and _optimizer.is_running:
        raise HTTPException(409, "优化器正在运行中，请稍后再触发 Agent")

    if req.background:
        def _run():
            try:
                _agent.run_once(goal=req.goal, dry_run=req.dry_run)
            except Exception as e:
                logger.error(f"Agent 执行失败: {e}")

        background_tasks.add_task(_run)
        return {
            "message": "Agent 已触发（后台执行）",
            "goal": req.goal,
            "dry_run": req.dry_run,
        }

    return _agent.run_once(goal=req.goal, dry_run=req.dry_run)


# ── 环境自适应端点 ──

@router.get("/regime/status")
async def get_regime_status():
    """获取环境自适应状态"""
    if not _regime_adapter:
        raise HTTPException(500, "环境自适应未初始化")
    return _regime_adapter.get_status()


@router.post("/regime/register")
async def register_regime_params(regime: str, params: dict[str, float], label: str = ""):
    """注册一个环境的参数组"""
    if not _regime_adapter:
        raise HTTPException(500, "环境自适应未初始化")
    _regime_adapter.register_params(regime, params, label)
    return {"success": True, "message": f"已注册 {regime} 参数组（{len(params)} 个参数）"}


@router.post("/regime/switch")
async def switch_regime(regime: str, confidence: float = 1.0):
    """手动切换市场环境"""
    if not _regime_adapter:
        raise HTTPException(500, "环境自适应未初始化")
    applied = _regime_adapter.on_regime_change(regime, confidence)
    return {
        "switched": applied,
        "current_regime": _regime_adapter.current_regime,
    }


@router.get("/regime/history")
async def get_regime_history(limit: int = 20):
    """获取环境切换历史"""
    if not _regime_adapter:
        raise HTTPException(500, "环境自适应未初始化")
    return {"history": _regime_adapter.get_switch_history(limit=limit)}


# ── 系统统计 ──

@router.get("/stats")
async def get_optimizer_stats():
    """优化系统统计"""
    if not _registry:
        raise HTTPException(500, "优化系统未初始化")

    stats = _registry.stats()
    data_summary = _data_manager.summary() if _data_manager else {"error": "数据管理器未初始化"}
    optimizer_status = _optimizer.get_status() if _optimizer else {"error": "优化器未初始化"}
    scheduler_status = _scheduler.get_status() if _scheduler else {"error": "调度器未初始化"}
    evolution_status = _evolution.get_status() if _evolution else {"error": "进化引擎未初始化"}
    regime_status = _regime_adapter.get_status() if _regime_adapter else {"error": "环境自适应未初始化"}
    agent_status = _agent.get_status() if _agent else {"error": "总控 Agent 未初始化"}

    return {
        "registry": stats,
        "data": data_summary,
        "optimizer": optimizer_status,
        "scheduler": scheduler_status,
        "evolution": evolution_status,
        "regime_adapter": regime_status,
        "agent": agent_status,
    }
