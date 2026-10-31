#!/bin/bash
# 容器入口脚本 - 统一配置 Redis socket 路径
# Container entrypoint - unified Redis socket path configuration

set -e

# 默认 socket 路径（单一事实来源）
# Default socket path (Single Source of Truth)
REDIS_SOCKET_PATH="${REDIS_SOCKET_PATH:-/run/redis/redis.sock}"
export REDIS_SOCKET_PATH
RSYNC_APP_TARGET="${RSYNC_APP_TARGET:-rsync-router}"
export RSYNC_APP_TARGET

echo "=== rsync 容器启动 ==="
echo "Redis socket 路径: ${REDIS_SOCKET_PATH}"
echo "应用目标 / App target: ${RSYNC_APP_TARGET}"

if [ ! -x "/app/build/${RSYNC_APP_TARGET}" ]; then
    echo "错误: 目标二进制不存在或不可执行: /app/build/${RSYNC_APP_TARGET}" >&2
    exit 1
fi

# 创建 socket 目录并设置权限
# Create socket directory and set permissions
SOCKET_DIR="$(dirname "$REDIS_SOCKET_PATH")"
mkdir -p "$SOCKET_DIR"
chmod 755 "$SOCKET_DIR"
echo "Socket 目录已创建: ${SOCKET_DIR}"

# 创建 data 目录结构
# Create data directory structure
mkdir -p /data/redis
mkdir -p /data/logs
mkdir -p /data/tmp
mkdir -p /data/state

# 设置目录权限
# Set directory permissions
chmod 755 /data/redis
chmod 755 /data/logs
chmod 755 /data/tmp
chmod 755 /data/state
echo "Data 目录结构已创建: /data/{redis,logs,tmp,state}"

# 渲染 Redis 配置文件（替换 socket 路径）
# Render Redis configuration (replace socket path)
REDIS_CONF_TEMPLATE="/etc/redis/redis.conf.template"
REDIS_CONF="/etc/redis/redis.conf"

if [ -f "$REDIS_CONF_TEMPLATE" ]; then
    sed "s|__REDIS_SOCKET_PATH__|${REDIS_SOCKET_PATH}|g" \
        "$REDIS_CONF_TEMPLATE" > "$REDIS_CONF"
    echo "Redis 配置已渲染: ${REDIS_CONF}"
else
    echo "警告: 模板文件不存在，使用默认配置"
fi

# 启动 supervisord
# Start supervisord
echo "启动 supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
