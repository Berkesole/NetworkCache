## 巡检 2026-09-16 11:59:59 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 3h 43m，formal slot 14/40
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 3h 43m，formal slot 14/40
  - domain-a: captured_attempt_files=13
  - domain-b: captured_attempt_files=13

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 2h 15m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 58/1280（finished 58，failed 0）
- unit: ActiveState=active SubState=running
- 当前/最近 slot：`controlled-n32-b65536-c512-controlled-poisson-r128-tcp-direct--s004-r2`（stage formal，epoch_ns 1789531215383140878）

---

## 巡检 2026-09-16 12:01:11 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 3h 44m，formal slot 14/40
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 3h 44m，formal slot 14/40
  - domain-a: captured_attempt_files=13
  - domain-b: captured_attempt_files=13

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 2h 17m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 58/1280（finished 58，failed 0）
- unit: ActiveState=active SubState=running
- 当前/最近 slot：`controlled-n32-b65536-c512-controlled-poisson-r128-tcp-direct--s004-r2`（stage formal，epoch_ns 1789531215383140878）

---

Traceback (most recent call last):
  File "/root/zzh/rsync/experiments/ops/monitor_campaigns.py", line 266, in <module>
    raise SystemExit(main())
                     ^^^^^^
  File "/root/zzh/rsync/experiments/ops/monitor_campaigns.py", line 257, in main
    lines += cross_host_section(now)
             ^^^^^^^^^^^^^^^^^^^^^^^
  File "/root/zzh/rsync/experiments/ops/monitor_campaigns.py", line 169, in cross_host_section
    for eta in eta_lines(now, state.get("entered"), slots or 0, CROSS_HOST_TARGET):
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/root/zzh/rsync/experiments/ops/monitor_campaigns.py", line 116, in eta_lines
    elapsed = (now - start).total_seconds()
               ~~~~^~~~~~~
TypeError: can't subtract offset-naive and offset-aware datetimes
## 巡检 2026-09-16 12:07:02 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 3h 50m，formal slot 14/40
  - 观测速率 0.18/h，按此剩余 5d 20h 50m，预计 09-22 08:57 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 3h 50m，formal slot 14/40
  - 观测速率 0.18/h，按此剩余 5d 20h 50m，预计 09-22 08:57 完成
  - domain-a: captured_attempt_files=13
  - domain-b: captured_attempt_files=13

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 2h 22m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 66/1280（finished 66，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 27.89/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 19h 31m，预计 09-18 07:38 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`controlled-n16-b65536-c512-controlled-poisson-r32-tcp-direct--s005-r1`（stage formal，epoch_ns 1789531656485334514）

---

## 巡检 2026-09-16 13:32:06 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 5h 15m，formal slot 15/40
  - 观测速率 0.19/h，按此剩余 5d 8h 45m，预计 09-21 22:17 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 5h 15m，formal slot 15/40
  - 观测速率 0.19/h，按此剩余 5d 8h 45m，预计 09-21 22:17 完成
  - domain-a: captured_attempt_files=14
  - domain-b: captured_attempt_files=14

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 3h 47m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 101/1280（finished 101，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 26.69/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 20h 10m，预计 09-18 09:42 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`controlled-n32-b65536-c512-controlled-poisson-r32-nc--s007-r1`（stage formal，epoch_ns 1789536636409078467）

---

## 巡检 2026-09-16 15:02:28 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 6h 45m，formal slot 15/40
  - 观测速率 0.19/h，按此剩余 5d 11h 15m，预计 09-22 02:18 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 6h 46m，formal slot 15/40
  - 观测速率 0.19/h，按此剩余 5d 11h 16m，预计 09-22 02:18 完成
  - domain-a: captured_attempt_files=14
  - domain-b: captured_attempt_files=14

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 5h 18m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 143/1280（finished 143，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 27.03/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 18h 3m，预计 09-18 09:06 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`controlled-n32-b65536-c512-controlled-poisson-r128-nc--s009-r2`（stage formal，epoch_ns 1789542123407499841）

---

## 巡检 2026-09-16 16:33:23 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 8h 16m，formal slot 15/40
  - 观测速率 0.19/h，按此剩余 5d 13h 47m，预计 09-22 06:20 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 8h 17m，formal slot 15/40
  - 观测速率 0.19/h，按此剩余 5d 13h 47m，预计 09-22 06:21 完成
  - domain-a: captured_attempt_files=14
  - domain-b: captured_attempt_files=14

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 6h 49m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 175/1280（finished 175，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 25.71/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 18h 58m，预计 09-18 11:31 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n16-b65536-c512-iid-zipf-burst-r32-tcp-direct--s001-r1`（stage formal，epoch_ns 1789547540896352799）

---

## 巡检 2026-09-16 18:04:18 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 9h 47m，formal slot 15/40
  - 观测速率 0.18/h，按此剩余 5d 16h 18m，预计 09-22 10:23 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 9h 47m，formal slot 15/40
  - 观测速率 0.18/h，按此剩余 5d 16h 19m，预计 09-22 10:23 完成
  - domain-a: captured_attempt_files=14
  - domain-b: captured_attempt_files=14

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 8h 20m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 199/1280（finished 199，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 23.92/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 21h 12m，预计 09-18 15:16 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n16-b65536-c512-iid-zipf-burst-r128-nc--s002-r1`（stage formal，epoch_ns 1789552882886378700）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 5/42 个 slot 目录
- formal: 已产生 0/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 0/42（finished 42，overload 0，failed 42）
  - 失败示例：capacity-n16-poisson-r32-nc--s001 :: not_started
  - formal 校验：qualified 0/210（finished 210，overload 0，failed 210）
  - 失败示例：capacity-n16-poisson-r32-nc--s001 :: not_started
- 当前/最近 slot：`capacity-n16-poisson-r128-nc--s001`（stage pilot）

---

## 巡检 2026-09-16 19:35:38 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 11h 19m，formal slot 16/40
  - 观测速率 0.19/h，按此剩余 5d 4h 58m，预计 09-22 00:33 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 11h 19m，formal slot 16/40
  - 观测速率 0.19/h，按此剩余 5d 4h 58m，预计 09-22 00:34 完成
  - domain-a: captured_attempt_files=15
  - domain-b: captured_attempt_files=15

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 9h 51m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 223/1280（finished 223，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 22.66/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 22h 39m，预计 09-18 18:15 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n16-b65536-c512-iid-zipf-burst-r128-tcp-direct--s002-r2`（stage formal，epoch_ns 1789558531665476402）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 17/210 个 slot 目录

---

## 巡检 2026-09-16 21:07:06 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 12h 50m，formal slot 16/40
  - 观测速率 0.19/h，按此剩余 5d 7h 15m，预计 09-22 04:22 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 12h 50m，formal slot 16/40
  - 观测速率 0.19/h，按此剩余 5d 7h 15m，预计 09-22 04:22 完成
  - domain-a: captured_attempt_files=15
  - domain-b: captured_attempt_files=15

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 11h 22m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 248/1280（finished 248，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 21.82/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 23h 18m，预计 09-18 20:25 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-burst-r128-tcp-direct--s003-r2`（stage formal，epoch_ns 1789564071483256146）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 63/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 0/210（finished 210，overload 0，failed 210）
  - 失败示例：capacity-n16-poisson-r32-nc--s001 :: not_started
- 当前/最近 slot：`capacity-n32-poisson-r128-tcp-direct--s002`（stage formal）

---

## 巡检 2026-09-16 22:38:40 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 14h 22m，formal slot 16/40
  - 观测速率 0.19/h，按此剩余 5d 9h 32m，预计 09-22 08:11 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 14h 22m，formal slot 16/40
  - 观测速率 0.19/h，按此剩余 5d 9h 32m，预计 09-22 08:11 完成
  - domain-a: captured_attempt_files=15
  - domain-b: captured_attempt_files=15

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 12h 54m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 272/1280（finished 272，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 21.10/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 23h 46m，预计 09-18 22:25 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n16-b65536-c512-iid-zipf-burst-r128-tcp-direct--s004-r1`（stage formal，epoch_ns 1789569473827646235）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 115/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 0/210（finished 210，overload 0，failed 210）
  - 失败示例：capacity-n16-poisson-r32-nc--s001 :: not_started
- 当前/最近 slot：`capacity-n32-poisson-r4096-nc--s003`（stage formal）

---

## 巡检 2026-09-17 00:10:15 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 15h 53m，formal slot 17/40
  - 观测速率 0.19/h，按此剩余 4d 22h 54m，预计 09-21 23:04 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 15h 53m，formal slot 17/40
  - 观测速率 0.19/h，按此剩余 4d 22h 54m，预计 09-21 23:05 完成
  - domain-a: captured_attempt_files=16
  - domain-b: captured_attempt_files=16

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 14h 26m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 296/1280（finished 296，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 20.53/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 23h 56m，预计 09-19 00:06 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-poisson-r32-nc--s005-r1`（stage formal，epoch_ns 1789575019011581033）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 167/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 0/210（finished 210，overload 0，failed 210）
  - 失败示例：capacity-n16-poisson-r32-nc--s001 :: not_started
- 当前/最近 slot：`burst-n32-burst-r128-tcp-direct--s004`（stage formal）

---

## 巡检 2026-09-17 01:41:51 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 17h 25m，formal slot 17/40
  - 观测速率 0.19/h，按此剩余 5d 0h 58m，预计 09-22 02:40 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 17h 25m，formal slot 17/40
  - 观测速率 0.19/h，按此剩余 5d 0h 58m，预计 09-22 02:40 完成
  - domain-a: captured_attempt_files=16
  - domain-b: captured_attempt_files=16

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 15h 57m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 320/1280（finished 320，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 20.07/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 23h 50m，预计 09-19 01:32 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-poisson-r128-tcp-direct--s005-r2`（stage formal，epoch_ns 1789580561312697385）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 03:13:06 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 18h 56m，formal slot 17/40
  - 观测速率 0.19/h，按此剩余 5d 3h 1m，预计 09-22 06:15 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 18h 56m，formal slot 17/40
  - 观测速率 0.19/h，按此剩余 5d 3h 2m，预计 09-22 06:15 完成
  - domain-a: captured_attempt_files=16
  - domain-b: captured_attempt_files=16

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 17h 28m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 344/1280（finished 344，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 19.69/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 23h 31m，预计 09-19 02:44 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n16-b65536-c512-iid-zipf-burst-r128-nc--s006-r2`（stage formal，epoch_ns 1789585910087077286）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 04:44:18 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 20h 27m，formal slot 17/40
  - 观测速率 0.18/h，按此剩余 5d 5h 5m，预计 09-22 09:49 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 20h 27m，formal slot 17/40
  - 观测速率 0.18/h，按此剩余 5d 5h 5m，预计 09-22 09:49 完成
  - domain-a: captured_attempt_files=16
  - domain-b: captured_attempt_files=16

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 19h 0m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 366/1280（finished 366，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 19.28/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 23h 25m，预计 09-19 04:09 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-burst-r128-tcp-direct--s007-r1`（stage formal，epoch_ns 1789591449120841051）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 06:15:30 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 21h 58m，formal slot 17/40
  - 观测速率 0.18/h，按此剩余 5d 7h 8m，预计 09-22 13:24 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 21h 59m，formal slot 17/40
  - 观测速率 0.18/h，按此剩余 5d 7h 8m，预计 09-22 13:24 完成
  - domain-a: captured_attempt_files=16
  - domain-b: captured_attempt_files=16

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 20h 31m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 391/1280（finished 391，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 19.07/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 22h 37m，预计 09-19 04:53 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-poisson-r32-nc--s008-r1`（stage formal，epoch_ns 1789596714619249428）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 07:46:42 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 3d 23h 30m，formal slot 18/40
  - 观测速率 0.19/h，按此剩余 4d 20h 43m，预计 09-22 04:29 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 3d 23h 30m，formal slot 18/40
  - 观测速率 0.19/h，按此剩余 4d 20h 43m，预计 09-22 04:30 完成
  - domain-a: captured_attempt_files=17
  - domain-b: captured_attempt_files=17

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 22h 2m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 416/1280（finished 416，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 18.89/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 21h 44m，预计 09-19 05:31 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n16-b65536-c512-iid-zipf-burst-r128-nc--s008-r2`（stage formal，epoch_ns 1789602337828798258）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 09:17:53 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 1h 1m，formal slot 18/40
  - 观测速率 0.19/h，按此剩余 4d 22h 34m，预计 09-22 07:52 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 1h 1m，formal slot 18/40
  - 观测速率 0.19/h，按此剩余 4d 22h 34m，预计 09-22 07:52 完成
  - domain-a: captured_attempt_files=17
  - domain-b: captured_attempt_files=17

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 23h 33m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 441/1280（finished 441，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 18.73/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 20h 47m，预计 09-19 06:05 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-burst-r128-tcp-direct--s009-r2`（stage formal，epoch_ns 1789607843311006828）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 10:49:07 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 2h 32m，formal slot 18/40
  - 观测速率 0.18/h，按此剩余 5d 0h 26m，预计 09-22 11:15 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 2h 32m，formal slot 18/40
  - 观测速率 0.18/h，按此剩余 5d 0h 26m，预计 09-22 11:15 完成
  - domain-a: captured_attempt_files=17
  - domain-b: captured_attempt_files=17

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 1h 4m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 464/1280（finished 464，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 18.51/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 20h 5m，预计 09-19 06:54 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`zipf-n32-b65536-c512-iid-zipf-burst-r128-tcp-direct--s010-r1`（stage formal，epoch_ns 1789613301704348618）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 12:20:23 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 4h 3m，formal slot 19/40
  - 观测速率 0.19/h，按此剩余 4d 14h 35m，预计 09-22 02:55 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 4h 4m，formal slot 19/40
  - 观测速率 0.19/h，按此剩余 4d 14h 35m，预计 09-22 02:56 完成
  - domain-a: captured_attempt_files=18
  - domain-b: captured_attempt_files=18

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 2h 36m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 489/1280（finished 489，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 18.39/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 19h 0m，预计 09-19 07:21 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n16-b65536-c512-iid-uniform-burst-r32-nc--s001-r1`（stage formal，epoch_ns 1789618837928047552）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 13:51:43 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 5h 35m，formal slot 19/40
  - 观测速率 0.19/h，按此剩余 4d 16h 16m，预计 09-22 06:08 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 5h 35m，formal slot 19/40
  - 观测速率 0.19/h，按此剩余 4d 16h 16m，预计 09-22 06:08 完成
  - domain-a: captured_attempt_files=18
  - domain-b: captured_attempt_files=18

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 4h 7m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 514/1280（finished 514，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 18.28/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 17h 53m，预计 09-19 07:45 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n32-b65536-c512-iid-uniform-poisson-r128-tcp-direct--s002-r1`（stage formal，epoch_ns 1789624149643701808）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 15:22:44 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 7h 6m，formal slot 19/40
  - 观测速率 0.18/h，按此剩余 4d 17h 56m，预计 09-22 09:19 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 7h 6m，formal slot 19/40
  - 观测速率 0.18/h，按此剩余 4d 17h 57m，预计 09-22 09:19 完成
  - domain-a: captured_attempt_files=18
  - domain-b: captured_attempt_files=18

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 5h 38m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 537/1280（finished 537，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 18.12/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 16h 59m，预计 09-19 08:22 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n32-b65536-c512-iid-uniform-burst-r32-nc--s002-r2`（stage formal，epoch_ns 1789629766646719232）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 16:53:51 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 8h 37m，formal slot 19/40
  - 观测速率 0.18/h，按此剩余 4d 19h 37m，预计 09-22 12:31 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 8h 37m，formal slot 19/40
  - 观测速率 0.18/h，按此剩余 4d 19h 37m，预计 09-22 12:31 完成
  - domain-a: captured_attempt_files=18
  - domain-b: captured_attempt_files=18

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 7h 9m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 559/1280（finished 559，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 17.95/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 16h 10m，预计 09-19 09:04 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n32-b65536-c512-iid-uniform-burst-r32-tcp-direct--s003-r1`（stage formal，epoch_ns 1789635003784158884）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 18:25:19 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 10h 8m，formal slot 20/40
  - 观测速率 0.19/h，按此剩余 4d 10h 8m，预计 09-22 04:33 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 10h 8m，formal slot 20/40
  - 观测速率 0.19/h，按此剩余 4d 10h 8m，预计 09-22 04:33 完成
  - domain-a: captured_attempt_files=19
  - domain-b: captured_attempt_files=19

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 8h 41m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 582/1280（finished 582，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 17.81/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 15h 10m，预计 09-19 09:36 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n16-b65536-c512-iid-uniform-burst-r128-nc--s004-r1`（stage formal，epoch_ns 1789640658846649083）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 19:56:39 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 11h 40m，formal slot 20/40
  - 观测速率 0.19/h，按此剩余 4d 11h 39m，预计 09-22 07:36 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 11h 40m，formal slot 20/40
  - 观测速率 0.19/h，按此剩余 4d 11h 39m，预计 09-22 07:36 完成
  - domain-a: captured_attempt_files=19
  - domain-b: captured_attempt_files=19

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 10h 12m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 612/1280（finished 612，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 17.90/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 13h 19m，预计 09-19 09:15 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n32-b65536-c512-iid-uniform-poisson-r32-nc--s005-r1`（stage formal，epoch_ns 1789646225034887296）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 21:28:15 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 13h 11m，formal slot 20/40
  - 观测速率 0.18/h，按此剩余 4d 13h 11m，预计 09-22 10:39 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 13h 11m，formal slot 20/40
  - 观测速率 0.18/h，按此剩余 4d 13h 11m，预计 09-22 10:39 完成
  - domain-a: captured_attempt_files=19
  - domain-b: captured_attempt_files=19

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 11h 44m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 635/1280（finished 635，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 17.78/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 12h 16m，预计 09-19 09:45 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n32-b65536-c512-iid-uniform-burst-r32-nc--s005-r2`（stage formal，epoch_ns 1789651546142436976）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

## 巡检 2026-09-17 23:00:08 CST

### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）

- evidence_status: `COLLECTING_NO_PARTIAL_STATS`
- domain-a (`volcengine`): `active/running`，自 Sun 2026-09-13 08:16:55 CST 起已运行 4d 14h 43m，formal slot 20/40
  - 观测速率 0.18/h，按此剩余 4d 14h 43m，预计 09-22 13:43 完成
- domain-b (`volcengine-1`): `active/running`，自 Sun 2026-09-13 08:16:41 CST 起已运行 4d 14h 43m，formal slot 20/40
  - 观测速率 0.18/h，按此剩余 4d 14h 43m，预计 09-22 13:43 完成
  - domain-a: captured_attempt_files=19
  - domain-b: captured_attempt_files=19

### worker-c 开放到达并发重跑（slides-v5）

- 单元：`active/running`，自 Wed 2026-09-16 09:45:03 CST 起已运行 1d 13h 15m
- smoke: qualified 6/6（finished 6，failed 0）
- pilot: qualified 64/64（finished 64，failed 0）
- formal: qualified 658/1280（finished 658，failed 0）
- unit: ActiveState=active SubState=running
- formal ETA：观测速率 17.66/h，早期含较多 Controlled 快配置，偏乐观，按此剩余 1d 11h 12m，预计 09-19 10:12 完成
- formal ETA：按冻结预算 3d 11h 9m 全程推算预计 09-19 20:54 完成
- 当前/最近 slot：`uniform-n32-b65536-c512-iid-uniform-burst-r32-tcp-direct--s006-r2`（stage formal，epoch_ns 1789657009228173195）

### simple-station 并发能力 campaign（capacity / burst）

- smoke: 已产生 6/6 个 slot 目录
- pilot: 已产生 42/42 个 slot 目录
- formal: 已产生 210/210 个 slot 目录
  - smoke 校验：qualified 6/6（finished 6，overload 0，failed 0）
  - pilot 校验：qualified 42/42（finished 42，overload 0，failed 0）
  - formal 校验：qualified 210/210（finished 210，overload 0，failed 0）
- 当前/最近 slot：`burst-n32-burst-r256-tcp-direct--s005`（stage formal）

---

python3: can't open file '/root/zzh/rsync/experiments/ops/monitor_campaigns.py': [Errno 2] No such file or directory
python3: can't open file '/root/zzh/rsync/experiments/ops/monitor_campaigns.py': [Errno 2] No such file or directory
