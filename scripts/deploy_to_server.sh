#!/usr/bin/env bash
# 本地/CI 一键部署到阿里云服务器：把 deploy.sh 通过 SSH 推上去执行
# 用法：SSH_USER=root SSH_HOST=121.43.113.196 ./scripts/deploy_to_server.sh
#      （可用 SSH_PORT、SSH_KEY 覆盖默认）
set -euo pipefail

HOST="${SSH_HOST:-121.43.113.196}"
USER="${SSH_USER:-root}"
PORT="${SSH_PORT:-22}"
KEY=""
[ -n "${SSH_KEY:-}" ] && KEY="-i ${SSH_KEY}"

echo "==> 通过 SSH 部署到 ${USER}@${HOST}"
ssh ${KEY} -p "${PORT}" -o StrictHostKeyChecking=accept-new \
  "${USER}@${HOST}" 'bash -s' < "$(dirname "$0")/deploy.sh"
