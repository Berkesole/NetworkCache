# DSH Web 公网穿透（frp）部署说明

把本机 `127.0.0.1:3080`（dsh web）通过 frp 暴露到公网 **http://124.174.41.239:8337**。

## 拓扑

```
浏览器 ──公网──> 124.174.41.239:8337 (frps)
                      │  frp 控制连接: tcp/8000 (token + TLS)
                      ▼
                 本机 frpc ──> 127.0.0.1:3080 (dsh web)
```

- 本机因在 NAT 后，由 frpc **主动外连**，无需任何入站端口。
- 火山云安全组已放通 `tcp 8000-9000`，故控制端口用 `8000`、公网入口用 `8337`（`7000/38080` 实测被安全组丢弃）。

## 端口与角色

| 位置 | 端口 | 用途 | 暴露范围 |
|---|---|---|---|
| 124.174.41.239 | 8000 | frps 控制端口（frpc 接入） | 公网（需 token） |
| 124.174.41.239 | 8337 | **dsh web 公网入口** | 公网（需 DSH token/ cookie） |
| 124.174.41.239 | 7500 | frps 管理面板（admin 账号） | 仅回环，需 SSH 隧道 |
| 本机 | 3080 | dsh web 本体 | 仅回环 |
| 本机 | / | frpc 出站连接 | — |

## 文件清单

**服务端 124.174.41.239（别名 `volcengine-3`）**
- `/opt/frp/frps` — frps 0.71.0 二进制
- `/etc/frp/frps.toml` — 配置（token、allowPorts 8000-9000、面板、强制 TLS）
- `/etc/systemd/system/frps.service` — 已 enable，开机自启
- `/var/log/frps.log` — 日志

**本机（运行 dsh web 的机器）**
- `/opt/frp/frpc` — frpc 0.71.0 二进制
- `/etc/frp/frpc.toml` — 配置（serverAddr/Port、token、dsh-web 代理 + healthCheck）
- `/etc/systemd/system/frpc.service` — 已 enable，开机自启
- `/var/log/frpc.log` — 日志

**本仓库（`/root/zzh/rsync/frp-deploy`）**
- `frps.toml.tmpl` / `frpc.toml.tmpl` — 配置模板（占位符 `__TOKEN__` 等）
- `render.sh` — 用 `.frp_token` / `.frp_panel_password` 渲染到 `build/`
- `install-frps.sh` / `install-frpc.sh` — 两端安装脚本
- `remote-*.sh` — 远端排查脚本
- `pkg/frp_0.71.0_linux_amd64/` — 解压后的二进制
- `.frp_token`、`.frp_panel_password` — 密钥（600 权限，**勿入库/勿外传**）

## 公网访问方式

DSH Web 有自己的一层鉴权：启动时会打印一行带**进程 token** 的 URL，浏览器访问该 URL 后会写入签名 cookie。

1. 在你启动 dsh web 的终端里找到 `dsh web:` 那一行（形如 `http://127.0.0.1:3080/?token=xxxx`）。
2. 把其中的 `127.0.0.1:3080` 换成 `124.174.41.239:8337`，用浏览器打开：
   `http://124.174.41.239:8337/?token=<你的token>`
3. 之后会 303 跳转到干净的 `http://124.174.41.239:8337/`，cookie 生效，即可正常使用。

> **前提：Host fence 信任该公网 authority。** DSH 的 `/api` 有 Host 校验，默认只信任回环（及 `--trusted-host`）。
> 已通过 profile patch 追加（见下），**需重启一次 dsh web 才生效**（或重启时改用
> `dsh --profile web --trusted-host 124.174.41.239`）。

在浏览器里验证是否已生效（应返回 401 而不是 403）：

```sh
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: 124.174.41.239:8337' http://127.0.0.1:3080/api
# 403 = fence 未信任；401 = 已信任（仅缺 token）
```

## 已做的 DSH 侧配置

`/root/.dsh/profiles/web/cordis.patch.yml` 追加：

```yaml
- id: connection
  config:
    trustedHosts: !!js [...(ctx.webRuntime?.trustedHosts ?? []), '124.174.41.239']
```

该文件是 DSH 官方 patch 层（bundle 内注释即推荐此写法），且 profile 的 `patchReload: "live"`，改动会被实时监听。

## 运维命令

```sh
# 服务端
ssh volcengine-3 'systemctl status frps --no-pager; tail -n 20 /var/log/frps.log'
ssh volcengine-3 'systemctl restart frps'

# 本机
systemctl status frpc --no-pager
systemctl restart frpc
tail -n 20 /var/log/frpc.log

# 管理面板（本机执行，然后浏览器开 http://127.0.0.1:7500 ，账号 admin）
ssh -N -L 7500:127.0.0.1:7500 volcengine-3

# 端到端自检
curl -s -o /dev/null -w '%{http_code}\n' http://124.174.41.239:8337/
```

## 安全提醒（重要）

DSH Web 背后的 agent 拥有本机 shell 能力，暴露到公网等于把**远程代码执行**入口放在互联网上，仅靠一个进程 token 保护：

- 泄露该 token 的 URL（浏览器历史、代理/网关日志、Referer）等于泄露本机控制权。
- 建议在火山云安全组把 `8337` 的源地址限制为你自己的出口 IP；或在 124.174.41.239 上用 iptables 只放行你的 IP：
  ```sh
  ssh volcengine-3 "iptables -A INPUT -p tcp --dport 8337 -s <你的IP>/32 -j ACCEPT; \
                    iptables -A INPUT -p tcp --dport 8337 -j DROP"
  ```
- 不在公共网络使用；用完可 `systemctl stop frpc` 立即断开公网入口。
- frps 控制端口已有 token + 强制 TLS；面板仅监听回环。

## 回滚

```sh
systemctl disable --now frpc           # 本机停止穿透
ssh volcengine-3 'systemctl disable --now frps'   # 服务端停止
# 删除 profile patch 中的 connection 条目即可恢复 DSH 默认信任策略
```