# PaperLens 部署与恢复手册

本文记录当前求职 Demo 的服务器部署事实和最小运维流程。所有命令均在服务器项目目录 `/opt/paperlens-rag` 执行，除非特别说明。不要把 `.env`、API Key、Token 或私钥内容复制到命令输出、日志或文档中。

## 当前架构与端口

```text
Internet
  -> Alibaba Cloud ECS security group
  -> host Nginx :80 / :443
       :80  -> HTTPS 301 redirect
       :80  -> /.well-known/acme-challenge/ HTTP webroot exception
       :443 -> TLS termination -> http://127.0.0.1:8080
  -> frontend container Nginx :80 (host publishes :8080)
       static React files
       /api/* -> backend:8000/*
  -> FastAPI backend container :8000
       ingested_data volume -> /app/data/ingested
       fastembed_cache volume -> /tmp/fastembed_cache
```

- 云主机：Alibaba Cloud ECS，Ubuntu 22.04。
- 编排：Docker Compose。
- 当前公网入口：`https://47.238.193.115`。
- 公网安全组：`80/443` 对 `0.0.0.0/0` 开放；`22` 仅允许当前 VPN 出口 IP `/32`；`8080` 不允许公网访问。
- Compose 的 frontend 将宿主机 `8080` 映射到容器 `80`；公网流量必须先经过宿主机 Nginx。

## 启动与恢复检查清单

1. 确认仓库没有未知修改，并只用 fast-forward 更新：

   ```bash
   cd /opt/paperlens-rag
   git switch main
   git status --short --branch
   git pull --ff-only origin main
   ```

   工作区不干净时先停止，不要用 `reset --hard` 覆盖服务器文件。

2. 只确认 `.env` 存在且权限合理，不读取或打印内容：

   ```bash
   test -f .env
   stat -c '%a %n' .env
   ```

3. 静态验证并启动 Compose：

   ```bash
   docker compose config --quiet
   docker compose up --build -d
   docker compose ps
   ```

4. 从宿主机验证容器链路：

   ```bash
   curl -fsS http://127.0.0.1:8080/api/health
   docker compose logs --tail=100 backend frontend
   ```

5. 验证宿主机 Nginx 与公网入口：

   ```bash
   sudo nginx -t
   sudo systemctl status nginx --no-pager
   curl -I http://47.238.193.115/
   curl -fsS https://47.238.193.115/api/health
   ```

   普通 HTTP 路径应返回 HTTPS 301；ACME challenge 路径必须继续使用 HTTP webroot，不能被全局重定向破坏。

6. 确认两个命名卷仍存在。`ingested_data` 同时保存 normalized documents 和 `.corpus-embeddings/`，`fastembed_cache` 保存 FastEmbed 模型文件：

   ```bash
   docker volume ls | grep -E 'ingested_data|fastembed_cache'
   ```

## SSH、VPN 与安全组

- SSH 私钥文件为 `paperlens-demo-key.pem`；只在受信设备保存，禁止提交到 Git、复制到项目目录或展示其内容。
- 连接时使用实际服务器账号，不在文档中固化账号：

  ```bash
  ssh -i paperlens-demo-key.pem <ssh-user>@47.238.193.115
  ```

- 端口 `22` 的安全组来源必须是当前 VPN 出口 IP `/32`。
- 切换 VPN 节点前，先通过阿里云控制台把 SSH 来源更新为新节点出口 IP `/32`，确认规则生效后再切换和连接；确认新连接正常后移除旧 IP 规则。
- 不要为了临时排障把 `22` 或 `8080` 开放给 `0.0.0.0/0`。

## Docker 与 Nginx 排障

按由内到外的顺序检查：

```bash
cd /opt/paperlens-rag
docker compose ps
docker compose logs --tail=200 backend frontend
curl -v http://127.0.0.1:8080/api/health
sudo nginx -t
sudo journalctl -u nginx --since '30 minutes ago' --no-pager
sudo ss -ltnp | grep -E ':80|:443|:8080'
```

- `127.0.0.1:8080` 失败：先检查 Compose、frontend 健康状态和 backend 日志。
- 本地 `8080` 正常但公网失败：检查宿主机 Nginx、证书和安全组。
- 首次未缓存 RAG 较慢：检查 `fastembed_cache` 与 `ingested_data` 卷是否仍挂载；不要通过增加公网端口绕过 Nginx。
- Nginx `/api` 的长读取窗口用于 FastEmbed 冷启动和 Qwen 请求；排障时不要把超时误判为必须重试全文摄取。

## HTTPS 与证书续期

- 当前使用 Let's Encrypt 免费公网 IP 证书。
- Certbot 版本为 `5.7.0`；证书为 short-lived certificate，自动续期必须保持可用。
- `certbot renew --dry-run` 已通过；该命令默认不运行 deploy hook。
- 续期 deploy hook 会在成功更新证书后 reload Nginx。

例行检查：

```bash
sudo certbot --version
sudo certbot certificates
sudo systemctl list-timers --all | grep -i certbot
sudo certbot renew --dry-run
sudo certbot renew --dry-run --run-deploy-hooks
sudo nginx -t
```

续期依赖公网 `80` 可访问 `/.well-known/acme-challenge/`。普通 `certbot renew --dry-run` 只验证续期流程；完整验证续期及 Nginx reload hook 必须使用 `certbot renew --dry-run --run-deploy-hooks`。调整 HTTP 重定向、Nginx server block 或 deploy hook 后必须重新执行对应 dry-run。

## 公网 IP 变化后的恢复

ECS economical stop mode 可能更换公网 IP。原 IP 证书和 Nginx IP 配置不能直接视为对新 IP 有效。

1. 在阿里云控制台确认新公网 IP。
2. 确认安全组仍为：公网 `80/443`，SSH 仅当前 VPN 出口 IP `/32`，无公网 `8080`。
3. 更新宿主机 Nginx 中引用旧 IP 的 server name 或相关配置，同时保留 ACME HTTP webroot exception。
4. 使用已经验证过的 Let's Encrypt 公网 IP 签发流程为新 IP 申请证书；不要猜测或复用旧 IP 证书。
5. 更新 Nginx TLS 证书路径，执行 `sudo nginx -t`，成功后 reload Nginx。
6. 重新验证 HTTP 301、HTTPS `/api/health`、Certbot dry-run 和 deploy hook。
7. 更新对外入口记录及所有依赖旧 IP 的运维说明。

## 安全停机

计划停止容器时：

```bash
cd /opt/paperlens-rag
docker compose down
```

普通 `docker compose down` 会保留命名卷。禁止使用 `docker compose down --volumes`，除非已经明确决定永久删除摄取文档、corpus embedding cache 和 FastEmbed 模型缓存并完成备份。

使用 ECS economical stop mode 前记录当前公网 IP，并预期下次启动后需要执行“公网 IP 变化后的恢复”检查。不要删除 `/opt/paperlens-rag`、`.env` 或 Docker volumes 作为普通停机步骤。

## 回滚与最小恢复

部署前记录 Git 确认过的当前版本：

```bash
git rev-parse HEAD
git log -5 --oneline
```

如新版本失败，可临时切换到一个由 Git 确认的 known-good commit，再重建容器；不要使用破坏性 reset：

```bash
git switch --detach <known-good-commit>
docker compose up --build -d
curl -fsS http://127.0.0.1:8080/api/health
```

恢复到主线时执行 `git switch main` 并只使用 `git pull --ff-only origin main`。回滚应用镜像时保留 `ingested_data` 和 `fastembed_cache`；若数据 schema 与旧版本不兼容，应先备份卷并单独评估，不能盲目删除缓存或文档。
