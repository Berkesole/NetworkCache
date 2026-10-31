#!/bin/bash
# 运行 Docker 容器脚本
# Docker container run script

set -e

# 颜色输出
# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}运行 rsync-router 容器${NC}"
echo -e "${GREEN}Running rsync-router container${NC}"
echo -e "${GREEN}========================================${NC}"

# 镜像名称和标签
# Image name and tag
IMAGE_NAME="rsync-router"
IMAGE_TAG="${1:-latest}"
CONTAINER_NAME="rsync-router-dev"
APP_TARGET="${RSYNC_APP_TARGET:-${2:-rsync-router}}"
APP_ARGS="${RSYNC_APP_ARGS:-}"
APP_ARGS_FILE="${RSYNC_APP_ARGS_FILE:-}"
RUN_ENV_ARGS=("-e" "RSYNC_APP_TARGET=${APP_TARGET}")

# 项目根目录
# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 创建 data 目录（如果不存在）
# Create data directory if not exists
mkdir -p "${PROJECT_ROOT}/data"

echo -e "${YELLOW}镜像 / Image: ${IMAGE_NAME}:${IMAGE_TAG}${NC}"
echo -e "${YELLOW}容器名称 / Container name: ${CONTAINER_NAME}${NC}"
echo -e "${YELLOW}应用目标 / App target: ${APP_TARGET}${NC}"
if [[ -n "${APP_ARGS}" ]]; then
    echo -e "${YELLOW}应用参数 / App args: ${APP_ARGS}${NC}"
    RUN_ENV_ARGS+=("-e" "RSYNC_APP_ARGS=${APP_ARGS}")
fi
if [[ -n "${APP_ARGS_FILE}" ]]; then
    echo -e "${YELLOW}参数文件 / App args file: ${APP_ARGS_FILE}${NC}"
    RUN_ENV_ARGS+=("-e" "RSYNC_APP_ARGS_FILE=${APP_ARGS_FILE}")
fi
echo -e "${YELLOW}Data 目录 / Data directory: ${PROJECT_ROOT}/data${NC}"

# 检查镜像是否存在
# Check if image exists
if ! docker image inspect "${IMAGE_NAME}:${IMAGE_TAG}" > /dev/null 2>&1; then
    echo -e "${RED}✗ 镜像不存在，请先构建 / Image not found, please build first${NC}"
    echo -e "${YELLOW}运行 / Run: ./docker/build.sh${NC}"
    exit 1
fi

# 停止并删除已存在的容器
# Stop and remove existing container
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${YELLOW}停止并删除已存在的容器 / Stopping and removing existing container...${NC}"
    docker stop "${CONTAINER_NAME}" > /dev/null 2>&1 || true
    docker rm "${CONTAINER_NAME}" > /dev/null 2>&1 || true
fi

# 项目根目录
# Project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 创建 data 目录（如果不存在）
# Create data directory if not exists
mkdir -p "${PROJECT_ROOT}/data"
echo -e "${YELLOW}Data 目录 / Data directory: ${PROJECT_ROOT}/data${NC}"

# 运行容器
# Run container
echo -e "${YELLOW}启动容器 / Starting container...${NC}"
docker run -d \
    --name "${CONTAINER_NAME}" \
    -v "${PROJECT_ROOT}/data:/data" \
    "${RUN_ENV_ARGS[@]}" \
    --restart unless-stopped \
    "${IMAGE_NAME}:${IMAGE_TAG}"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 容器启动成功 / Container started successfully${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${YELLOW}查看日志 / View logs:${NC}"
    echo -e "  docker logs -f ${CONTAINER_NAME}"
    echo -e ""
    echo -e "${YELLOW}进入容器 / Enter container:${NC}"
    echo -e "  docker exec -it ${CONTAINER_NAME} /bin/bash"
    echo -e ""
    echo -e "${YELLOW}停止容器 / Stop container:${NC}"
    echo -e "  docker stop ${CONTAINER_NAME}"
    echo -e ""
    echo -e "${YELLOW}健康检查 / Health check:${NC}"
    echo -e "  docker exec ${CONTAINER_NAME} redis-cli -s /run/redis/redis.sock ping"
else
    echo -e "${RED}✗ 容器启动失败 / Container start failed${NC}"
    exit 1
fi
