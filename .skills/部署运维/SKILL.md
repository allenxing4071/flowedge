# FlowEdge 部署运维 Skill

## 触发词
用户说"部署"、"上线"、"重启"、"同步代码"、"查看状态"、"查看日志"时执行本 Skill。

## 必须遵守的 Rules
- **R6（部署规范）**：未经用户明确授权，禁止执行任何部署操作
- **R0（变更控制）**：禁止在容器内直接修改代码
- **R10（Git Push）**：禁止自动 push
- **R5（安全）**：禁止泄露 SSH 密钥路径和 API 密钥

## 部署架构

```
Mac（开发）
  │
  └── FlowEdge 后端 ──→ rsync ──→ 阿里云服务器（47.254.246.53）
                                   ├── flowedge-backend（Docker，端口 8005:8000）
                                   └── Nginx 反代：fe.kline007.top → :8005
```

## 部署脚本（推荐）

```bash
cd ~/Documents/soft/FlowEdge

# ——— 云服务器常用命令 ———
./scripts/deploy-flowedge.sh c       # 一键部署（rsync + Docker 重建）
./scripts/deploy-flowedge.sh cr      # 仅重建容器（不传代码）
./scripts/deploy-flowedge.sh cx      # 快速重启（不重建镜像）
./scripts/deploy-flowedge.sh cs      # 查看云服务器状态
./scripts/deploy-flowedge.sh cl      # 查看云服务器日志
./scripts/deploy-flowedge.sh sync    # 仅同步代码（不重建）
./scripts/deploy-flowedge.sh i       # 首次初始化

# ——— 本地命令 ———
./scripts/deploy-flowedge.sh start   # 本地 Docker 启动
./scripts/deploy-flowedge.sh stop    # 本地 Docker 停止
./scripts/deploy-flowedge.sh status  # 查看本地状态
./scripts/deploy-flowedge.sh logs    # 查看本地日志

# ——— 本地直接运行 ———
python3 -m flowedge                  # 启动服务（端口 8005）
```

## 手动部署步骤

### 第一步：同步代码到服务器
```bash
rsync -avz --delete \
    -e "ssh -i ~/Documents/soft/KKline/deploy/LH.pem -p 2222" \
    --exclude '.git' --exclude '.env' --exclude 'data/' \
    --exclude '__pycache__' --exclude '*.pyc' \
    --exclude '.cursor' --exclude '.skills' --exclude 'tests/' \
    ~/Documents/soft/FlowEdge/ root@47.254.246.53:/opt/FlowEdge/
```

### 第二步：服务器上重建容器
```bash
ssh -i ~/Documents/soft/KKline/deploy/LH.pem -p 2222 root@47.254.246.53
cd /opt/FlowEdge && docker compose up -d --build
```

### 第三步：验证
```bash
curl https://fe.kline007.top/health
curl https://fe.kline007.top/status
```

## 环境信息速查

### 服务器
- IP: `47.254.246.53`（复用 KKline 服务器）
- SSH 端口: `2222`
- SSH 密钥: `~/Documents/soft/KKline/deploy/LH.pem`
- 后端路径: `/opt/FlowEdge`

### Docker 容器

| 容器 | 端口 | 说明 |
|---|---|---|
| `flowedge-backend` | `8005:8000` | 特征引擎（WS + REST + 特征计算） |

### 访问地址
- 健康检查: http://localhost:8005/health（本地）
- 系统状态: http://localhost:8005/status
- API 文档: http://localhost:8005/docs
- 特征快照: http://localhost:8005/features/snapshot
- SSE 推送: http://localhost:8005/features/stream

## 常见运维操作

### 查看日志
```bash
# 本地直接运行时：终端直接看输出
# Docker 运行时：
docker compose logs -f backend

# 远程查看
ssh -i LH.pem -p 2222 root@47.254.246.53 \
    "cd /opt/FlowEdge && docker compose logs --tail=100 backend"
```

### 重启服务
```bash
# 本地
docker compose restart backend

# 远程
ssh -i LH.pem -p 2222 root@47.254.246.53 \
    "cd /opt/FlowEdge && docker compose restart backend"
```

### 检查 API 数据
```bash
# 系统状态
curl -s http://localhost:8005/status | python3 -m json.tool

# 特征快照（指定币种）
curl -s "http://localhost:8005/features/snapshot?symbol=BTCUSDT" | python3 -m json.tool

# 速率限制器状态
curl -s http://localhost:8005/rate-limits | python3 -m json.tool
```

## 常见问题排查

### 服务启动后无数据
1. 检查 `.env` 中 `WATCH_SYMBOLS` 是否配置
2. 检查日志是否有 WS 连接错误
3. 确认网络可达币安 WS（`wss://fstream.binance.com`）

### Coinglass/Coinalyze 数据缺失
1. 检查 `.env` 中 API Key 是否配置
2. 查看 `/rate-limits` 确认限速器状态
3. 检查日志中的 HTTP 状态码

### 内存占用过高
1. 检查 `WATCH_SYMBOLS` 数量（每多一个币种 × 11 个特征计算器 × 缓冲区）
2. `RingBuffer` 默认大小 1000 条，可通过调整降低内存

## 经验沉淀区

### 经验：FlowEdge 与 KKline 共用服务器但独立容器
- 现象: 两个项目部署在同一台服务器（47.254.246.53）
- 解决: 使用不同端口（KKline:8003, FlowEdge:8005）和不同容器名
- 注意: 部署时注意跨项目部署锁，避免并发构建导致资源冲突
- 日期: 2026-02

<!-- 按 R8 模板追加经验条目 -->
