# CI/CD 搭建说明（GitHub Actions + 阿里云 4C8G 部署）

> 目标：推代码到 **GitHub 私有仓库** `git@github.com:Kongtiaop/jingrong.git`（分支 `main`）→ GitHub Actions 跑 CI（测试+前端构建）→ 通过后 **SSH 部署**到阿里云服务器（121.43.113.196）。

## 一、需要下载/安装的东西

| 位置 | 需要装 | 说明 |
|---|---|---|
| 本地开发机 | 无 | git 已有；CI 跑在 GitHub 云端 |
| 阿里云服务器 | Docker Engine + Docker Compose plugin | 跑整套应用 |
| 阿里云服务器 | 访问仓库凭据（`/opt/wealth` 的 origin 指向 GitHub） | `deploy.sh` 要 `git fetch`；私有仓库需配 GitHub SSH key 或 token |
| GitHub 仓库 | Secrets：`SSH_HOST`/`SSH_USER`/`SSH_PRIVATE_KEY`/`SSH_PORT` | CI/CD 用它 SSH 登录服务器部署 |

## 二、GitHub 侧配置

1. 在 GitHub 新建的私有仓库（`Kongtiaop/jingrong`）。
2. 本地把代码推过去（Gitee 可继续作主力，也可弃用）：
   ```bash
   git remote add github git@github.com:Kongtiaop/jingrong.git
   git push -u github main
   ```
3. 生成一对 SSH 密钥（本地或服务器 `ssh-keygen -t ed25519`）。
4. 在 GitHub 仓库 **Settings → Secrets and variables → Actions** 添加：
   - `SSH_HOST` = `121.43.113.196`
   - `SSH_USER` = `root`（或你的用户）
   - `SSH_PRIVATE_KEY` = 私钥内容（OpenSSH 格式）
   - `SSH_PORT` = `22`
5. 把公钥加到**服务器**的 `~/.ssh/authorized_keys`（让 GitHub Actions 能 SSH 登录服务器）。

## 三、服务器一次性初始化（121.43.113.196）

```bash
# 1) 装 Docker
curl -fsSL https://get.docker.com | sh && systemctl enable --now docker
docker compose version || apt-get install -y docker-compose-plugin

# 2) 克隆仓库（origin 指向 GitHub 私有仓库，用 GitHub SSH key）
git clone git@github.com:Kongtiaop/jingrong.git /opt/wealth
cd /opt/wealth
cp .env.example .env   # 改数据库密码等

# 3) 首次手动部署验证
bash scripts/deploy.sh
```

## 四、触发方式

- push 到 GitHub `main` → Actions 自动跑 CI → 通过后自动部署到服务器。
- 也支持 `workflow_dispatch`（仓库 Actions 页手动触发）。

## 五、端口与防火墙

- 应用对外端口：`API=8010`（docker-compose.p0.yml 的 `API_PORT`）。
- 阿里云安全组放行 **8010**（和 SSH 22）。

## 六、服务器资源（4C8G）提示

- p0 compose 起 Postgres + Redis + API + 多个 worker（outbox/knowledge/memory/aml 等）。
- 演示够用；内存吃紧可只起核心：`docker compose -f docker-compose.p0.yml up -d postgres redis api memory-worker outbox-worker`。
- 日志：`docker compose -f docker-compose.p0.yml logs -f api`

## 七、本地手动部署（不想等流水线时）

```bash
SSH_USER=root SSH_HOST=121.43.113.196 ./scripts/deploy_to_server.sh
```
