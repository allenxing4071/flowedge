# FlowEdge 数据质量 Skill

> **核心原则：数据是地基的地基。每一条进入特征计算的数据，都必须经过质量把关。**

## 触发词
用户说"数据源"、"数据质量"、"噪声"、"采集"、"新增 API"、"接入数据"时执行本 Skill。

## 必须遵守的 Rules
- **R0（变更控制）**：数据管道属于核心链路，修改前必须征得用户同意
- **R4（速率限制）**：所有 REST 请求必须走限速器
- **R8（经验沉淀）**：数据质量相关经验追加到本文件

---

## 一、数据质量三层检查

### 第一层：连通性（数据能不能拿到）
- WS 流是否持续连接且有数据
- REST 采集器是否在周期内完成
- API Key 是否有效（Coinglass/Coinalyze）
- 速率限制器是否被触发（检查 `/rate-limits`）

### 第二层：完整性（数据有没有缺失）
- 每个币种的每条 WS 流都应有数据
- REST 采集的每个字段都应有值（None/null 需要记录告警）
- 多币种之间数据不应有大幅度时间偏移

### 第三层：准确性（数据对不对）
- 价格/OI/费率等数值是否在合理范围
- 时间戳是否递增（不倒退）
- 跨数据源的同一指标是否一致（如币安 OI vs Coinglass OI）

---

## 二、数据源完整清单（9 源）

### 实时 WebSocket（6 条）

| # | 流 | 数据 | 检查点 |
|---|---|---|---|
| 1 | aggTrade | 逐笔成交 | 连续无数据 > 30s → 告警 |
| 2 | depth@100ms | 订单簿 20 档 | 深度为空 → 告警 |
| 3 | bookTicker | 最优买卖报价 | bid/ask 倒挂 → 异常 |
| 4 | markPrice@1s | 标记价+费率 | 费率突变 > 0.5% → 标记异常 |
| 5 | forceOrder | 全市场清算 | 低频流，无数据不一定异常 |
| 6 | kline@1m | 1分钟K线 | close 偏离 mark_price > 1% → 异常 |

### REST 中频采集（3 个）

| # | 采集器 | 数据源 | 周期 | 检查点 |
|---|---|---|---|---|
| 7 | BinanceREST | 币安 | 5min | OI 为 0 或负数 → 异常 |
| 8 | Coinglass | Coinglass API | 5min | 返回空数据 → 降级（不阻断） |
| 9 | External | Coinalyze + alternative.me | 5min | FearGreed 超出 0-100 → 异常 |

---

## 三、新数据源接入流程

### Step 1：必要性评估
```
□ 这个数据能为现有 11 个特征增加什么信息？
□ 是否与现有数据源高度重叠？
□ 更新频率是否匹配系统需求？
□ API 是否稳定可靠？限速是多少？
□ 是免费还是付费？成本是否值得？
```

### Step 2：技术接入
1. 在 `flowedge/feeds/` 下创建新的采集器
2. 在 `core/rate_limiter.py` 注册对应的限速器
3. 在 `config.py` 添加必要的配置项
4. 在 `api.py` 的 `lifespan` 中初始化和启动

### Step 3：特征对接
1. 在 `features/engine.py` 中添加数据分发逻辑
2. 更新相关特征计算器以使用新数据
3. 更新 `/features/snapshot` 的输出

### Step 4：验证
```bash
# 启动服务后检查
curl -s http://localhost:8005/status | python3 -m json.tool
# 确认新数据源出现在 status 中且 connected=true
```

---

## 四、速率限制器配置

| 限制器 | 对应 API | 速率 | 突发 | 说明 |
|---|---|---|---|---|
| `binance` | 币安 REST | 32/s | 100 | 2400 权重/分钟上限 |
| `coinglass` | Coinglass | 0.5/s | 10 | 30 次/分钟 |
| `coinalyze` | Coinalyze | 0.66/s | 10 | 40 次/分钟 |
| `external` | 免费 API | 1/s | 5 | 保守限速 |

### 多币种扩展时的注意事项
- N 个币种 = N 倍 REST 请求
- 10 个币种时，单轮币安 REST 采集约 70 次请求（7 接口 × 10 币种）
- 必须确保总请求量在限速器容量内
- 可通过增大采集间隔来适配更多币种

---

## 五、数据降级策略

当某个数据源不可用时，系统应优雅降级而非崩溃。

| 数据源 | 降级策略 |
|---|---|
| WS 流断连 | 自动重连，特征计算暂停直到数据恢复 |
| Coinglass 无 Key | 跳过 Coinglass 采集，相关特征使用 fallback |
| Coinalyze 无 Key | 跳过 Coinalyze 采集，情绪特征部分缺失 |
| alternative.me 超时 | 上次值缓存，标记为陈旧 |
| 币安 REST 限速 | 等待限速器令牌，不丢弃请求 |

---

## 经验沉淀区

### 经验：Coinglass API 无 Key 时需优雅降级
- 现象: 启动时未配置 COINGLASS_API_KEY，导致采集循环报错
- 解决: 检测 Key 为空时跳过 Coinglass 采集，日志提示，不影响其他数据源
- 关联: `flowedge/feeds/market_data.py`
- 日期: 2026-02

### 经验：Coinalyze API 返回格式与文档不一致
- 现象: 部分接口返回嵌套结构而非扁平数组
- 解决: 解析时增加容错处理，检查多层数据结构
- 关联: `flowedge/feeds/external.py`
- 日期: 2026-02

<!-- 按 R8 模板追加经验条目 -->
