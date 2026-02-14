# FlowEdge 项目入门

## 触发词
用户说"项目结构"、"怎么跑"、"入门"时执行本 Skill。

## 必须遵守的 Rules
- 修改任何核心模块前先阅读本 Skill 了解架构（R0 要求）
- 新经验写入本文件"经验沉淀"区（R8 要求）

## 核心定位

FlowEdge 是一个**订单流驱动的量化特征引擎**，专注于从币安 USDⓈ-M 永续合约的市场微结构数据中实时计算量化特征。它是交易系统的"数据地基层"，不直接交易，而是为上层交易策略提供高质量的实时特征输入。

### 与 KKline 的关系
- **KKline** = 交易引擎（AI 分析 + 风控 + 下单）
- **FlowEdge** = 数据引擎（市场微结构 + 特征计算 + SSE 推送）
- 未来：KKline 可订阅 FlowEdge 的 SSE 特征流，替代内置的简单数据采集

## 项目结构

```
FlowEdge/
├── flowedge/                # 核心代码
│   ├── api.py               # FastAPI 服务层（REST + SSE + 生命周期管理）
│   ├── config.py             # 全局配置（.env → dataclass）
│   ├── main.py               # 入口点（uvicorn 启动）
│   ├── __init__.py
│   ├── __main__.py           # python3 -m flowedge 入口
│   ├── core/                 # 核心基础设施
│   │   └── rate_limiter.py   # Token Bucket 限速器 + Registry
│   ├── feeds/                # 数据源层（9 个采集器）
│   │   ├── agg_trade.py      # aggTrade WS — 逐笔成交
│   │   ├── depth.py          # depth@100ms WS — 订单簿增量
│   │   ├── book_ticker.py    # bookTicker WS — 最优报价
│   │   ├── mark_price.py     # markPrice@1s WS — 标记价/资金费率
│   │   ├── force_order.py    # forceOrder WS — 全市场清算
│   │   ├── kline.py          # kline@1m WS — 实时K线
│   │   ├── binance_rest.py   # 币安 REST — OI/多空比/大户/K线/24h
│   │   ├── market_data.py    # Coinglass — 全网OI/清算
│   │   └── external.py       # 外部 — 恐慌贪婪/Coinalyze/ETF
│   └── features/             # 特征计算层（11 个计算器）
│       ├── engine.py          # 特征引擎聚合（数据分发 + 快照 + SSE 广播）
│       ├── ring_buffer.py     # NumPy 环形缓冲区
│       ├── cvd.py             # 累积成交量增量
│       ├── ofi.py             # 订单流失衡
│       ├── vpin.py            # 知情交易概率
│       ├── large_trade.py     # 大单检测
│       ├── depth_change.py    # 深度变化 / 假墙检测
│       ├── funding.py         # 资金费率追踪
│       ├── liquidation.py     # 清算级联检测
│       ├── oi_tracker.py      # 持仓量变化
│       ├── sentiment.py       # 多空情绪综合
│       └── trend.py           # 趋势上下文（多周期）
├── tests/
│   ├── conftest.py            # pytest fixture
│   ├── test_features.py      # 特征计算单元测试
│   ├── test_api.py           # API 端点测试
│   └── test_rate_limiter.py  # 速率限制器测试
├── docs/
│   └── architecture.md        # 架构文档
├── scripts/
│   └── deploy-flowedge.sh     # 部署脚本
├── deploy/                    # 部署配置
├── .cursor/rules/             # 开发规则
├── .skills/                   # AI 操作指引
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env                       # API 密钥（不入库）
├── .env.example               # 配置模板
└── .gitignore
```

## 快速上手

### 本地直接运行
```bash
cd ~/Documents/soft/FlowEdge
cp .env.example .env  # 编辑填入 API Key
pip3 install -r requirements.txt
python3 -m flowedge
# 验证：curl http://localhost:8005/health
```

### Docker 运行
```bash
cd ~/Documents/soft/FlowEdge
docker compose up -d --build
# 验证：curl http://localhost:8005/health
```

### 部署到服务器
```bash
./scripts/deploy-flowedge.sh c  # 一键部署
```

## 数据架构：三层九源

### 实时层（6 条 WebSocket 流）
| 流 | 频率 | 数据 |
|---|---|---|
| aggTrade | 每笔 | 成交价/量/方向 |
| depth@100ms | 100ms | 订单簿 20 档增量 |
| bookTicker | 实时 | 最优买卖报价 |
| markPrice@1s | 1s | 标记价/费率/基差 |
| forceOrder | 事件 | 全市场清算通知 |
| kline@1m | 1min | OHLCV + 主动买入量 |

### 中频层（3 个 REST 采集器）
| 采集器 | 周期 | 数据源 |
|---|---|---|
| BinanceREST | 5min | OI, 多空比(3种), 费率历史, K线5档, 24h |
| Coinglass | 5min | 全网OI, 分交易所OI, 清算, ETF流向 |
| External | 5min | 恐慌贪婪, Coinalyze(聚合OI/费率/爆仓/多空比) |

### 特征层（11 个计算器）
| # | 特征 | 类型 | 输入 |
|---|---|---|---|
| 1 | CVD | 趋势 | aggTrade |
| 2 | OFI | 流动性 | depth |
| 3 | VPIN | 风险 | aggTrade |
| 4 | LargeTradeDetector | 事件 | aggTrade |
| 5 | DepthChangeDetector | 结构 | depth |
| 6 | BookImbalance | 实时 | bookTicker |
| 7 | FundingRateTracker | 成本 | markPrice |
| 8 | LiquidationTracker | 事件 | forceOrder + Coinglass |
| 9 | OITracker | 仓位 | BinanceREST + Coinglass |
| 10 | SentimentTracker | 情绪 | BinanceREST + FearGreed + Coinalyze |
| 11 | TrendTracker | 宏观 | kline WS + BinanceREST K线 |

## API 端点

| 端点 | 说明 |
|---|---|
| `GET /health` | 健康检查 |
| `GET /status` | 系统状态（WS连接/REST采集/特征计算/币种数） |
| `GET /features/snapshot` | 全币种特征快照（可选 ?symbol=BTCUSDT） |
| `GET /features/stream` | SSE 实时特征推送（可选 ?symbol=BTCUSDT） |
| `GET /rate-limits` | 速率限制器状态 |
| `GET /docs` | Swagger API 文档 |

## 核心模块速查

| 模块 | 一句话 |
|---|---|
| `api.py` | 服务层：生命周期管理 + REST + SSE + 数据同步循环 |
| `config.py` | 配置：.env → FlowEdgeConfig dataclass |
| `core/rate_limiter.py` | 速率限制：Token Bucket + Registry（4 个限制器） |
| `feeds/*.py` | 数据源：9 个采集器（6 WS + 3 REST） |
| `features/engine.py` | 聚合层：数据分发 → 11 计算器 → 快照 → SSE 广播 |
| `features/ring_buffer.py` | 基础设施：NumPy 环形缓冲区（O(1) 滚动计算） |

## 多币种设计

- 配置：`.env` 的 `WATCH_SYMBOLS=BTCUSDT,ETHUSDT,...`
- WS 流：每个流合并所有币种的订阅（单连接）
- REST 采集：循环遍历每个币种，受速率限制器保护
- 特征引擎：为每个币种独立初始化 11 个计算器
- API：`/features/snapshot` 返回所有币种，可用 `?symbol=` 过滤

## Skills 路由

| 用户意图 | 执行 Skill |
|---|---|
| "项目入门" | 本文件 |
| "部署/上线" | `.skills/部署运维/SKILL.md` |
| "数据源/数据质量" | `.skills/数据质量/SKILL.md` |
| "特征/指标/算法" | `.skills/特征工程/SKILL.md` |
| "开发/测试/pytest/单元测试" | `.skills/开发测试/SKILL.md` |

## 经验沉淀区

### 经验：Python 3.9 不支持 dataclass(slots=True)
- 现象: `TypeError: dataclass() got an unexpected keyword argument 'slots'`
- 根因: `slots=True` 是 Python 3.10+ 特性
- 解决: 移除所有 dataclass 的 `(slots=True)` 参数
- 关联: `flowedge/feeds/*.py`, `flowedge/features/*.py`
- 日期: 2026-02

### 经验：Python 3.9 不支持 `str | None` 类型联合
- 现象: FastAPI 路由参数使用 `str | None` 报 TypeError
- 解决: 使用 `Optional[str]` 替代
- 关联: `flowedge/api.py`
- 日期: 2026-02

### 经验：numpy.float64 不可 JSON 序列化
- 现象: `/features/snapshot` 返回 `TypeError: Type is not JSON serializable: numpy.float64`
- 解决: `ring_buffer.py` 的 `sum()/mean()/window_sum()` 显式转为 Python `float`
- 关联: `flowedge/features/ring_buffer.py`
- 日期: 2026-02

### 经验：端口 8000 被占用需改用 8005
- 现象: `[Errno 48] address already in use` 端口 8000 被 KKline 或其他服务占用
- 解决: `main.py` 改为从环境变量 `PORT` 读取，默认 8005
- 关联: `flowedge/main.py`, `.env`
- 日期: 2026-02

<!-- 按 R8 模板追加经验条目 -->
