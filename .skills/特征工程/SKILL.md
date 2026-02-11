# FlowEdge 特征工程 Skill

> **核心原则：特征是从原始数据到交易信号的桥梁，每个特征必须有明确的市场含义和学术依据。**

## 触发词
用户说"特征"、"指标"、"OFI"、"CVD"、"VPIN"、"计算"、"新增特征"、"优化特征"时执行本 Skill。

## 必须遵守的 Rules
- **R0（变更控制）**：特征计算器属于核心模块，修改前必须征得用户同意
- **R8（经验沉淀）**：特征相关经验追加到本文件
- **R12（数据驱动）**：新增特征必须有学术/行业依据

---

## 一、特征计算器全景（11 个）

### 实时微结构特征（数据源：WS）

| # | 特征 | 类 | 学术依据 | 市场含义 |
|---|---|---|---|---|
| 1 | **CVD** | `CVDCalculator` | Lee & Ready (1991) | 累积成交量增量，衡量买卖压力净值 |
| 2 | **OFI** | `OFICalculator` | Cont, Kukanov & Stoikov (2014) | 订单流失衡，订单簿变化的净压力 |
| 3 | **VPIN** | `VPINCalculator` | Easley, López de Prado & O'Hara (2012) | 知情交易概率，量化毒性订单流 |
| 4 | **大单检测** | `LargeTradeDetector` | 经验方法 | 大额成交（>动态阈值），机构/鲸鱼动向 |
| 5 | **深度变化** | `DepthChangeDetector` | 经验方法 | 订单簿大幅变动/假墙检测，做市商行为 |
| 6 | **Book Imbalance** | `BookImbalance` (engine内) | Cartea, Jaimungal & Penalva (2015) | L1 买卖盘失衡，短期价格方向预测 |

### 中低频衍生特征（数据源：WS + REST）

| # | 特征 | 类 | 数据源 | 市场含义 |
|---|---|---|---|---|
| 7 | **资金费率** | `FundingRateTracker` | markPrice WS | 多空成本差，杠杆拥挤度 |
| 8 | **清算级联** | `LiquidationTracker` | forceOrder WS + Coinglass | 大规模清算检测，瀑布/轧空风险 |
| 9 | **OI 变化** | `OITracker` | BinanceREST + Coinglass | 持仓量增减，资金进出 |
| 10 | **多空情绪** | `SentimentTracker` | BinanceREST + FearGreed + Coinalyze | 散户/大户/全市场情绪综合 |
| 11 | **趋势上下文** | `TrendTracker` | kline WS + BinanceREST K线 | 多周期趋势方向/强度/对齐度 |

---

## 二、RingBuffer 环形缓冲区

核心数据结构，所有时序计算的基础。

### 设计原理
- NumPy 数组固定大小（默认 1000），写满后覆盖最旧数据
- O(1) 写入、O(1) 求和/均值（利用 prefix sum trick）
- 自动将 `numpy.float64` 转为 Python `float`（避免 JSON 序列化问题）

### 关键方法
| 方法 | 说明 |
|---|---|
| `push(value)` | 追加一条数据 |
| `sum()` | 全缓冲区总和 |
| `mean()` | 全缓冲区均值 |
| `window_sum(n)` | 最近 n 条数据的和 |
| `window_mean(n)` | 最近 n 条数据的均值 |
| `count` | 当前有效数据条数 |

---

## 三、特征计算详解

### CVD（Cumulative Volume Delta）
```
CVD = Σ (taker_buy_volume - taker_sell_volume)
```
- 正值 → 买方主导
- 负值 → 卖方主导
- 5 分钟 CVD 变化率可用于短期方向判断

### OFI（Order Flow Imbalance）
```
OFI = Δ(bid_qty × I(bid↑)) - Δ(ask_qty × I(ask↓))
```
- 正值 → 买方挂单增加/卖方撤单
- 负值 → 卖方挂单增加/买方撤单
- 基于 Cont et al. (2014) 的定义

### VPIN（Volume-Synchronized PIN）
```
VPIN = |V_buy - V_sell| / V_total（按成交量桶切割）
```
- 值越高 → 知情交易概率越大 → 市场可能剧烈波动
- VPIN > 0.7 通常预示大幅波动

### Book Imbalance
```
imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
```
- 范围 [-1, +1]
- > 0.3 → 买方压力大
- < -0.3 → 卖方压力大

---

## 四、新增特征流程

### Step 1：学术/行业依据
```
□ 这个特征有论文/学术依据吗？
□ 行业中有团队在用这个特征吗？
□ 它衡量的是什么市场含义？
□ 它与现有 11 个特征的相关性如何？
```

### Step 2：实现
1. 在 `flowedge/features/` 下创建新的计算器文件
2. 遵循现有模式：`__init__` → `on_xxx` 回调 → `snapshot()` 输出
3. 使用 `RingBuffer` 存储时序数据

### Step 3：集成
1. 在 `features/engine.py` 中初始化新计算器
2. 在相应的 `on_xxx` 回调中调用新计算器
3. 在 `get_snapshot()` 中包含新特征输出

### Step 4：验证
```bash
curl -s "http://localhost:8005/features/snapshot?symbol=BTCUSDT" | python3 -m json.tool
# 确认新特征字段存在且值合理
```

---

## 五、特征输出格式（snapshot）

每个币种的特征快照包含以下字段：

```json
{
  "symbol": "BTCUSDT",
  "timestamp_ms": 1707654321000,
  "cvd": {"value": 1234.5, "5min_delta": 56.7, ...},
  "ofi": {"value": -89.2, "avg_1min": -45.1, ...},
  "vpin": {"value": 0.42, ...},
  "large_trades": {"recent_count": 3, "net_direction": "buy", ...},
  "depth_change": {"fake_walls_detected": 1, ...},
  "book_imbalance": {"value": 0.15, ...},
  "funding": {"rate": 0.0001, "trend": "rising", ...},
  "liquidation": {"1h_total_usd": 5000000, "cascade_level": "low", ...},
  "oi": {"binance_change_pct": 2.3, "global_change_pct": 1.8, ...},
  "sentiment": {"retail_bias": "bullish", "whale_bias": "bearish", ...},
  "trend": {"1m_direction": "up", "alignment_score": 0.6, ...}
}
```

---

## 经验沉淀区

### 经验：特征值必须是 Python 原生类型
- 现象: numpy.float64 导致 JSON 序列化失败
- 根因: FastAPI/orjson 不识别 numpy 数值类型
- 解决: RingBuffer 所有返回值显式 `float()` 转换
- 关联: `ring_buffer.py`, R1（验证规范）
- 日期: 2026-02

<!-- 按 R8 模板追加经验条目 -->
