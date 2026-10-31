#!/bin/bash
# 构建 Docker 镜像脚本
# Docker image build script

set -e

# 颜色输出
# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}构建 rsync-router Docker 镜像${NC}"
echo -e "${GREEN}Building rsync-router Docker image${NC}"
echo -e "${GREEN}========================================${NC}"

# 镜像名称和标签
# Image name and tag
IMAGE_NAME="rsync-router"
IMAGE_TAG="${1:-latest}"

echo -e "${YELLOW}镜像名称 / Image name: ${IMAGE_NAME}:${IMAGE_TAG}${NC}"

# 构建镜像
# Build image
echo -e "${YELLOW}开始构建 / Starting build...${NC}"
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" -f Dockerfile ..

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 构建成功 / Build successful${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "${YELLOW}运行镜像 / Run image:${NC}"
    echo -e "  docker run --rm ${IMAGE_NAME}:${IMAGE_TAG}"
    echo -e ""
    echo -e "${YELLOW}或使用运行脚本 / Or use run script:${NC}"
    echo -e "  ./docker/run.sh"
else
    echo -e "${RED}✗ 构建失败 / Build failed${NC}"
    exit 1
fi
