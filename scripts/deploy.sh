#!/usr/bin/env bash
# 服务器端一键部署（在阿里云服务器上执行）
# 前置：服务器已 git clone 本仓库（origin 指向 GitHub 仓库）、已装 Docker + compose plugin、已配置 .env
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> 拉取最新代码（origin 应为 GitHub 仓库，即流水线测试过的版本）"
git fetch origin main
git checkout -B main origin/main

echo "==> 构建并启动整套应用"
export COMPOSE_PROJECT_NAME=wealth-p0
docker compose -f docker-compose.p0.yml build
docker compose -f docker-compose.p0.yml up -d

echo "==> 清理旧镜像"
docker image prune -f >/dev/null 2>&1 || true

echo "==> 部署完成。检查健康状态："
curl -fsS http://127.0.0.1:8010/api/v1/health && echo || echo "（健康检查未通过，请查看日志 docker compose -f docker-compose.p0.yml logs api）"
