# FlowEdge 系统架构文档

> 本文档描述 FlowEdge 系统"是什么、怎么构建的"。操作指引见 `.skills/`，开发规则见 `.cursor/rules/`。

## 1. 项目定位

FlowEdge 是一个**订单流驱动的量化特征引擎**，专注从币安 USDⓈ-M 永续合约的市场微结构数据中实时计算量化特征。核心特点：

- **9 路数据源**：6 条 WebSocket 实时流 + 3 个 REST 中频采集器
- **11 个特征计算器**：涵盖订单流、流动性、波动性、情绪、趋势等维度
- **多币种原生支持**：设计可扩展至数十个交易对
- **Token Bucket 限速**：保护所有外部 API 调用
- **SSE 实时推送**：特征快照毫秒级推送到下游系统
- **NumPy RingBuffer**：高性能环形缓冲区，O(1) 滚动计算

### 与 KKline 的关系

| 系统 | 定位 | 核心功能 |
|---|---|---|
| **FlowEdge** | 数据引擎（地基层） | 市场微结构 → 量化特征 → SSE 推送 |
| **KKline** | 交易引擎（上层建筑） | AI 分析 → 风控 → 下单 → 复盘 |

未来架构：KKline 订阅 FlowEdge 的 SSE 特征流，替代内置的简单数据采集。

---

## 2. 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│  FlowEdge 特征引擎（Docker 单容器 / 本地直运行）                  │
│                                                                  │
│  ┌─ 实时数据层（6 条 WebSocket）────────────────────────────┐    │
│  │                                                          │    │
│  │  aggTrade     → 逐笔成交（每笔）                          │    │
│  │  depth@100ms  → 订单簿 20 档增量（100ms）                  │    │
│  │  bookTicker   → 最优买卖报价（实时）                       │    │
│  │  markPrice@1s → 标记价格 + 资金费率（1秒）                 │    │
│  │  forceOrder   → 全市场清算事件（事件驱动）                 │    │
│  │  kline@1m     → 1分钟 K 线 OHLCV（1分钟）                 │    │
│  │                                                          │    │
│  │  单连接合并所有币种订阅                                    │    │
│  └──────────────────────────────────────────────────────────┘    │
│                          ↓ 回调分发                              │
│  ┌─ 中频数据层（3 个 REST 采集器，5 分钟周期）──────────────┐    │
│  │                                                          │    │
│  │  BinanceREST   → OI / 多空比(3种) / 费率历史              │    │
│  │                   K线(1m/5m/15m/1h/4h) / 24h 统计         │    │
│  │  Coinglass     → 全网OI / 分交易所OI / 清算 / ETF流向     │    │
│  │  External      → 恐慌贪婪 / Coinalyze / ETF              │    │
│  │                                                          │    │
│  │  所有请求经过 TokenBucketLimiter 保护                      │    │
│  └──────────────────────────────────────────────────────────┘    │
│                          ↓ 数据同步                              │
│  ┌─ 特征计算层（11 个计算器 × N 币种）─────────────────────┐    │
│  │                                                          │    │
│  │  FeatureEngine（聚合层）                                  │    │
│  │    ├── CVDCalculator        — 累积成交量增量               │    │
│  │    ├── OFICalculator        — 订单流失衡                   │    │
│  │    ├── VPINCalculator       — 知情交易概率                 │    │
│  │    ├── LargeTradeDetector   — 大单检测                     │    │
│  │    ├── DepthChangeDetector  — 深度变化/假墙                │    │
│  │    ├── BookImbalance        — L1 买卖盘失衡（engine 内）   │    │
│  │    ├── FundingRateTracker   — 资金费率追踪                 │    │
│  │    ├── LiquidationTracker   — 清算级联检测                 │    │
│  │    ├── OITracker            — 持仓量变化                   │    │
│  │    ├── SentimentTracker     — 多空情绪综合                 │    │
│  │    └── TrendTracker         — 趋势上下文（多周期）         │    │
│  │                                                          │    │
│  │  每个币种独立一套计算器实例                                │    │
│  └──────────────────────────────────────────────────────────┘    │
│                          ↓                                       │
│  ┌─ API 服务层（FastAPI）───────────────────────────────────┐    │
│  │                                                          │    │
│  │  GET /health            — 健康检查                        │    │
│  │  GET /status            — 系统状态                        │    │
│  │  GET /features/snapshot — 全币种特征快照                   │    │
│  │  GET /features/stream   — SSE 实时推送                    │    │
│  │  GET /rate-limits       — 速率限制器状态                   │    │
│  │  GET /docs              — Swagger 文档                    │    │
│  │                                                          │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 关键设计决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 单容器运行 | WS + REST + 特征 + API 同进程 | 内存数据共享，避免 IPC 开销 |
| NumPy RingBuffer | 固定大小环形缓冲 | O(1) 写入和滚动计算，内存可控 |
| Token Bucket 限速 | 异步令牌桶 | 多币种扩展时保护 API 限额 |
| WS 单连接合并 | 所有币种共用一个 WS 连接 | 减少连接数，币安有并发限制 |
| orjson 序列化 | 替代标准 json | 性能提升 3-5x |
| dataclass 无 slots | 兼容 Python 3.9 | 服务器环境可能不是 3.10+ |
| 端口 8005 | 避开 8000/8003 | KKline 占用 8003，避免冲突 |

---

## 3. 项目结构

```
FlowEdge/
├── flowedge/                  # 核心代码
│   ├── api.py                 # FastAPI 服务层（REST + SSE + 生命周期管理）
│   ├── config.py              # 配置管理（.env → FlowEdgeConfig）
│   ├── main.py                # 入口点（uvicorn）
│   ├── __init__.py
│   ├── __main__.py            # python3 -m flowedge 入口
│   ├── core/
│   │   └── rate_limiter.py    # Token Bucket 限速器 + Registry（4 个）
│   ├── feeds/                 # 数据源层（9 个采集器）
│   │   ├── agg_trade.py       # aggTrade WS
│   │   ├── depth.py           # depth@100ms WS
│   │   ├── book_ticker.py     # bookTicker WS
│   │   ├── mark_price.py      # markPrice@1s WS
│   │   ├── force_order.py     # forceOrder WS
│   │   ├── kline.py           # kline@1m WS
│   │   ├── binance_rest.py    # 币安 REST 全量采集器
│   │   ├── market_data.py     # Coinglass 中频数据
│   │   └── external.py        # 外部数据（恐慌贪婪/Coinalyze/ETF）
│   └── features/              # 特征计算层（11 个计算器）
│       ├── engine.py           # 聚合层：数据分发 → 特征计算 → 快照 → SSE
│       ├── ring_buffer.py      # NumPy 环形缓冲区
│       ├── cvd.py              # 累积成交量增量
│       ├── ofi.py              # 订单流失衡
│       ├── vpin.py             # 知情交易概率（VPIN）
│       ├── large_trade.py      # 大单检测
│       ├── depth_change.py     # 深度变化 / 假墙检测
│       ├── funding.py          # 资金费率追踪
│       ├── liquidation.py      # 清算级联检测
│       ├── oi_tracker.py       # 持仓量变化
│       ├── sentiment.py        # 多空情绪综合
│       └── trend.py            # 趋势上下文（多周期）
├── tests/
│   └── test_features.py
├── docs/
│   └── architecture.md        # 本文件
├── scripts/
│   └── deploy-flowedge.sh     # 部署脚本
├── .cursor/rules/
│   └── flowedge-dev-rules.mdc # 开发规则
├── .skills/                   # AI 操作指引
│   ├── 项目入门/SKILL.md
│   ├── 部署运维/SKILL.md
│   ├── 数据质量/SKILL.md
│   ├── 特征工程/SKILL.md
│   └── 经验沉淀/SKILL.md
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env                       # API 密钥（不入库）
├── .env.example
└── .gitignore
```

---

## 4. 数据流

```
┌─ 实时层（WebSocket，毫秒级）────────────────────────────────┐
│                                                              │
│  wss://fstream.binance.com/stream?streams=                   │
│    btcusdt@aggTrade / ethusdt@aggTrade                       │
│    btcusdt@depth@100ms / ethusdt@depth@100ms                 │
│    btcusdt@bookTicker / ethusdt@bookTicker                   │
│    btcusdt@markPrice@1s / ethusdt@markPrice@1s               │
│    !forceOrder                                               │
│    btcusdt@kline_1m / ethusdt@kline_1m                       │
│          ↓ JSON 消息                                         │
│  各 Stream 类解析 → 回调 FeatureEngine.on_xxx()              │
│          ↓                                                   │
│  11 个特征计算器实时更新                                      │
│                                                              │
├─ 中频层（REST，5 分钟周期）──────────────────────────────────┤
│                                                              │
│  BinanceREST → OI + 多空比(3种) + 费率历史 + K线(5档) + 24h │
│  Coinglass   → 全网OI + 分交易所OI + 清算 + ETF流向          │
│  External    → 恐慌贪婪 + Coinalyze + ETF                    │
│          ↓ _data_sync_loop（每 10 秒检查是否有新数据）        │
│  engine.update_binance_rest() / update_coinglass_data()      │
│  engine.update_external()                                    │
│          ↓                                                   │
│  OITracker / SentimentTracker / TrendTracker / etc. 更新     │
│                                                              │
├─ 输出层──────────────────────────────────────────────────────┤
│                                                              │
│  GET /features/snapshot → JSON 全币种特征快照                 │
│  GET /features/stream   → SSE 每秒推送特征快照               │
│                                                              │
│  下游系统（KKline 等）可订阅 SSE 获取实时特征                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. API 端点

| 端点 | 说明 | 返回 |
|---|---|---|
| `GET /health` | 健康检查 | `{"status": "ok", "timestamp": ...}` |
| `GET /status` | 系统状态 | WS 连接数/REST 状态/特征数/币种数 |
| `GET /features/snapshot` | 特征快照 | 所有币种的 11 维特征值 |
| `GET /features/snapshot?symbol=BTCUSDT` | 单币种快照 | 指定币种的特征值 |
| `GET /features/stream` | SSE 推送 | 每秒推送特征快照 |
| `GET /features/stream?symbol=BTCUSDT` | 单币种 SSE | 指定币种的特征推送 |
| `GET /rate-limits` | 速率限制器状态 | 4 个限制器的令牌/队列信息 |
| `GET /docs` | Swagger UI | API 交互文档 |

---

## 6. 部署架构

### 6.1 生产环境（47.254.246.53）

```
┌─ 阿里云服务器 47.254.246.53 ───────────────────────────────┐
│                                                              │
│  Nginx（系统级 / TradeDesk Docker）    ← 端口 80/443         │
│    ├── fe.kline007.top → 127.0.0.1:8005 (FlowEdge)         │
│    ├── kk.kline007.top → 127.0.0.1:8003 (KKline)           │
│    └── *.kline007.top  → 其他站点                            │
│                                                              │
│  flowedge-backend (Docker)            ← 端口 8005:8000      │
│    ├── uvicorn api:app               (FastAPI)               │
│    ├── 6 条 WebSocket 流             (后台 asyncio task)      │
│    ├── 3 个 REST 采集器              (后台 asyncio task)      │
│    ├── FeatureEngine                 (特征计算)              │
│    └── _data_sync_loop               (数据同步)              │
│                                                              │
│  数据卷                                                      │
│    ./data → /app/data               (持久化，预留)           │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 Docker Compose

```yaml
services:
  backend:
    build: .
    container_name: flowedge-backend
    ports:
      - "8005:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

### 6.3 部署命令

```bash
# 使用部署脚本（推荐）
cd ~/Documents/soft/FlowEdge
./scripts/deploy-flowedge.sh c       # 一键部署

# 手动部署
rsync -avz --delete \
    -e "ssh -i ~/Documents/soft/KKline/deploy/LH.pem -p 2222" \
    --exclude '.git' --exclude '.env' --exclude 'data/' \
    --exclude '__pycache__' --exclude '.cursor' --exclude '.skills' \
    ~/Documents/soft/FlowEdge/ root@47.254.246.53:/opt/FlowEdge/

ssh -i LH.pem -p 2222 root@47.254.246.53
cd /opt/FlowEdge && docker compose up -d --build
```

---

## 7. 开发与测试

### 7.1 测试结构

```
tests/
├── conftest.py         # pytest fixture（TestClient、测试用环境变量）
├── test_features.py    # 特征计算单元测试（RingBuffer/CVD/OFI/VPIN/LargeTrade）
├── test_api.py         # API 端点测试（/health、/status 等）
└── test_rate_limiter.py # 速率限制器单元测试
```

### 7.2 运行测试

```bash
cd ~/Documents/soft/FlowEdge
python3 -m pytest tests/ -v
```

### 7.3 测试策略

- **单元测试**：特征计算器、RingBuffer、RateLimiter 等纯逻辑，无 IO
- **API 测试**：使用 `httpx.TestClient`，测试环境会触发「演示模式」（不启动 WS/REST）
- **功能验证**：按 R1 执行阶段 A（接口快验）+ 阶段 B（页面/完整验证）

### 7.4 相关 Skill

| 用户意图 | 执行 Skill |
|---|---|
| 开发/测试/pytest/写测试 | `.skills/开发测试/SKILL.md` |

---

## 8. 速率限制体系

### Token Bucket 算法

```python
class TokenBucketLimiter:
    rate: float       # 令牌生成速率（个/秒）
    burst: int        # 桶容量（最大突发）
    tokens: float     # 当前令牌数
    
    async def acquire():
        # 等待直到有令牌可用
```

### 限制器注册表

| 名称 | API | 速率 | 突发 |
|---|---|---|---|
| `binance` | 币安 REST | 32/s | 100 |
| `coinglass` | Coinglass | 0.5/s | 10 |
| `coinalyze` | Coinalyze | 0.66/s | 10 |
| `external` | 免费 API | 1/s | 5 |

### 多币种扩展公式

```
单轮请求数 = 币种数 × 每币种接口数
币安: N × 7 接口 = 7N 次/5分钟
Coinglass: N × 3 接口 = 3N 次/5分钟
```

10 个币种时：70 次/5分钟（币安）= 0.23 次/秒，远低于 32/s 限制。

---

## 9. 配置参数

### .env 配置项

| 参数 | 默认值 | 说明 |
|---|---|---|
| `WATCH_SYMBOLS` | `BTCUSDT,ETHUSDT` | 监控币种（逗号分隔） |
| `REST_INTERVAL` | `300` | REST 采集间隔（秒） |
| `BROADCAST_INTERVAL` | `1.0` | SSE 广播间隔（秒） |
| `COINGLASS_API_KEY` | — | Coinglass API 密钥 |
| `COINALYZE_API_KEY` | — | Coinalyze API 密钥 |
| `PORT` | `8005` | 服务端口 |

---

## 10. 经验沉淀

### Python 兼容性
- `dataclass(slots=True)` 仅 3.10+，已全部移除
- `str | None` 语法仅 3.10+，改用 `Optional[str]`
- numpy.float64 需显式转为 Python float 才能 JSON 序列化

### 端口管理
- KKline: 8003
- FlowEdge: 8005
- TradeDesk Backend: 8001
- TradeDesk Frontend: 3001

### 币安 WebSocket
- 单连接最多 1024 个流
- 合并所有币种的所有流类型为一个 URL
- 24h 断连一次（币安强制），需要重连逻辑

---

## 11. 核心文件索引

| 文件 | 用途 | 关联 Rules | 关联 Skill |
|---|---|---|---|
| `flowedge/api.py` | 服务层 + 生命周期 | R0 R6 | 项目入门 |
| `flowedge/config.py` | 配置管理 | R0 R5 | — |
| `flowedge/core/rate_limiter.py` | 速率限制 | R0 R4 | 数据质量 |
| `flowedge/feeds/agg_trade.py` | aggTrade WS | R0 | 数据质量 |
| `flowedge/feeds/depth.py` | depth WS | R0 | 数据质量 |
| `flowedge/feeds/book_ticker.py` | bookTicker WS | R0 | 数据质量 |
| `flowedge/feeds/mark_price.py` | markPrice WS | R0 | 数据质量 |
| `flowedge/feeds/force_order.py` | forceOrder WS | R0 | 数据质量 |
| `flowedge/feeds/kline.py` | kline WS | R0 | 数据质量 |
| `flowedge/feeds/binance_rest.py` | 币安 REST | R0 R4 | 数据质量 |
| `flowedge/feeds/market_data.py` | Coinglass REST | R0 R4 | 数据质量 |
| `flowedge/feeds/external.py` | 外部数据 REST | R0 R4 | 数据质量 |
| `flowedge/features/engine.py` | 特征引擎聚合 | R0 | 特征工程 |
| `flowedge/features/ring_buffer.py` | 环形缓冲区 | — | 特征工程 |
| `flowedge/features/cvd.py` | CVD 计算 | R0 | 特征工程 |
| `flowedge/features/ofi.py` | OFI 计算 | R0 | 特征工程 |
| `flowedge/features/vpin.py` | VPIN 计算 | R0 | 特征工程 |
| `flowedge/features/large_trade.py` | 大单检测 | R0 | 特征工程 |
| `flowedge/features/depth_change.py` | 深度变化检测 | R0 | 特征工程 |
| `flowedge/features/funding.py` | 资金费率 | R0 | 特征工程 |
| `flowedge/features/liquidation.py` | 清算追踪 | R0 | 特征工程 |
| `flowedge/features/oi_tracker.py` | OI 追踪 | R0 | 特征工程 |
| `flowedge/features/sentiment.py` | 情绪综合 | R0 | 特征工程 |
| `flowedge/features/trend.py` | 趋势上下文 | R0 | 特征工程 |
| `scripts/deploy-flowedge.sh` | 部署脚本 | R6 | 部署运维 |
| `tests/*.py` | 单元/API 测试 | R1 | 开发测试 |
