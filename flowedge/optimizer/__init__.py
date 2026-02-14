"""
FlowEdge 自动优化系统 — 参数管理、回测、优化、验证、进化、自适应。

核心模块：
  - param_registry:   参数注册中心（所有可优化参数的唯一数据源）
  - data_manager:     数据管理器（训练/验证/测试集分割）
  - metrics:          绩效指标计算（Sharpe/MaxDD/IC 等）
  - backtester:       信号级回测引擎
  - optimizer:        Optuna 优化器封装（单目标/多目标/Walk-Forward）
  - validator:        三层验证引擎（OOS/稳定性/Bootstrap）
  - scheduler:        自动调度器（周期性优化+验证+应用）
  - ab_test:          A/B 对照测试（多参数组统计检验）
  - ai_evaluator:     AI 评估器（优化结果解读+过拟合风险+建议）
  - agent_controller: 总控 Agent（模型 API 接入 + 计划执行）
  - regime_adapter:   市场环境自适应参数切换
  - evolution:        持续进化循环引擎（数据→优化→验证→应用→监控）
"""
