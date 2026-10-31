#!/usr/bin/env python3
"""Read-only monitor for the two remote NetworkCache campaigns.

Campaigns:
  * a/b cross-host physical federation
      - volcengine   -> domain-a, unit networkcache-cross-host-v1-domain-a-formal.service
      - volcengine-1 -> domain-b, unit networkcache-cross-host-v1-domain-b-formal.service
      - results root: /data/networkcache-cross-host-federation-v1/results-pre-isolation-v2
      - target: 40 joint formal slots (NC/TCP Direct x 10 seeds x 2 repetitions)
  * worker-c open-arrival concurrency rerun (slides-v5)
      - volcengine-3, unit networkcache-concurrency-slides-v5.service
      - root: /data/networkcache-concurrency-slides-v5
      - target: 6 smoke + 64 pilot + 1280 formal slots

This script performs no mutation: ssh commands are read-only, and the local
cross_host.py `status` subcommand is documented as strictly read-only.  Output
is Markdown intended to be appended to a status log.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CROSS_HOST = REPO / "experiments" / "distributed_cross_host_federation_v1" / "cross_host.py"

CROSS_HOST_UNITS = {
    "domain-a": ("volcengine", "networkcache-cross-host-v1-domain-a-formal.service"),
    "domain-b": ("volcengine-1", "networkcache-cross-host-v1-domain-b-formal.service"),
}
CROSS_HOST_RESULTS = "/data/networkcache-cross-host-federation-v1/results-pre-isolation-v2"
CROSS_HOST_TARGET = 40

WORKER_C_HOST = "volcengine-3"
WORKER_C_ROOT = "/data/networkcache-concurrency-slides-v5"
WORKER_C_UNIT = "networkcache-concurrency-slides-v5.service"

CAPACITY_HOST = "simple-station"
CAPACITY_ROOT = "/home/simpleai/zhengzihao/networkcache-concurrency-capacity-v1"
CAPACITY_TARGETS = {"smoke": 6, "pilot": 42, "formal": 210}


def ssh(host: str, command: str, timeout: int = 90) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:  # pragma: no cover - environment issue
        return 127, "", str(exc)


def human_delta(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    return f"{hours}h {minutes}m"


def parse_systemd_timestamp(raw: str) -> dt.datetime | None:
    raw = raw.strip()
    if not raw or raw in {"-", "n/a"}:
        return None
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %Z"):
        try:
            return dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def unit_state(host: str, unit: str) -> dict:
    rc, out, err = ssh(
        host,
        f"systemctl show {unit} -p ActiveEnterTimestamp -p ActiveState -p SubState --value",
    )
    if rc != 0:
        return {"error": err or f"exit={rc}"}
    lines = out.splitlines()
    state = {
        "active": lines[0] if len(lines) > 0 else "?",
        "sub": lines[1] if len(lines) > 1 else "?",
        "entered_raw": lines[2] if len(lines) > 2 else "",
    }
    entered = parse_systemd_timestamp(state.get("entered_raw", ""))
    state["entered"] = entered
    if entered is not None:
        now = dt.datetime.now(entered.tzinfo) if entered.tzinfo else dt.datetime.now()
        state["elapsed"] = human_delta((now - entered).total_seconds())
    return state


def count_remote_dir(host: str, path: str) -> int | None:
    rc, out, _ = ssh(host, f"ls {path} 2>/dev/null | wc -l")
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def eta_lines(now: dt.datetime, start: dt.datetime | None, done: int, target: int,
              budget_seconds: float | None = None, rate_note: str = "") -> list[str]:
    """Report observed-rate projection and, when known, the frozen-budget projection."""
    lines: list[str] = []
    if start is not None and now.tzinfo is not None and start.tzinfo is None:
        # systemd prints local wall-clock time without a parsable zone abbreviation.
        start = start.replace(tzinfo=now.tzinfo)
    if start is not None and done > 0 and target > 0:
        elapsed = (now - start).total_seconds()
        if elapsed > 0:
            remaining = max(target - done, 0)
            rate_per_hour = done / (elapsed / 3600.0)
            if remaining == 0:
                lines.append("已达标（按观测计数）")
            else:
                observed_seconds = remaining / (done / elapsed)
                finish = now + dt.timedelta(seconds=observed_seconds)
                note = f"，{rate_note}" if rate_note else ""
                lines.append(
                    f"观测速率 {rate_per_hour:.2f}/h{note}，按此剩余 "
                    f"{human_delta(observed_seconds)}，预计 {finish.strftime('%m-%d %H:%M')} 完成"
                )
    if start is not None and budget_seconds:
        finish = start + dt.timedelta(seconds=budget_seconds)
        lines.append(
            f"按冻结预算 {human_delta(budget_seconds)} 全程推算预计 "
            f"{finish.strftime('%m-%d %H:%M')} 完成"
        )
    return lines


def cross_host_section(now: dt.datetime) -> list[str]:
    lines = ["### a/b 跨主机物理联邦（Domain A volcengine / Domain B volcengine-1）", ""]
    try:
        proc = subprocess.run(
            [sys.executable, str(CROSS_HOST), "status"],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=REPO,
        )
        status = json.loads(proc.stdout)
        evidence = status.get("evidence_status", "?")
    except Exception as exc:  # noqa: BLE001 - monitor should never crash
        lines.append(f"- cross_host.py status 失败：{exc}")
        status = None
        evidence = "?"

    lines.append(f"- evidence_status: `{evidence}`")
    for role, (host, unit) in CROSS_HOST_UNITS.items():
        state = unit_state(host, unit)
        slots = count_remote_dir(host, f"{CROSS_HOST_RESULTS}/formal/{role}")
        slot_txt = f"{slots}/{CROSS_HOST_TARGET}" if slots is not None else "?"
        if "error" in state:
            lines.append(f"- {role} (`{host}`): 单元状态查询失败：{state['error']}")
            continue
        lines.append(
            f"- {role} (`{host}`): `{state['active']}/{state['sub']}`，"
            f"自 {state.get('entered_raw', '?')} 起已运行 {state.get('elapsed', '?')}，"
            f"formal slot {slot_txt}"
        )
        for eta in eta_lines(now, state.get("entered"), slots or 0, CROSS_HOST_TARGET):
            lines.append(f"  - {eta}")
    if status:
        for worker in status.get("workers", []):
            lines.append(
                f"  - {worker.get('role')}: captured_attempt_files="
                f"{worker.get('captured_attempt_files')}"
            )
    lines.append("")
    return lines


def worker_c_section(now: dt.datetime) -> list[str]:
    lines = ["### worker-c 开放到达并发重跑（slides-v5）", ""]
    state = unit_state(WORKER_C_HOST, WORKER_C_UNIT)
    formal_done = formal_target = 0
    if "error" in state:
        lines.append(f"- 单元状态查询失败：{state['error']}")
    else:
        lines.append(
            f"- 单元：`{state['active']}/{state['sub']}`，自 {state.get('entered_raw', '?')} "
            f"起已运行 {state.get('elapsed', '?')}"
        )

    rc, out, err = ssh(
        WORKER_C_HOST,
        f"cd {WORKER_C_ROOT} && python3 -u storage/adapter/storage.py status",
        timeout=120,
    )
    if rc != 0:
        lines.append(f"- storage.py status 失败：{err or f'exit={rc}'}")
    else:
        try:
            status = json.loads(out)
        except json.JSONDecodeError as exc:
            lines.append(f"- storage.py status 解析失败：{exc}")
            status = None
        if status:
            for stage, info in status.get("stages", {}).items():
                lines.append(
                    f"- {stage}: qualified {info.get('qualified')}/{info.get('target')}"
                    f"（finished {info.get('finished')}，failed {len(info.get('failed') or [])}）"
                )
                if stage == "formal":
                    formal_done = int(info.get("qualified") or 0)
                    formal_target = int(info.get("target") or 0)
            unit_txt = (status.get("unit") or "").strip().replace("\n", " ")
            if unit_txt:
                lines.append(f"- unit: {unit_txt}")

    budget_seconds = None
    rc, out, _ = ssh(
        WORKER_C_HOST,
        f"cat {WORKER_C_ROOT}/storage/formal_budget.json 2>/dev/null",
    )
    if rc == 0 and out:
        try:
            budget_seconds = float(json.loads(out).get("estimated_formal_seconds_including_storage"))
        except (json.JSONDecodeError, TypeError, ValueError):
            budget_seconds = None

    if formal_target:
        for eta in eta_lines(
            now, state.get("entered"), formal_done, formal_target, budget_seconds,
            rate_note="早期含较多 Controlled 快配置，偏乐观",
        ):
            lines.append(f"- formal ETA：{eta}")

    rc, out, _ = ssh(
        WORKER_C_HOST,
        f"cat {WORKER_C_ROOT}/derived/checkpoint.json 2>/dev/null",
    )
    if rc == 0 and out:
        try:
            checkpoint = json.loads(out)
            lines.append(
                f"- 当前/最近 slot：`{checkpoint.get('slot')}`"
                f"（stage {checkpoint.get('stage')}，epoch_ns {checkpoint.get('epoch_ns')}）"
            )
        except json.JSONDecodeError:
            pass
    lines.append("")
    return lines


def capacity_section(now: dt.datetime) -> list[str]:
    """simple-station concurrency-capacity campaign (runs as root, no unit)."""
    lines = ["### simple-station 并发能力 campaign（capacity / burst）", ""]
    live = {}
    for stage in ("smoke", "pilot", "formal"):
        rc, out, _ = ssh(CAPACITY_HOST,
                         f"sudo ls {CAPACITY_ROOT}/raw/{stage} 2>/dev/null | wc -l", timeout=60)
        live[stage] = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
        count = live[stage] if live[stage] is not None else "?"
        lines.append(f"- {stage}: 已产生 {count}/{CAPACITY_TARGETS[stage]} 个 slot 目录")
    rc, out, _ = ssh(CAPACITY_HOST,
                     f"sudo cat {CAPACITY_ROOT}/derived/run_status.json 2>/dev/null", timeout=60)
    if rc == 0 and out:
        try:
            status = json.loads(out)
            for stage in ("smoke", "pilot", "formal"):
                items = status.get(stage) or []
                if live.get(stage) is not None and len(items) != live[stage]:
                    # run_status.json is only refreshed by analyze() at the end of
                    # a stage, so it lags the raw directories while a stage runs.
                    lines.append(f"  - {stage} 校验：derived 状态已过期（记录 {len(items)}，现场 {live[stage]}），待 analyze 刷新")
                    continue
                qualified = sum(1 for x in items if x.get("qualified"))
                overload = sum(1 for x in items if x.get("outcome") == "overload")
                failed = [x for x in items if not x.get("qualified")]
                lines.append(
                    f"  - {stage} 校验：qualified {qualified}/{CAPACITY_TARGETS[stage]}"
                    f"（finished {len(items)}，overload {overload}，failed {len(failed)}）"
                )
                if failed:
                    lines.append(f"  - 失败示例：{failed[0].get('slot')} :: {failed[0].get('reason')}")
        except json.JSONDecodeError:
            lines.append("  - run_status.json 解析失败")
    rc, out, _ = ssh(CAPACITY_HOST,
                     f"sudo cat {CAPACITY_ROOT}/derived/checkpoint.json 2>/dev/null", timeout=60)
    if rc == 0 and out:
        try:
            checkpoint = json.loads(out)
            lines.append(f"- 当前/最近 slot：`{checkpoint.get('slot')}`（stage {checkpoint.get('stage')}）")
        except json.JSONDecodeError:
            pass
    lines.append("")
    return lines


def main() -> int:
    now = dt.datetime.now().astimezone()
    lines = [f"## 巡检 {now.strftime('%Y-%m-%d %H:%M:%S %Z')}", ""]
    lines += cross_host_section(now)
    lines += worker_c_section(now)
    lines += capacity_section(now)
    lines.append("---")
    lines.append("")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())