# ruikekkk Kathara 场景集

这里把 `main` 分支那 7 个带 `scenario:` 的 YAML 场景，整理成一套可复用的 Kathara 入口。

详细的中文使用与验证说明见：
- `docs/ruikekkk-kathara-validation-guide.zh-CN.md`

## 场景清单
- `3-node-topology`
- `triangle-topology`
- `ring-4-topology`
- `ring-5-topology`
- `ring-5-warm`
- `binary-tree-7-warm`
- `spine-leaf-2x4-warm`
- `kua-dc-16-warm`
- `kua-dc-32-warm`

这些场景的本地副本都放在：
- `kathara/ruikekkk/scenarios/`

## 运行方式
先看支持的场景：

```bash
scripts/kathara/run_ruikekkk_scenario.sh --list
```

跑某一个场景：

```bash
scripts/kathara/run_ruikekkk_scenario.sh ring-5-warm
```

停止当前 `ruikekkk` 实验：

```bash
scripts/kathara/down_ruikekkk_scenario.sh
```

## 设计说明
- `scripts/kathara/render_ruikekkk_scenario.sh <scenario>` 负责把选中的 YAML 渲染成当前实验用的：
  - `lab.conf`
  - `lab.dep`
  - `node*.startup`
  - `/shared/generated/*.network.sh`
  - `/shared/ruikekkk-topology.yaml`
- 运行时统一启动 `rsync-node-dev`
- 网络后端统一走 TCP
- Redis 只作为 cache backend，不承担控制面职责
- 渲染产物和日志都不入库
