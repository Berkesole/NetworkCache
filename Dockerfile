# Dockerfile - 单容器多进程部署（Redis + Router）
# Single-container multi-process deployment (Redis + Router)

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# 默认 Redis socket 路径（单一事实来源）
# Default Redis socket path (Single Source of Truth)
ENV REDIS_SOCKET_PATH=/run/redis/redis.sock
# Keep the existing runtime default until rsync-node-dev grows a real socket-backed transport.
ENV RSYNC_APP_TARGET=rsync-router

# 安装依赖
# Install dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    iproute2 \
    redis-server \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
# Set working directory
WORKDIR /app

# 复制源码
# Copy source code
COPY . /app

# 构建项目
# Build project
# Phase-1 acceptance prefers deterministic/offline-safe image builds.
# Redis service still ships in the image, but Redis cache support is opt-in
# until we choose a stable dependency strategy for redis-plus-plus/hiredis.
ARG ENABLE_REDIS=OFF
ARG FETCH_REDIS_DEPS=OFF
RUN cmake -S . -B build \
    -DENABLE_REDIS=${ENABLE_REDIS} \
    -DFETCH_REDIS_DEPS=${FETCH_REDIS_DEPS} && \
    cmake --build build -j$(nproc)

# 复制配置文件
# Copy configuration files
COPY docker/redis.conf.template /etc/redis/redis.conf.template
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /app/docker/run-rsync-app.sh

# 声明卷挂载点
# Declare volume mount points
VOLUME ["/data"]

# 健康检查（使用环境变量）
# Health check (using environment variable)
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
    CMD redis-cli -s "$REDIS_SOCKET_PATH" ping || exit 1

# 入口点
# Entrypoint
ENTRYPOINT ["/entrypoint.sh"]
