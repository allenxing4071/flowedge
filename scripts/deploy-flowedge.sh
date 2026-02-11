#!/bin/bash
# 本文件核心用途：FlowEdge 部署脚本（本地 Docker + 阿里云服务器部署）
# ============================================================================
# FlowEdge 部署脚本 v1.0
# 订单流驱动的量化特征引擎 - 本地开发 + 云服务器部署
# ============================================================================
#
# 🚀 快捷命令（常用）:
#   ./scripts/deploy-flowedge.sh c       # 云服务器一键部署 (rsync + 重建)
#   ./scripts/deploy-flowedge.sh cr      # 云服务器仅重建 (不传代码)
#   ./scripts/deploy-flowedge.sh cx      # 云服务器快速重启 (不重建镜像)
#   ./scripts/deploy-flowedge.sh cs      # 查看云服务器状态
#   ./scripts/deploy-flowedge.sh cl      # 查看云服务器日志
#
# 🏠 本地命令:
#   ./scripts/deploy-flowedge.sh start   # 本地 Docker 启动
#   ./scripts/deploy-flowedge.sh stop    # 本地 Docker 停止
#   ./scripts/deploy-flowedge.sh status  # 查看本地状态
#   ./scripts/deploy-flowedge.sh logs    # 查看本地日志
#
# ============================================================================

set -e

# ============================================================================
# 配置
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env"

# 云服务器配置（复用 KKline 的阿里云服务器）
CLOUD_SERVER_IP="47.254.246.53"
CLOUD_SERVER_USER="root"
CLOUD_SSH_KEY="$HOME/Documents/soft/KKline/deploy/LH.pem"
CLOUD_SSH_PORT="2222"
CLOUD_PROJECT_PATH="/opt/FlowEdge"
CLOUD_CONTAINER="flowedge-backend"
CLOUD_HEALTH_URL="http://127.0.0.1:8005/health"

# SSH 稳定性参数
CLOUD_SSH_CONNECT_TIMEOUT=10
CLOUD_SSH_CMD_TIMEOUT=30
CLOUD_SSH_BUILD_TIMEOUT=300
CLOUD_SSH_RETRY=3
CLOUD_SSH_SOCKET="/tmp/flowedge-ssh-control-$$"

# 跨项目部署锁（与 KKline/TradeDesk 共享）
DEPLOY_LOCK="/tmp/server-deploy.lock"
DEPLOY_LOCK_ACQUIRED=0

# ============================================================================
# 颜色输出
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

log() { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"; }
ok() { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
err() { echo -e "${RED}❌ $1${NC}"; }

# ============================================================================
# SSH 连接复用：建立持久连接
# ============================================================================
cloud_ssh_connect() {
    if [ -S "$CLOUD_SSH_SOCKET" ]; then
        if ssh -S "$CLOUD_SSH_SOCKET" -p "$CLOUD_SSH_PORT" -O check "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP" 2>/dev/null; then
            return 0
        fi
        rm -f "$CLOUD_SSH_SOCKET"
    fi

    log "🔗 建立 SSH 持久连接..."

    local attempt=1
    while [ $attempt -le "$CLOUD_SSH_RETRY" ]; do
        if ssh -M -S "$CLOUD_SSH_SOCKET" -fN \
            -i "$CLOUD_SSH_KEY" \
            -p "$CLOUD_SSH_PORT" \
            -o StrictHostKeyChecking=no \
            -o BatchMode=yes \
            -o ConnectTimeout="$CLOUD_SSH_CONNECT_TIMEOUT" \
            -o ServerAliveInterval=30 \
            -o ServerAliveCountMax=10 \
            -o ControlPersist=10m \
            -o TCPKeepAlive=yes \
            "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP" 2>/dev/null; then
            ok "SSH 持久连接已建立"
            return 0
        fi

        if [ $attempt -lt "$CLOUD_SSH_RETRY" ]; then
            warn "连接失败，2秒后重试 (${attempt}/${CLOUD_SSH_RETRY})..."
            attempt=$((attempt + 1))
            sleep 2
        else
            warn "无法建立持久连接，将使用普通模式"
            return 1
        fi
    done
}

# ============================================================================
# SSH 连接复用：关闭
# ============================================================================
cloud_ssh_disconnect() {
    if [ -S "$CLOUD_SSH_SOCKET" ]; then
        ssh -S "$CLOUD_SSH_SOCKET" -p "$CLOUD_SSH_PORT" -O exit "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP" 2>/dev/null || true
        rm -f "$CLOUD_SSH_SOCKET"
        log "🔌 SSH 持久连接已关闭"
    fi
}
cleanup_on_exit() {
    release_deploy_lock 2>/dev/null || true
    cloud_ssh_disconnect
}
trap cleanup_on_exit EXIT

# ============================================================================
# SSH 执行命令（带重试）
# ============================================================================
cloud_ssh() {
    local timeout="${CLOUD_SSH_TIMEOUT:-$CLOUD_SSH_CMD_TIMEOUT}"
    local alive_interval=5
    local alive_count=12
    if [ -n "$timeout" ] && [ "$timeout" -ge 60 ] 2>/dev/null; then
        alive_interval=30
        alive_count=20
    fi

    # 优先使用持久连接
    if [ -S "$CLOUD_SSH_SOCKET" ]; then
        ssh -S "$CLOUD_SSH_SOCKET" \
            -p "$CLOUD_SSH_PORT" \
            -o ServerAliveInterval=$alive_interval \
            -o ServerAliveCountMax=$alive_count \
            "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP" "$@"
        local rc=$?
        if [ $rc -eq 0 ]; then
            return 0
        fi
        if ssh -S "$CLOUD_SSH_SOCKET" -p "$CLOUD_SSH_PORT" -O check "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP" 2>/dev/null; then
            return $rc
        fi
        rm -f "$CLOUD_SSH_SOCKET"
        warn "持久连接断开，回退到普通模式..."
    fi

    # 普通模式（带重试）
    local attempt=1
    local ssh_opts=(
        -i "$CLOUD_SSH_KEY"
        -p "$CLOUD_SSH_PORT"
        -o StrictHostKeyChecking=no
        -o BatchMode=yes
        -o ConnectTimeout="$CLOUD_SSH_CONNECT_TIMEOUT"
        -o ServerAliveInterval=$alive_interval
        -o ServerAliveCountMax=$alive_count
        -o ConnectionAttempts=1
    )

    while true; do
        if ssh "${ssh_opts[@]}" "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP" "$@"; then
            return 0
        fi

        local rc=$?
        if [ $attempt -ge "$CLOUD_SSH_RETRY" ]; then
            return $rc
        fi
        warn "SSH 失败，2秒后重试 (${attempt}/${CLOUD_SSH_RETRY})..."
        attempt=$((attempt + 1))
        sleep 2
    done
}

# ============================================================================
# SSH 执行 stdin 脚本
# ============================================================================
cloud_ssh_script() {
    local stdin_payload=""
    if [ ! -t 0 ]; then
        stdin_payload="$(cat || true)"
    fi
    if [ -z "$stdin_payload" ]; then
        err "cloud_ssh_script: 没有从 stdin 读取到内容"
        return 1
    fi
    printf '%s' "$stdin_payload" | cloud_ssh "$@"
}

# ============================================================================
# 跨项目部署锁
# ============================================================================
acquire_deploy_lock() {
    if [ "$DEPLOY_LOCK_ACQUIRED" = "1" ]; then return 0; fi
    log "🔒 检查部署锁..."
    if cloud_ssh "test -f /tmp/server-deploy.lock && [ \$(( \$(date +%s) - \$(stat -c %Y /tmp/server-deploy.lock) )) -lt 600 ]" 2>/dev/null; then
        local owner
        owner=$(cloud_ssh "cat /tmp/server-deploy.lock 2>/dev/null" || echo "未知")
        err "部署锁被占用: $owner"
        err "如需强制解锁，在服务器执行: rm -f /tmp/server-deploy.lock"
        return 1
    fi
    cloud_ssh "date '+FlowEdge %Y-%m-%d %H:%M:%S' > /tmp/server-deploy.lock"
    DEPLOY_LOCK_ACQUIRED=1
    ok "部署锁已获取 (FlowEdge)"
}

release_deploy_lock() {
    if [ "$DEPLOY_LOCK_ACQUIRED" = "1" ]; then
        cloud_ssh "rm -f /tmp/server-deploy.lock" 2>/dev/null || true
        DEPLOY_LOCK_ACQUIRED=0
        log "🔓 部署锁已释放"
    fi
}

# ============================================================================
# 检查连接
# ============================================================================
cloud_check_connection() {
    log "🔍 检查云服务器连接..."
    if [ ! -f "$CLOUD_SSH_KEY" ]; then
        err "SSH 密钥文件不存在: $CLOUD_SSH_KEY"
        return 1
    fi
    if cloud_ssh "echo '连接成功'" 2>/dev/null; then
        ok "云服务器连接正常 ($CLOUD_SERVER_IP)"
        return 0
    else
        err "无法连接到云服务器 ($CLOUD_SERVER_IP)"
        return 1
    fi
}

# ============================================================================
# 确保系统 Nginx 运行
# ============================================================================
ensure_system_nginx() {
    log "🔄 确保系统 Nginx 运行..."
    cloud_ssh "if systemctl is-active nginx >/dev/null 2>&1; then systemctl reload nginx && echo 'Nginx reloaded'; else systemctl start nginx && echo 'Nginx started'; fi"
    ok "系统 Nginx 已确认运行"
}

# ============================================================================
# 本地 Docker 启动
# ============================================================================
local_start() {
    log "🚀 本地 Docker 启动 FlowEdge..."
    cd "$PROJECT_ROOT"

    if [ ! -f "$ENV_FILE" ]; then
        err ".env 文件不存在，请先 cp .env.example .env 并配置"
        exit 1
    fi

    docker compose -f "$COMPOSE_FILE" up -d --build

    sleep 3
    ok "本地服务已启动"
    echo ""
    echo -e "  健康检查: ${GREEN}http://localhost:8005/health${NC}"
    echo -e "  系统状态: ${GREEN}http://localhost:8005/status${NC}"
    echo -e "  特征快照: ${GREEN}http://localhost:8005/features/snapshot${NC}"
    echo -e "  API 文档: ${GREEN}http://localhost:8005/docs${NC}"
}

# ============================================================================
# 本地 Docker 停止
# ============================================================================
local_stop() {
    log "⏹️ 停止本地服务..."
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" down
    ok "本地服务已停止"
}

# ============================================================================
# 本地状态
# ============================================================================
local_status() {
    echo ""
    echo -e "${CYAN}════════════════════ 本地服务状态 ════════════════════${NC}"
    echo ""
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" ps
    echo ""
}

# ============================================================================
# 本地日志
# ============================================================================
local_logs() {
    cd "$PROJECT_ROOT"
    docker compose -f "$COMPOSE_FILE" logs -f --tail=100
}

# ============================================================================
# 云服务器: rsync 同步代码
# ============================================================================
cloud_sync() {
    log "📤 同步代码到云服务器..."

    cloud_ssh_connect
    cloud_check_connection || return 1

    cloud_ssh "mkdir -p $CLOUD_PROJECT_PATH"

    rsync -avz --delete \
        -e "ssh -i $CLOUD_SSH_KEY -p $CLOUD_SSH_PORT -o StrictHostKeyChecking=no" \
        --exclude '.git' \
        --exclude '.env' \
        --exclude 'data/' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.cursor' \
        --exclude '.skills' \
        --exclude 'tests/' \
        "$PROJECT_ROOT/" \
        "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP:$CLOUD_PROJECT_PATH/"

    ok "代码同步完成"
}

# ============================================================================
# 云服务器: 重建服务（强制清理缓存）
# ============================================================================
cloud_rebuild() {
    log "🔄 云服务器重建 FlowEdge 服务..."

    cloud_ssh_connect
    cloud_check_connection || return 1

    acquire_deploy_lock || return 1

    log "🛑 停止 FlowEdge + 清理旧镜像..."
    cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose down 2>/dev/null || true"
    cloud_ssh "docker rmi -f \$(docker images -q flowedge*) 2>/dev/null || true"

    log "📦 无缓存重建 Docker 镜像..."
    CLOUD_SSH_TIMEOUT="$CLOUD_SSH_BUILD_TIMEOUT" cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose build --no-cache 2>&1 | tail -10"
    cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose up -d"

    log "⏳ 等待服务启动..."
    sleep 5

    log "📊 服务状态:"
    cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose ps"

    echo ""
    log "🩺 健康检查..."
    local health
    health=$(cloud_ssh "curl -s $CLOUD_HEALTH_URL 2>/dev/null || echo 'FAIL'")
    echo "  $health"

    cloud_ssh "docker image prune -f 2>/dev/null | tail -2"

    ensure_system_nginx

    release_deploy_lock

    echo ""
    ok "FlowEdge 重建完成"
}

# ============================================================================
# 云服务器: 快速重启（不重建镜像）
# ============================================================================
cloud_restart() {
    log "🔄 云服务器快速重启 FlowEdge..."

    cloud_ssh_connect
    cloud_check_connection || return 1

    cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose restart"

    sleep 3
    log "🩺 健康检查..."
    local health
    health=$(cloud_ssh "curl -s $CLOUD_HEALTH_URL 2>/dev/null || echo 'FAIL'")
    echo "  $health"

    ok "FlowEdge 已重启"
}

# ============================================================================
# 云服务器: 一键部署（rsync + 重建）
# ============================================================================
cloud_deploy() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║    FlowEdge 一键部署 (rsync + Docker 重建)   ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
    echo ""

    cloud_sync
    cloud_rebuild

    echo ""
    echo -e "${CYAN}════════════════════ 部署完成 ════════════════════${NC}"
    echo ""
    echo -e "  系统状态: ${GREEN}http://localhost:8005/status${NC} (本地)"
    echo ""
}

# ============================================================================
# 云服务器: 查看状态
# ============================================================================
cloud_status() {
    echo ""
    echo -e "${CYAN}════════════════════ FlowEdge 云服务器状态 ════════════════════${NC}"
    echo ""

    cloud_ssh_connect
    cloud_check_connection || return 1

    log "📊 Docker 状态:"
    cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose ps 2>/dev/null || echo '(未部署)'"

    echo ""
    log "🩺 健康检查:"
    local health
    health=$(cloud_ssh "curl -s $CLOUD_HEALTH_URL 2>/dev/null || echo 'FAIL'")
    echo "  $health"

    echo ""
    log "📈 系统状态:"
    local status
    status=$(cloud_ssh "curl -s http://127.0.0.1:8005/status 2>/dev/null || echo 'FAIL'")
    echo "  $status"

    echo ""
    log "💾 磁盘使用:"
    cloud_ssh "du -sh $CLOUD_PROJECT_PATH 2>/dev/null || echo '(目录不存在)'"

    echo ""
}

# ============================================================================
# 云服务器: 查看日志
# ============================================================================
cloud_logs() {
    cloud_ssh_connect
    cloud_check_connection || return 1

    log "📋 FlowEdge 日志（Ctrl+C 退出）..."
    cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose logs -f --tail=100"
}

# ============================================================================
# 云服务器: 首次初始化
# ============================================================================
cloud_init() {
    log "🔧 云服务器首次初始化 FlowEdge..."

    cloud_ssh_connect
    cloud_check_connection || return 1

    cloud_sync

    # 检查/上传 .env
    log "📋 检查远程 .env..."
    local has_env
    has_env=$(cloud_ssh "[ -f $CLOUD_PROJECT_PATH/.env ] && echo 'yes' || echo 'no'")
    if [ "$has_env" = "no" ]; then
        log "📋 上传 .env 到服务器..."
        if [ -S "$CLOUD_SSH_SOCKET" ]; then
            scp -o "ControlPath=$CLOUD_SSH_SOCKET" -P "$CLOUD_SSH_PORT" \
                "$ENV_FILE" "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP:$CLOUD_PROJECT_PATH/.env"
        else
            scp -i "$CLOUD_SSH_KEY" -P "$CLOUD_SSH_PORT" -o StrictHostKeyChecking=no \
                "$ENV_FILE" "$CLOUD_SERVER_USER@$CLOUD_SERVER_IP:$CLOUD_PROJECT_PATH/.env"
        fi
        ok ".env 已上传"
    else
        ok "远程 .env 已存在，跳过"
    fi

    # 初始化环境
    cloud_ssh_script << 'REMOTE_SCRIPT'
set -e
cd /opt/FlowEdge

echo "═══ 检查 Docker ═══"
if ! command -v docker &>/dev/null; then
    echo "安装 Docker..."
    curl -fsSL https://get.docker.com | sh
fi
docker --version

echo ""
echo "═══ 创建数据目录 ═══"
mkdir -p data

echo ""
echo "✅ 环境检查完成"
REMOTE_SCRIPT

    # 构建并启动
    log "📦 构建 Docker 镜像..."
    CLOUD_SSH_TIMEOUT="$CLOUD_SSH_BUILD_TIMEOUT" cloud_ssh "cd $CLOUD_PROJECT_PATH && docker compose up -d --build 2>&1 | tail -10"

    sleep 5

    # 健康检查
    log "🩺 健康检查..."
    local health
    health=$(cloud_ssh "curl -s $CLOUD_HEALTH_URL 2>/dev/null || echo 'FAIL'")
    echo "  健康检查: $health"

    echo ""
    echo -e "${CYAN}════════════════════ 初始化完成 ════════════════════${NC}"
    echo ""
    echo -e "  本地状态: ${GREEN}http://localhost:8005/status${NC}"
    echo ""
    echo -e "  ${YELLOW}注意: Nginx 反代配置需在 TradeDesk/KKline 的 Nginx 中添加${NC}"
    echo ""
}

# ============================================================================
# 主入口
# ============================================================================
show_help() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║     FlowEdge 部署脚本 v1.0                   ║${NC}"
    echo -e "${CYAN}║     订单流驱动的量化特征引擎                  ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BOLD}云服务器命令:${NC}"
    echo -e "    ${GREEN}c${NC}      一键部署 (rsync + Docker 重建)"
    echo -e "    ${GREEN}cr${NC}     仅重建服务 (不传代码)"
    echo -e "    ${GREEN}cx${NC}     快速重启 (不重建镜像)"
    echo -e "    ${GREEN}cs${NC}     查看云服务器状态"
    echo -e "    ${GREEN}cl${NC}     查看云服务器日志"
    echo -e "    ${GREEN}sync${NC}   仅同步代码 (不重建)"
    echo -e "    ${GREEN}i${NC}      首次初始化"
    echo ""
    echo -e "  ${BOLD}本地命令:${NC}"
    echo -e "    ${GREEN}start${NC}  本地 Docker 启动"
    echo -e "    ${GREEN}stop${NC}   本地 Docker 停止"
    echo -e "    ${GREEN}status${NC} 查看本地状态"
    echo -e "    ${GREEN}logs${NC}   查看本地日志"
    echo ""
}

case "${1:-}" in
    # 云服务器命令
    c)      cloud_deploy ;;
    cr)     cloud_rebuild ;;
    cx)     cloud_restart ;;
    cs)     cloud_status ;;
    cl)     cloud_logs ;;
    sync)   cloud_sync ;;
    i)      cloud_init ;;

    # 本地命令
    start)  local_start ;;
    stop)   local_stop ;;
    status) local_status ;;
    logs)   local_logs ;;

    # 帮助
    *)      show_help ;;
esac
