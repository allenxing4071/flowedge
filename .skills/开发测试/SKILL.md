# FlowEdge 开发与测试 Skill

> **核心原则：修改功能或修复 Bug 后必须按 R1 两段式验证；测试是回归保障的第一道防线。**

## 触发词
用户说"开发"、"测试"、"pytest"、"单元测试"、"API 测试"、"写测试"、"验证"时执行本 Skill。

## 必须遵守的 Rules
- **R1（功能验证）**：阶段 A 接口快验 + 阶段 B 完整验证
- **R0（变更控制）**：改核心模块前征得用户同意
- **R8（经验沉淀）**：测试相关经验追加到本文件或对应 Skill

---

## 一、测试结构

```
tests/
├── conftest.py         # pytest fixture（TestClient、mock 配置）
├── test_features.py    # 特征计算单元测试（RingBuffer/CVD/OFI/VPIN/LargeTrade）
├── test_api.py         # API 端点测试（/health、/status 等）
└── test_rate_limiter.py # 速率限制器单元测试
```

### 已覆盖模块
| 模块 | 文件 | 覆盖内容 |
|---|---|---|
| RingBuffer | test_features.py | 空缓冲、溢出、window_sum、recent_values、clear |
| CVD | test_features.py | 纯买/纯卖/平衡、trade_count |
| OFI | test_features.py | 首次无 OFI、bid/ask 增加 |
| VPIN | test_features.py | 空桶、纯买/平衡、桶填充 |
| LargeTrade | test_features.py | 小单忽略、大单检测、窗口统计 |
| RateLimiter | test_rate_limiter.py | 令牌补充、acquire 行为 |
| API | test_api.py | /health、/status 结构校验 |

### 待补充（按需）
- depth_change、funding、liquidation、oi_tracker、sentiment、trend 等特征
- signals/engine 信号引擎
- optimizer 模块（依赖外部数据，可用 mock）

---

## 二、运行测试

```bash
cd ~/Documents/soft/FlowEdge

# 运行全部测试
python3 -m pytest tests/ -v

# 运行指定文件
python3 -m pytest tests/test_features.py -v

# 运行指定类/方法
python3 -m pytest tests/test_features.py::TestRingBuffer::test_overflow_ring -v

# 带覆盖率（需 pytest-cov）
python3 -m pytest tests/ -v --cov=flowedge --cov-report=term-missing
```

---

## 三、新增测试流程

### Step 1：确定测试层级
- **单元测试**：纯函数/计算器逻辑，无 IO，用 mock 数据
- **集成测试**：API 端点，用 TestClient 不启动真实 WS/REST

### Step 2：编写用例
1. 遵循 `test_xxx` 或 `TestXxx` 命名
2. 每个用例只测一个行为
3. 断言要明确（值/类型/结构）

### Step 3：验证
```bash
python3 -m pytest tests/ -v
# 确认新增用例通过
```

---

## 四、R1 两段式验证（功能验证时）

### 阶段 A：接口快验
```bash
curl -s http://localhost:8005/health | python3 -m json.tool
curl -s http://localhost:8005/status | python3 -m json.tool
curl -s "http://localhost:8005/features/snapshot?symbol=BTCUSDT" | python3 -m json.tool
```

### 阶段 B：完整验证
- 涉及前端：浏览器打开页面 + 截图
- 涉及数据流：确认 WS 连接、REST 采集、特征输出结构正确

---

## 五、依赖

测试依赖已包含在 `requirements.txt`：
- `pytest`
- `httpx`（TestClient 用于 FastAPI）

可选：`pytest-cov`（覆盖率）、`pytest-asyncio`（异步测试）

---

## 六、环境冲突排查

若运行 `pytest` 时出现 `ImportError: cannot import name 'ContractName' from 'eth_typing'`，多为**全局环境中 web3 的 pytest 插件**与 FlowEdge 无关依赖冲突。

**处理方式**：
- **推荐**：使用项目 venv 运行 `pip install -r requirements.txt && pytest tests/ -v`（可避免全局 web3 等插件冲突）
- 或 `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && pytest tests/ -v`

---

## 经验沉淀区

### 经验：测试中避免真实 API 调用
- 现象: 单元测试依赖币安 WS/Coinglass 导致不稳定
- 解决: API 测试用 TestClient 且不启动 lifespan 中的 WS/REST；或用 mock 替代
- 关联: test_api.py, R1
- 日期: 2026-02

### 经验：numpy 类型断言
- 现象: `assert x == 0.3` 在 numpy.float64 时可能失败
- 解决: 使用 `pytest.approx(0.3, abs=0.01)` 或先转 `float(x)`
- 关联: test_features.py
- 日期: 2026-02

<!-- 按 R8 模板追加经验条目 -->
