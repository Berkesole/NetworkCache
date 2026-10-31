# 如何使用 Kathara 测试验证新增的 7 个 `ruikekkk` 场景

本文档说明如何在 Rsync 仓库中，使用 Kathara 对 `kathara/ruikekkk/` 下新增的 7 个场景做**单场景验证**与**全量 7 场景验证**。

## 1. 适用范围

适用仓库：

- `/root/project/NetOS/NetOS-Ubuntu/Rsync`

适用脚本：

- `scripts/kathara/render_ruikekkk_scenario.sh`
- `scripts/kathara/run_ruikekkk_scenario.sh`
- `scripts/kathara/down_ruikekkk_scenario.sh`

适用场景目录：

- `kathara/ruikekkk/scenarios/`

## 2. 这 7 个场景是什么

`ruikekkk` 场景集来自 `main` 分支里带 `scenario:` 段的 YAML 拓扑样例。当前纳入验证的 7 个场景如下：

| 场景名 | 节点数 | 边数 | 预置数据 | 计划请求 |
| --- | ---: | ---: | --- | --- |
| `3-node-topology` | 3 | 2 | `3:redis_key_a=VALUE_OF_A` | `1:redis_key_a` |
| `triangle-topology` | 3 | 3 | `3:redis_key_a=VALUE_OF_A` | `1:redis_key_a` |
| `ring-4-topology` | 4 | 4 | `3:redis_key_a=VALUE_OF_A` | `1:redis_key_a` |
| `ring-5-topology` | 5 | 5 | `3:redis_key_a=VALUE_OF_A` | `1:redis_key_a` |
| `ring-5-warm` | 5 | 5 | `3:redis_key_a=VALUE_OF_A` | `1:redis_key_a@0`、`4:redis_key_a@8` |
| `binary-tree-7-warm` | 7 | 6 | `7:redis_key_a=VALUE_OF_A` | `4:redis_key_a@0`、`5:redis_key_a@8` |
| `spine-leaf-2x4-warm` | 8 | 12 | `8:redis_key_a=VALUE_OF_A` | `1:redis_key_a@0`、`2:redis_key_a@10` |

> `config/8x8-mesh-topology.yaml` 没有 `scenario:` 段，因此**不属于这 7 个验证场景**。

## 3. Kathara 验证链路做了什么

`run_ruikekkk_scenario.sh <scenario>` 的职责不是“只打印一下场景名”，而是会真实完成下面这条链：

1. 调用 `render_ruikekkk_scenario.sh <scenario>`
2. 从 `kathara/ruikekkk/scenarios/<scenario>.yaml` 读取场景
3. 自动生成当前实验用的：
   - `kathara/ruikekkk/lab.conf`
   - `kathara/ruikekkk/lab.dep`
   - `kathara/ruikekkk/node*.startup`
   - `kathara/ruikekkk/shared/generated/node*.network.sh`
   - `kathara/ruikekkk/shared/ruikekkk-topology.yaml`
4. 启动 Kathara lab
5. 在每个节点里启动：
   - Redis（作为 cache backend）
   - `rsync-node-dev`（TCP 模式）
6. 把日志写到：
   - `kathara/ruikekkk/shared/logs/`

也就是说，这套验证是**真实的 Kathara + 多节点 TCP + Redis cache backend**，不是只跑单进程 mock-sim。

## 4. 运行前准备

进入仓库根目录：

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync
```

确保以下命令可用：

```bash
docker version
kathara --version
```

如果你准备直接复用已经推到 Docker Hub 的镜像，也可以先拉取：

```bash
docker pull zihaocnic/rsync:ruikekkk-<shortsha>-<YYYYMMDD-HHMM>
```

例如当前已验证过的一版：

```bash
docker pull zihaocnic/rsync:ruikekkk-b898b29-20260512-1115
```

## 5. 查看支持的场景

```bash
scripts/kathara/run_ruikekkk_scenario.sh --list
```

预期会列出这 7 个场景名。

## 6. 单场景验证

### 6.1 首次运行：建议允许 Docker 使用缓存

```bash
BUILD_NO_CACHE=0 scripts/kathara/run_ruikekkk_scenario.sh 3-node-topology
```

### 6.2 如果本地已经有对应镜像：可跳过构建

```bash
SKIP_BUILD=1 scripts/kathara/run_ruikekkk_scenario.sh ring-5-warm
```

### 6.3 如果想直接使用 Docker Hub 上的镜像

```bash
IMAGE_TAG=zihaocnic/rsync:ruikekkk-b898b29-20260512-1115 \
SKIP_BUILD=1 \
scripts/kathara/run_ruikekkk_scenario.sh triangle-topology
```

## 7. 单场景成功时应看到什么

### 7.1 runner 标准输出中

至少应看到类似输出：

```text
scenario=triangle-topology
source=/root/project/NetOS/NetOS-Ubuntu/Rsync/kathara/ruikekkk/scenarios/triangle-topology.yaml
lab_dir=/root/project/NetOS/NetOS-Ubuntu/Rsync/kathara/ruikekkk
rendered_topology=/root/project/NetOS/NetOS-Ubuntu/Rsync/kathara/ruikekkk/shared/ruikekkk-topology.yaml
node_count=3
edge_count=3
...
active_scenario=triangle-topology
logs_dir=/root/project/NetOS/NetOS-Ubuntu/Rsync/kathara/ruikekkk/shared/logs
```

这些信息说明：

- 场景参数被正确解析
- 拓扑已被渲染为 Kathara 可执行资产
- 对应的 lab 已成功启动

### 7.2 节点日志中

节点日志在：

- `kathara/ruikekkk/shared/logs/node1.log`
- `kathara/ruikekkk/shared/logs/node2.log`
- ...

至少应检查以下信号：

```bash
grep -R "\[rsync-node-dev\] startup configuration" kathara/ruikekkk/shared/logs/node*.log
grep -R "tcp transport initialized" kathara/ruikekkk/shared/logs/node*.log
grep -R "cache backend: redis" kathara/ruikekkk/shared/logs/node*.log
grep -R "\[App\] Received data for key=" kathara/ruikekkk/shared/logs/node*.log
grep -R "completed .* loop iteration" kathara/ruikekkk/shared/logs/node*.log
```

最关键的是最后两类：

- 出现 `\[App\] Received data for key=...`
- 出现 `completed ... loop iteration(s)`

这说明：

- 场景里的请求确实被发起了
- 数据确实被应用层收到
- 节点主循环正常跑完了预设轮次

## 8. 全量验证 7 个场景

如果你要验证 **runner 是否真正覆盖了全部 7 个场景**，建议用下面这段批量命令。

特点：

- 第 1 个场景执行构建
- 后 6 个场景复用同一个镜像
- 每个场景的 runner 标准输出分别落盘
- 统一生成一份总汇总文件

### 8.1 推荐批量命令

```bash
cd /root/project/NetOS/NetOS-Ubuntu/Rsync

SCENARIOS=(
  3-node-topology
  binary-tree-7-warm
  ring-4-topology
  ring-5-topology
  ring-5-warm
  spine-leaf-2x4-warm
  triangle-topology
)

OUT_DIR="$PWD/kathara/ruikekkk/shared/logs"
SUMMARY="$OUT_DIR/ruikekkk-batch-summary.txt"
mkdir -p "$OUT_DIR"
: > "$SUMMARY"

printf 'branch=%s\nhead=%s\n' "$(git branch --show-current)" "$(git rev-parse --short HEAD)" | tee -a "$SUMMARY"
printf 'started_at=%s\n' "$(date -Is)" | tee -a "$SUMMARY"

first=1
for scenario in "${SCENARIOS[@]}"; do
  echo "===== RUN ${scenario} =====" | tee -a "$SUMMARY"

  if [[ "$first" == "1" ]]; then
    cmd=(env BUILD_NO_CACHE=0 ./scripts/kathara/run_ruikekkk_scenario.sh "$scenario")
    first=0
  else
    cmd=(env SKIP_BUILD=1 ./scripts/kathara/run_ruikekkk_scenario.sh "$scenario")
  fi

  set +e
  "${cmd[@]}" >"$OUT_DIR/${scenario}.batch.stdout.log" 2>&1
  rc=$?
  set -e

  printf 'scenario=%s rc=%s\n' "$scenario" "$rc" | tee -a "$SUMMARY"
  printf 'stdout_log=%s\n' "$OUT_DIR/${scenario}.batch.stdout.log" | tee -a "$SUMMARY"
  printf 'run_log_dir=%s\n' "$OUT_DIR" | tee -a "$SUMMARY"

  if [[ "$rc" -ne 0 ]]; then
    echo "FAILED_SCENARIO=$scenario" | tee -a "$SUMMARY"
    tail -n 120 "$OUT_DIR/${scenario}.batch.stdout.log" | tee -a "$SUMMARY"
    exit "$rc"
  fi
done

printf 'finished_at=%s\n' "$(date -Is)" | tee -a "$SUMMARY"
echo 'ALL_SCENARIOS_PASS=1' | tee -a "$SUMMARY"
```

### 8.2 批量验证成功标准

批量跑完后，至少应满足：

1. shell 退出码为 `0`
2. `kathara/ruikekkk/shared/logs/ruikekkk-batch-summary.txt` 中出现：

```text
ALL_SCENARIOS_PASS=1
```

3. 7 个场景都出现各自的 `rc=0`
4. 7 个 `*.batch.stdout.log` 都存在

## 9. 批量验证时的一个重要注意事项

`kathara/ruikekkk/shared/logs/node*.log` 是**当前实验的共享节点日志**。

这意味着：

- 你连续跑多个场景时
- `node1.log`、`node2.log`、... 这些文件会被**后一次运行覆盖**

所以：

- 如果你只关心“全量是否跑通”，看 `ruikekkk-batch-summary.txt` 和每个 `*.batch.stdout.log` 就够了
- 如果你要**保留每个场景自己的节点日志**，应在每轮结束后把 `node*.log` 另存一份

### 9.1 如需保留每个场景的节点日志，可在循环内追加归档

```bash
ARCHIVE_ROOT="$OUT_DIR/archive-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$ARCHIVE_ROOT"

# 每个场景成功后追加：
SCENE_DIR="$ARCHIVE_ROOT/$scenario"
mkdir -p "$SCENE_DIR"
cp kathara/ruikekkk/shared/active-scenario.txt "$SCENE_DIR"/
cp kathara/ruikekkk/shared/ruikekkk-topology.yaml "$SCENE_DIR"/
cp kathara/ruikekkk/lab.conf kathara/ruikekkk/lab.dep "$SCENE_DIR"/
cp kathara/ruikekkk/shared/logs/node*.log "$SCENE_DIR"/ 2>/dev/null || true
cp kathara/ruikekkk/shared/logs/node*.startup.log "$SCENE_DIR"/ 2>/dev/null || true
cp kathara/ruikekkk/shared/logs/node*.redis.log "$SCENE_DIR"/ 2>/dev/null || true
```

## 10. 停止 / 清理当前 ruikekkk 实验

```bash
scripts/kathara/down_ruikekkk_scenario.sh
```

它会执行：

```bash
kathara lclean -d /root/project/NetOS/NetOS-Ubuntu/Rsync/kathara/ruikekkk
```

## 11. 常见问题

### 11.1 `missing required command: docker`
说明宿主机 Docker CLI 未安装或不在 PATH。

### 11.2 `missing required command: kathara`
说明宿主机 Kathara 未安装或不在 PATH。

### 11.3 `FAIL: docker build`
优先检查：

- Docker daemon 是否正常
- 是否有拉取基础镜像的网络权限
- Docker Hub 登录态是否有效（如果你依赖远端镜像）

### 11.4 场景跑完了，但想知道是不是“真验证到了应用层”
不要只看 Kathara 启动成功，还要检查：

```bash
grep -R "\[App\] Received data for key=" kathara/ruikekkk/shared/logs/node*.log
```

如果没有这类记录，说明只把 lab 拉起来还不够，应用层未必真正完成了场景请求。

## 12. 当前推荐的验收口径

对单场景：

- `run_ruikekkk_scenario.sh <scenario>` 退出码为 `0`
- stdout 打出 `scenario=...`、`node_count=...`、`edge_count=...`、`active_scenario=...`
- 节点日志中能看到：
  - `startup configuration`
  - `tcp transport initialized`
  - `cache backend: redis`
  - `\[App\] Received data`
  - `completed ... loop iteration(s)`

对全量 7 场景：

- `ruikekkk-batch-summary.txt` 出现 `ALL_SCENARIOS_PASS=1`
- 7 个场景均有 `rc=0`
- 7 个 `*.batch.stdout.log` 全部存在

---

如果你只需要一句最短命令口径：

```bash
BUILD_NO_CACHE=0 scripts/kathara/run_ruikekkk_scenario.sh 3-node-topology
```

如果你要验证 runner 是否真的覆盖了全部 7 个新增场景，请直接用 **第 8 节的批量命令**。
