# OpenRec auto-test

Cross-repository integration tests for OpenRec 0.1.0: live HTTP APIs, Chromium interactions with the Web demo and rec-console, and offline job acceptance checks from the distribution repository. Both `standalone` and `cluster` are supported. Run tests in a dedicated test environment, one mode at a time: the modes share ports, container names, and storage.

## Installation

`smoke` and `--dry-run` require only the Python standard library (Python 3.10+). API and UI tests require pytest and Playwright in the same Python environment:

```bash
cd /home/xsank.mz/openrec/auto-test
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m playwright install chromium
# On minimal Linux hosts, the environment administrator can install missing browser libraries:
# .venv/bin/python -m playwright install-deps chromium
```

The framework does not install packages or system dependencies automatically. Before UI tests, preflight launches headless Chromium; missing dependencies fail preflight and block subsequent startup and data writes. Set the browser cache with `PLAYWRIGHT_BROWSERS_PATH` or `--browser-path /path/to/cache`.

See the adjacent `example/` and `bigdata-platform/` repositories for deployment dependencies. Docker / Compose, Java 21, Maven, initialization data, and the services for the selected mode are required. `../example/release/openrec.json` defines source references and compatible versions; reports also record actual Git HEADs and uncommitted changes.

## Common commands

```bash
# Running environment: health, all recall channels, ranking mode, and user creation/deletion
python3 run.py standalone
python3 run.py cluster

# API and demo backend tests without launching a browser
.venv/bin/python run.py standalone --suite api

# Web demo and console interactions, excluding lengthy model training
.venv/bin/python run.py standalone --suite ui
.venv/bin/python run.py cluster --suite ui

# Full tests against the current environment; cluster runs distribution checks before API/UI tests
.venv/bin/python run.py standalone --suite full --continue-on-failure
.venv/bin/python run.py cluster --suite full --continue-on-failure

# Rebuild and restart from current sources; explicitly use --local for cluster
.venv/bin/python run.py cluster --restart --local --suite full --continue-on-failure
.venv/bin/python run.py standalone --restart --suite full --continue-on-failure

# Select groups (repeatable); mode checks and smoke always run first
.venv/bin/python run.py cluster --case daily-recall --case api --case demo
.venv/bin/python run.py cluster --case models-ui
python3 run.py cluster --restart --local --suite full --dry-run
```

`--start` only starts applications and requires existing example applications to be stopped. `--restart` calls the stop scripts for both modes before starting the selected mode. Cluster shutdown uses `--keep-platform`; volumes are retained and `down -v` is never invoked. Startup imports fixtures, builds images, and triggers initialization jobs. Do not run this against a production environment.

| Suite | Contents |
| --- | --- |
| smoke (default) | Mode checks, readiness, full recall diagnostics, rankScore, user projection and deletion |
| api | smoke + `api` + `demo` |
| ui | smoke + `ui` |
| full | smoke + distribution acceptance checks (cluster only) + `api` + `demo` + `ui` + `models-ui` |

`--case` overrides the suite's group selection; duplicates are removed while preserving the specified order. See [COVERAGE.md](COVERAGE.md) for test cases and coverage limits. `full` runs all currently registered tests; it does not exhaust every possible product state combination.

## Failures, cleanup, and timeouts

- Execution is fail-fast between groups by default. `--continue-on-failure` allows subsequent functional groups after assertion failures. Failures in preflight, shutdown, startup, smoke, or distribution scripts still block subsequent groups, as do pytest fixture errors. Restoration failures are reported separately as CleanupError and immediately stop the current pytest group and subsequent groups to prevent further environment changes.
- pytest collects all case results within a group. Only features explicitly unsupported in the selected mode are skipped. Product defects, missing required data, and training failures are failures; there is no xfail or automatic retry after failure.
- Entity IDs and experiment names use UUIDs. Users and items are deleted in finally blocks or fixture teardown. Graph experiments are deleted, DAG configurations rolled back, and recall aliases and models restored to their original versions. Restoration is verified by reading the resulting state.
- Smoke temporarily moves the fixture user's exposure key and restores it afterward, preserving the natural expiration of the original TTL. Do not use this fixture concurrently with production traffic or another regression run.
- **Cleanup is not a database transaction rollback**: Kafka/Hive/HDFS history, console version history, training artifacts, and test events may remain. Deleting a standalone user does not cascade to its events. Distribution `verify_*` scripts retain their existing behavior, including recall/model version changes; restoring the state from before the entire full run is not guaranteed. The model UI case restores only the version active when that case started.
- `--timeout` defaults to 7200 seconds per group; `--projection-timeout` defaults to 90 seconds for asynchronous projection. Each training job has a 600-second wait. Polling is limited to pending states and does not suppress HTTP errors.
- Process groups receive TERM on timeout, followed by KILL after 15 seconds if necessary. Forced termination cannot guarantee finally blocks will run. Submitted Airflow tasks continue after the client exits. Use the report's run ID to investigate, and check for leftover tasks occupying the cluster runner before rerunning.
- `.run.lock` prevents concurrent runs of both modes in this checkout. It cannot prevent runs from other checkouts, manual operations, or external scheduled jobs. Offline training, recall, and analytics share a runner; do not run them concurrently with pytest-xdist.

## Reports and troubleshooting

Each run writes to `reports/<UTC>-<mode>-<uuid>/` (ignored by Git):

```text
index.html             # Result summary that can be opened directly
results.json           # Manifest, actual component versions, commands, group states, individual pytest results
junit.xml              # Startup/preflight/acceptance/functional group results
api-junit.xml          # Individual API tests, including fixture errors
demo-junit.xml
ui-junit.xml
models-ui-junit.xml
<group>.log
<group>/<test>/
    server-http.jsonl  # Method, path, HTTP status, duration, response
    console-http.jsonl
    page.png          # Screenshot for each browser test
    page.html
    trace.zip         # Open with playwright show-trace
    browser-errors.json
```

Any failure produces a nonzero exit code. Mode-specific skips are counted separately from failures. Assertions check business status, nonempty results, data projection, and version changes in addition to HTTP 200. Logs may contain entity data from the test environment; treat CI artifacts as internal data.

Integration tests default to `127.0.0.1` on ports 13579 / 12345 / 8095 / 8123. For direct pytest debugging, set `OPENREC_TEST_SERVER_URL`, `OPENREC_TEST_WEB_URL`, `OPENREC_TEST_CONSOLE_URL`, or `OPENREC_TEST_RANK_URL`. The runner's smoke and distribution scripts still require local Docker and standard ports; a complete remote deployment is not supported by this workflow.

```bash
# Debug individual cases without the runner; ensure exclusive environment access and the correct mode
.venv/bin/python -m pytest integration/test_ui.py --mode standalone \
  --artifacts reports/debug --junitxml reports/debug.xml -k detail -vv

# Framework checks without connecting to services
python3 -m unittest discover -s tests -v
.venv/bin/python -m pytest integration --mode cluster --artifacts /tmp/collection --collect-only -q
```

By default, `pytest` discovers only `tests/`, avoiding accidental service connections. GitHub Actions checks framework unit tests, CLI plans, and test collection in both modes. Full integration tests require a dedicated execution host with the entire workspace and platform resources; ordinary PR runners do not start the big data cluster.

See [VALIDATION_20261017.md](VALIDATION_20261017.md) for the latest acceptance: standalone 42 passed / 23 mode-specific skips; cluster 65 passed / 0 skips, plus all five end-to-end checks. Cluster reused local images with current source mounts and compiled artifacts; its image rebuild was canceled. Airflow publication task IDs now use `publish-<tableName>`.

See [VALIDATION_FIXES.md](VALIDATION_FIXES.md#documentation-and-console-directory-regression-2026-10-15) for the earlier regression: standalone had 41 passes and 22 mode-specific skips; cluster had 63 passes and no skips, with all five end-to-end scripts passing. These results apply to the sources recorded in those reports; the latest runs reused existing images and jars with updated console source files mounted read-only. [VALIDATION_FRAMEWORK.md](VALIDATION_FRAMEWORK.md) preserves the initial framework runs, including failures, and [VALIDATION.md](VALIDATION.md) preserves the original acceptance record. Functional test startup can explicitly use `RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000`. This is not acceptance of the default latency/performance SLA; the framework does not silently change service budgets.
