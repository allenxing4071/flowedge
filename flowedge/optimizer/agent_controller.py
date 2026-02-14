"""
优化总控 Agent 控制器。

本文件核心用途：
  1. 统一管理 Agent 的模型 API 配置（默认 Qwen，可切 DeepSeek/OpenAI）。
  2. 为自动化流程提供“计划 → 执行”入口（调度器 / 进化引擎）。
  3. 提供重试、超时、预算与调用次数等护栏，避免失控调用。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from .data_manager import DataManager
from .evolution import EvolutionConfig, EvolutionEngine
from .scheduler import OptimizationScheduler, SchedulerConfig

logger = logging.getLogger("flowedge.optimizer.agent_controller")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _safe_json_extract(text: str) -> Optional[dict[str, Any]]:
    """从模型文本中提取 JSON 对象（容忍代码块/前后噪声）"""
    text = (text or "").strip()
    if not text:
        return None

    # 直接 JSON
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fenced:
        try:
            data = json.loads(fenced.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 兜底：第一个 { ... } 区间
    l = text.find("{")
    r = text.rfind("}")
    if l >= 0 and r > l:
        frag = text[l : r + 1]
        try:
            data = json.loads(frag)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
    return None


@dataclass
class AgentProviderConfig:
    provider: str = "qwen"  # qwen / deepseek / openai
    model: str = "qwen-plus-latest"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key_env: str = "DASHSCOPE_API_KEY"
    timeout_s: float = 25.0
    max_retries: int = 2
    temperature: float = 0.1
    max_output_tokens: int = 1200


@dataclass
class AgentGuardrailConfig:
    daily_budget_usd: float = 8.0
    max_calls_per_day: int = 200
    min_samples: int = 60
    allow_auto_apply: bool = False
    enable_external_research: bool = True


@dataclass
class AgentRuntimeState:
    day_key: str = field(default_factory=_today_utc)
    calls_today: int = 0
    est_cost_today_usd: float = 0.0
    last_run_at: str = ""
    last_error: str = ""
    last_plan: dict[str, Any] = field(default_factory=dict)
    last_result: dict[str, Any] = field(default_factory=dict)


class OptimizationAgentController:
    """
    优化总控 Agent。

    当前版本职责（轻量）：
      - 生成下一步执行计划（hold / scheduler_run / evolve）
      - 按计划触发现有优化引擎，不直接改底层逻辑
      - 将预算与调用次数作为硬护栏
    """

    PROVIDER_PRESETS: dict[str, dict[str, str]] = {
        "qwen": {
            "model": "qwen-plus-latest",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_env": "DASHSCOPE_API_KEY",
        },
        "deepseek": {
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "openai": {
            "model": "gpt-5-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key_env": "OPENAI_API_KEY",
        },
    }

    # 仅用于预算粗估（美元 / 1M tokens）
    PROVIDER_PRICE_ESTIMATE: dict[str, tuple[float, float]] = {
        "qwen": (0.4, 1.2),
        "deepseek": (0.27, 1.10),
        "openai": (0.25, 2.0),
    }

    ALLOWED_ACTIONS = {"hold", "scheduler_run", "evolve"}

    def __init__(
        self,
        data_manager: DataManager,
        scheduler: OptimizationScheduler,
        evolution: EvolutionEngine,
        data_dir: str = "data/optimizer",
    ):
        self._data_manager = data_manager
        self._scheduler = scheduler
        self._evolution = evolution

        self._data_dir = Path(data_dir)
        self._config_path = self._data_dir / "agent_config.json"
        self._state_path = self._data_dir / "agent_state.json"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._enabled = True
        self._provider = AgentProviderConfig()
        self._guardrail = AgentGuardrailConfig()
        self._state = AgentRuntimeState()

        self._load()
        self._apply_env_overrides()

    # ── 外部接口 ──

    def get_config(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "provider": asdict(self._provider),
            "guardrail": asdict(self._guardrail),
            "api_key_set": bool(os.getenv(self._provider.api_key_env, "")),
            "config_file": str(self._config_path),
            "state_file": str(self._state_path),
        }

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        provider = updates.get("provider")
        if provider:
            provider = str(provider).strip().lower()
            if provider not in self.PROVIDER_PRESETS:
                raise ValueError(f"不支持的 provider: {provider}")
            self._provider.provider = provider
            preset = self.PROVIDER_PRESETS[provider]
            self._provider.model = preset["model"]
            self._provider.base_url = preset["base_url"]
            self._provider.api_key_env = preset["api_key_env"]

        if "enabled" in updates:
            self._enabled = bool(updates["enabled"])

        for key in (
            "model",
            "base_url",
            "api_key_env",
            "timeout_s",
            "max_retries",
            "temperature",
            "max_output_tokens",
        ):
            if key in updates and updates[key] is not None:
                setattr(self._provider, key, updates[key])

        for key in (
            "daily_budget_usd",
            "max_calls_per_day",
            "min_samples",
            "allow_auto_apply",
            "enable_external_research",
        ):
            if key in updates and updates[key] is not None:
                setattr(self._guardrail, key, updates[key])

        self._save()
        return self.get_config()

    def get_status(self) -> dict[str, Any]:
        self._rollover_daily_if_needed()
        return {
            "enabled": self._enabled,
            "provider": self._provider.provider,
            "model": self._provider.model,
            "api_key_env": self._provider.api_key_env,
            "api_key_set": bool(os.getenv(self._provider.api_key_env, "")),
            "calls_today": self._state.calls_today,
            "max_calls_per_day": self._guardrail.max_calls_per_day,
            "est_cost_today_usd": round(self._state.est_cost_today_usd, 6),
            "daily_budget_usd": self._guardrail.daily_budget_usd,
            "last_run_at": self._state.last_run_at,
            "last_error": self._state.last_error,
            "last_plan": self._state.last_plan,
            "last_result": self._state.last_result,
            "scheduler": self._scheduler.get_status() if self._scheduler else {},
            "evolution": self._evolution.get_status() if self._evolution else {},
        }

    def plan(self, goal: str = "auto_optimize") -> dict[str, Any]:
        """
        生成下一步动作计划。
        """
        self._rollover_daily_if_needed()

        if not self._enabled:
            plan = self._build_hold_plan("Agent 已禁用")
            self._state.last_plan = plan
            self._save_state()
            return plan

        summary = self._data_manager.summary()
        quality = self._data_manager.quality_check()
        total_records = int(getattr(quality, "total_records", 0))

        if total_records < self._guardrail.min_samples:
            plan = self._build_hold_plan(
                f"样本不足: {total_records} < {self._guardrail.min_samples}"
            )
            plan["context"] = {"total_records": total_records, "goal": goal}
            self._state.last_plan = plan
            self._save_state()
            return plan

        if not self._has_api_key():
            # 无模型 Key 时的确定性降级策略
            plan = {
                "action": "evolve",
                "reason": "未配置模型 API Key，使用内置规则触发进化",
                "source": "rule_fallback",
                "goal": goal,
                "config": {
                    "n_trials": 100,
                    "param_groups": ["weights", "signal_thresholds", "gate"],
                    "auto_apply": self._guardrail.allow_auto_apply,
                    "run_ai_eval": True,
                    "run_ab_test": True,
                },
                "need_external_research": False,
                "research_queries": [],
                "confidence": 0.6,
            }
            self._state.last_plan = plan
            self._save_state()
            return plan

        prompt_payload = {
            "goal": goal,
            "samples": {
                "total_records": total_records,
                "date_range_days": getattr(quality, "date_range_days", 0.0),
                "symbols": getattr(quality, "symbols", []),
                "records_with_factors": getattr(quality, "records_with_factors", 0),
                "issues": getattr(quality, "issues", []),
            },
            "optimizer_summary": summary,
            "guardrail": asdict(self._guardrail),
        }
        plan = self._llm_plan(prompt_payload)
        self._state.last_plan = plan
        self._save_state()
        return plan

    def run_once(self, goal: str = "auto_optimize", dry_run: bool = False) -> dict[str, Any]:
        """
        执行一轮 Agent 计划（或 dry-run 仅返回计划）。
        """
        plan = self.plan(goal=goal)
        result: dict[str, Any] = {
            "started_at": _utc_iso(),
            "goal": goal,
            "dry_run": dry_run,
            "plan": plan,
            "executed": False,
        }

        action = plan.get("action", "hold")
        if dry_run or action == "hold":
            result["status"] = "planned" if dry_run else "held"
            self._state.last_run_at = result["started_at"]
            self._state.last_result = result
            self._state.last_error = ""
            self._save_state()
            return result

        try:
            if action == "scheduler_run":
                cfg_data = plan.get("config", {})
                cfg = SchedulerConfig(
                    n_trials=int(cfg_data.get("n_trials", 100)),
                    param_groups=list(
                        cfg_data.get("param_groups", ["weights", "signal_thresholds", "gate"])
                    ),
                    auto_apply=self._guardrail.allow_auto_apply,
                )
                run = self._scheduler.run_once(config=cfg)
                result["status"] = run.status
                result["executed"] = True
                result["scheduler_run"] = asdict(run)
            elif action == "evolve":
                cfg_data = plan.get("config", {})
                cfg = EvolutionConfig(
                    n_trials=int(cfg_data.get("n_trials", 100)),
                    param_groups=list(
                        cfg_data.get("param_groups", ["weights", "signal_thresholds", "gate"])
                    ),
                    run_ai_eval=bool(cfg_data.get("run_ai_eval", True)),
                    run_ab_test=bool(cfg_data.get("run_ab_test", True)),
                    auto_apply=self._guardrail.allow_auto_apply,
                )
                cycle = self._evolution.evolve(config=cfg)
                result["status"] = cycle.status
                result["executed"] = True
                result["evolution_cycle"] = asdict(cycle)
            else:
                result["status"] = "held"
                result["executed"] = False
                result["message"] = f"未知动作: {action}"
        except Exception as e:
            self._state.last_error = str(e)
            result["status"] = "failed"
            result["error"] = str(e)

        result["finished_at"] = _utc_iso()
        self._state.last_run_at = result["finished_at"]
        self._state.last_result = result
        self._save_state()
        return result

    # ── 内部实现 ──

    def _build_hold_plan(self, reason: str) -> dict[str, Any]:
        return {
            "action": "hold",
            "reason": reason,
            "source": "rule",
            "config": {},
            "need_external_research": False,
            "research_queries": [],
            "confidence": 1.0,
        }

    def _has_api_key(self) -> bool:
        return bool(os.getenv(self._provider.api_key_env, ""))

    def _llm_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._rollover_daily_if_needed()
        if self._state.calls_today >= self._guardrail.max_calls_per_day:
            return self._build_hold_plan("达到每日调用上限，暂停模型调用")
        if self._state.est_cost_today_usd >= self._guardrail.daily_budget_usd:
            return self._build_hold_plan("达到每日预算上限，暂停模型调用")

        api_key = os.getenv(self._provider.api_key_env, "")
        if not api_key:
            return self._build_hold_plan(f"环境变量 {self._provider.api_key_env} 未配置")

        system_prompt = (
            "你是 FlowEdge 的优化总控 Agent。"
            "请基于输入上下文输出一个 JSON 对象，不要输出额外解释。"
            "仅允许 action: hold | scheduler_run | evolve。"
            "默认禁止自动应用参数（auto_apply=false）。"
        )
        user_prompt = json.dumps(
            {
                "task": "为下一轮自动优化生成执行计划",
                "required_output_schema": {
                    "action": "hold|scheduler_run|evolve",
                    "reason": "string",
                    "config": {
                        "n_trials": "int",
                        "param_groups": "list[str]",
                        "run_ai_eval": "bool(optional, evolve only)",
                        "run_ab_test": "bool(optional, evolve only)",
                        "auto_apply": "bool(false)",
                    },
                    "need_external_research": "bool",
                    "research_queries": "list[str]",
                    "confidence": "0~1 float",
                },
                "context": payload,
                "rules": [
                    "若样本量不足或存在严重数据问题，必须选择 hold",
                    "action=hold 时 config 必须为空对象",
                    "若选择 scheduler_run/evolve，n_trials 建议 50~200",
                    "auto_apply 必须为 false",
                ],
            },
            ensure_ascii=False,
        )

        url = self._provider.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body = {
            "model": self._provider.model,
            "temperature": self._provider.temperature,
            "max_tokens": self._provider.max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        last_error = ""
        for i in range(max(1, int(self._provider.max_retries) + 1)):
            try:
                with httpx.Client(timeout=float(self._provider.timeout_s)) as client:
                    resp = client.post(url, headers=headers, json=body)
                    resp.raise_for_status()
                    data = resp.json()

                usage = data.get("usage", {}) if isinstance(data, dict) else {}
                prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
                completion_tokens = int(usage.get("completion_tokens", 0) or 0)
                self._state.calls_today += 1
                self._state.est_cost_today_usd += self._estimate_cost(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )

                content = ""
                choices = data.get("choices", []) if isinstance(data, dict) else []
                if choices:
                    content = choices[0].get("message", {}).get("content", "") or ""
                parsed = _safe_json_extract(content)
                if not parsed:
                    raise ValueError("模型返回非 JSON")
                return self._normalize_plan(parsed)
            except Exception as e:
                last_error = str(e)
                if i < int(self._provider.max_retries):
                    time.sleep(0.4 * (2**i))
                continue

        self._state.last_error = f"LLM 调用失败: {last_error}"
        return self._build_hold_plan(f"LLM 调用失败，降级 hold：{last_error}")

    def _normalize_plan(self, raw: dict[str, Any]) -> dict[str, Any]:
        action = str(raw.get("action", "hold")).strip().lower()
        if action not in self.ALLOWED_ACTIONS:
            action = "hold"

        reason = str(raw.get("reason", "")).strip() or "模型未提供原因"

        config = raw.get("config", {}) if isinstance(raw.get("config"), dict) else {}
        if action == "hold":
            config = {}
        else:
            n_trials = int(config.get("n_trials", 100))
            n_trials = max(20, min(n_trials, 300))
            groups = config.get("param_groups", ["weights", "signal_thresholds", "gate"])
            if not isinstance(groups, list) or not groups:
                groups = ["weights", "signal_thresholds", "gate"]
            safe_groups = [
                g for g in groups if g in {"weights", "signal_thresholds", "gate", "detector", "feature", "confidence"}
            ] or ["weights", "signal_thresholds", "gate"]
            config = {
                "n_trials": n_trials,
                "param_groups": safe_groups,
                "run_ai_eval": bool(config.get("run_ai_eval", True)),
                "run_ab_test": bool(config.get("run_ab_test", True)),
                "auto_apply": False,  # 强制 fail-closed
            }

        queries = raw.get("research_queries", [])
        if not isinstance(queries, list):
            queries = []
        queries = [str(q).strip() for q in queries if str(q).strip()][:10]

        confidence = raw.get("confidence", 0.6)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.6
        confidence = max(0.0, min(1.0, confidence))

        return {
            "action": action,
            "reason": reason,
            "source": "llm",
            "config": config,
            "need_external_research": bool(raw.get("need_external_research", False))
            and self._guardrail.enable_external_research,
            "research_queries": queries,
            "confidence": confidence,
        }

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        # 若供应商未返回 usage，则按经验字符转 token 粗估（近似）
        if prompt_tokens <= 0 and completion_tokens <= 0:
            prompt_tokens = 2000
            completion_tokens = 600

        in_price, out_price = self.PROVIDER_PRICE_ESTIMATE.get(
            self._provider.provider, (0.5, 1.5)
        )
        return (prompt_tokens / 1_000_000) * in_price + (
            completion_tokens / 1_000_000
        ) * out_price

    def _rollover_daily_if_needed(self) -> None:
        today = _today_utc()
        if self._state.day_key != today:
            self._state.day_key = today
            self._state.calls_today = 0
            self._state.est_cost_today_usd = 0.0
            self._save_state()

    def _apply_env_overrides(self) -> None:
        provider = os.getenv("OPT_AGENT_PROVIDER", "").strip().lower()
        if provider in self.PROVIDER_PRESETS:
            preset = self.PROVIDER_PRESETS[provider]
            self._provider.provider = provider
            self._provider.model = preset["model"]
            self._provider.base_url = preset["base_url"]
            self._provider.api_key_env = preset["api_key_env"]

        if os.getenv("OPT_AGENT_MODEL"):
            self._provider.model = os.getenv("OPT_AGENT_MODEL", self._provider.model)
        if os.getenv("OPT_AGENT_BASE_URL"):
            self._provider.base_url = os.getenv(
                "OPT_AGENT_BASE_URL", self._provider.base_url
            )
        if os.getenv("OPT_AGENT_API_KEY_ENV"):
            self._provider.api_key_env = os.getenv(
                "OPT_AGENT_API_KEY_ENV", self._provider.api_key_env
            )

    def _load(self) -> None:
        # 配置
        if self._config_path.exists():
            try:
                data = json.loads(self._config_path.read_text(encoding="utf-8"))
                self._enabled = bool(data.get("enabled", self._enabled))
                p = data.get("provider", {})
                g = data.get("guardrail", {})
                for k in asdict(self._provider).keys():
                    if k in p:
                        setattr(self._provider, k, p[k])
                for k in asdict(self._guardrail).keys():
                    if k in g:
                        setattr(self._guardrail, k, g[k])
            except Exception as e:
                logger.warning(f"[Agent] 加载配置失败，使用默认值: {e}")

        # 状态
        if self._state_path.exists():
            try:
                s = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._state = AgentRuntimeState(**s)
            except Exception as e:
                logger.warning(f"[Agent] 加载状态失败，重新初始化: {e}")

    def _save(self) -> None:
        cfg_data = {
            "enabled": self._enabled,
            "provider": asdict(self._provider),
            "guardrail": asdict(self._guardrail),
            "updated_at": _utc_iso(),
        }
        self._config_path.write_text(
            json.dumps(cfg_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._save_state()

    def _save_state(self) -> None:
        self._state_path.write_text(
            json.dumps(asdict(self._state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

