# Packaging validation

Validation date: **2026-10-21**.

## Environment

- Ubuntu 24.04.2 LTS, x86_64.
- GNU C++ 13.3.0.
- CMake 3.28.3.
- Python 3.12.3.
- Debug build; `ENABLE_REDIS=OFF`, `FETCH_REDIS_DEPS=OFF`.

The package was configured and compiled from its own source directory into a fresh temporary build directory. It did not reuse the development repository's build products or fetch third-party dependencies.

## Executed checks

| Check | Result |
| --- | --- |
| Build all default CMake targets | PASS |
| CTest `core` | PASS |
| CTest `mock_delivery` (line and triangle) | PASS |
| CTest `tcp_delivery` (three independent loopback processes) | PASS |
| Included shell script syntax | PASS |
| Python runner syntax | PASS |
| C++ source comparison against the source snapshot | All 29 files identical |
| Example topology comparison | Configuration values unchanged; whole-line comments removed |

CTest summary: **3/3 passed**, 0 failed, approximately 4.4 seconds total on this environment. This duration is a smoke-check observation, not a performance result. The core target was compiled with assertions enabled (`-UNDEBUG`).

The package was also checked for broken local documentation links, accidental inclusion of manuscript/meeting files or generated binaries, private absolute paths, and common credential patterns. The source workspace was left unchanged.

## Not executed during packaging

- Redis backend contract/runtime suite: `redis-server` was not installed in the validation environment.
- Docker image build and container execution.
- Kathara labs and multi-host network deployments.
- Figure-level performance experiments.

The relevant sources and optional deployment helpers are included where available, but these checks are not represented as passing. See [reproducibility scope](REPRODUCIBILITY.md) for the distinction between functional execution and full experiment reproduction.
