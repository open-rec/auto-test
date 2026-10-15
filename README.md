# OpenRec auto-test

OpenRec 0.1.0 跨仓集成测试入口：真实 HTTP API、Chromium Web demo / rec-console 操作，以及发行仓的离线任务验收。支持 `standalone` 和 `cluster`；测试必须在专用测试环境运行，两种模式共享端口、容器名和存储，串行执行。

## 安装

`smoke` 和 `--dry-run` 只需要 Python 标准库（Python 3.10+）。API / UI 测试使用同一个 Python 环境中的 pytest 和 Playwright：

```bash
cd /home/xsank.mz/openrec/auto-test
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m playwright install chromium
# 精简 Linux 主机缺少浏览器系统库时，由环境维护者安装：
# .venv/bin/python -m playwright install-deps chromium
```

框架不会自动安装包或系统依赖。UI 运行前会实际启动一次无头 Chromium 做预检；缺少依赖会失败并阻止后续启动、数据写入。浏览器缓存可用 `PLAYWRIGHT_BROWSERS_PATH` 或 `--browser-path /path/to/cache` 指定。

发行环境依赖见相邻 `example/` 和 `bigdata-platform/`。需 Docker / Compose、Java 21、Maven、初始化数据及对应模式的服务。源码和兼容版本以 `../example/release/openrec.json` 为准，报告同时记录实际 Git HEAD 和未提交改动。

## 常用命令

```bash
# 已启动环境：轻量健康、全召回通道、排序模式、用户新增删除
python3 run.py standalone
python3 run.py cluster

# API / demo 后端，不启动浏览器
.venv/bin/python run.py standalone --suite api

# Web demo 和控制台交互（不执行耗时模型训练）
.venv/bin/python run.py standalone --suite ui
.venv/bin/python run.py cluster --suite ui

# 当前环境全量测试；cluster 会先执行发行仓验收，再执行 API 和 UI
.venv/bin/python run.py standalone --suite full --continue-on-failure
.venv/bin/python run.py cluster --suite full --continue-on-failure

# 重启当前源码构建；cluster 显式使用 --local
.venv/bin/python run.py cluster --restart --local --suite full --continue-on-failure
.venv/bin/python run.py standalone --restart --suite full --continue-on-failure

# 选择组（可重复）；始终先执行模式检查和 smoke
.venv/bin/python run.py cluster --case daily-recall --case api --case demo
.venv/bin/python run.py cluster --case models-ui
python3 run.py cluster --restart --local --suite full --dry-run
```

`--start` 仅启动，要求现有示例应用已停止；`--restart` 先调用两个模式的 stop 脚本，再启动指定模式。cluster 停止传入 `--keep-platform`，不删除 volume，不执行 `down -v`。启动自身会导入 fixture、构建镜像、触发初始化任务；请勿用于业务环境。

| suite | 内容 |
| --- | --- |
| smoke（默认） | 模式检查、ready、完整召回诊断、rankScore、用户投影与删除 |
| api | smoke + `api` + `demo` |
| ui | smoke + `ui` |
| full | smoke + cluster 发行验收（仅 cluster）+ `api` + `demo` + `ui` + `models-ui` |

`--case` 覆盖 suite 的组选择，按给定顺序去重执行。所有功能用例及边界见 [COVERAGE.md](COVERAGE.md)。`full` 表示运行当前注册的全部测试，不表示已经穷尽产品的所有状态组合。

## 失败、清理与超时

- 默认组间 fail-fast；`--continue-on-failure` 允许断言失败后继续其他功能组。预检、停止、启动、smoke、发行脚本失败仍阻断后续组；pytest fixture 错误也阻断。恢复失败用 CleanupError 单独标记，立即停止当前 pytest 组及后续组，避免继续修改环境。
- pytest 在组内收集全部用例结果。仅模式明确不支持的功能标记 skipped；产品缺陷、缺少必需数据、训练失败均为失败，没有 xfail 或失败后自动重试。
- 实体 ID 和实验名使用 UUID。用户、物品在 finally/fixture teardown 删除；图实验删除，DAG 配置回滚，召回别名和模型恢复原版本，并读取验证恢复结果。
- smoke 临时移走 fixture 用户曝光 key，结束后恢复原 key，保留原 TTL 的自然流逝。不能和业务流量/其他回归同时操作这个 fixture。
- **不是数据库事务回滚**：Kafka/Hive/HDFS 历史、控制台版本历史、训练产物和测试事件可能保留。standalone 删除用户不级联清理其事件。发行仓 `verify_*` 自身会修改召回/模型版本，保留其原有语义；不保证恢复到整个 full 运行前状态。模型 UI 用例只恢复到该用例开始时的活动版本。
- `--timeout` 默认每组 7200 秒；`--projection-timeout` 默认异步投影等待 90 秒。训练每个任务等待 600 秒。仅对尚未完成的状态轮询，不吞掉 HTTP 错误。
- 进程组超时发送 TERM，15 秒后必要时 KILL。强制终止无法保证 finally 执行；已提交的 Airflow 任务不会随客户端退出停止。报告中的 run ID 可用于继续排查，重跑前确认没有遗留任务占用 cluster runner。
- `.run.lock` 防止本 checkout 同时运行两种模式；不能阻止其他 checkout、手动操作或外部定时任务。离线训练、召回、分析共享 runner，不要用 pytest-xdist 并发执行。

## 报告与定位

每次运行写入 `reports/<UTC>-<mode>-<uuid>/`（Git 忽略）：

```text
index.html             # 可直接打开的结果汇总
results.json           # manifest、组件实际版本、命令、组状态、逐条 pytest 结果
junit.xml              # 启动/预检/验收/功能组级别结果
api-junit.xml          # 每个 API 测试（含 fixture error）
demo-junit.xml
ui-junit.xml
models-ui-junit.xml
<group>.log
<group>/<test>/
    server-http.jsonl  # 方法、路径、HTTP 状态、耗时、响应
    console-http.jsonl
    page.png          # 每条浏览器测试截图
    page.html
    trace.zip         # 可用 playwright show-trace 打开
    browser-errors.json
```

任何失败返回非零退出码；模式 skip 与失败分别统计。HTTP 200 之外还验证业务状态、非空结果、数据投影及版本变化。日志可能含测试环境中的实体数据，CI artifact 应按内部数据处理。

集成测试默认访问 `127.0.0.1` 的 13579 / 12345 / 8095 / 8123。单独调试 pytest 可设置 `OPENREC_TEST_SERVER_URL`、`OPENREC_TEST_WEB_URL`、`OPENREC_TEST_CONSOLE_URL`、`OPENREC_TEST_RANK_URL`，但 runner 的 smoke / 发行脚本仍要求本机 Docker 和标准端口；不宣称支持远程完整部署。

```bash
# 逐条调试，绕过 runner；必须自己保证环境互斥和正确模式
.venv/bin/python -m pytest integration/test_ui.py --mode standalone \
  --artifacts reports/debug --junitxml reports/debug.xml -k detail -vv

# 不连接任何服务的框架自测
python3 -m unittest discover -s tests -v
.venv/bin/python -m pytest integration --mode cluster --artifacts /tmp/collection --collect-only -q
```

默认 `pytest` 只发现 `tests/`，不会意外连接服务。GitHub Actions 验证框架单测、CLI 计划和两种模式的用例收集；完整集成测试需在有整个 workspace 与平台资源的专用执行机运行，不在普通 PR runner 启动大数据集群。

新增框架实跑记录见 [VALIDATION_FRAMEWORK.md](VALIDATION_FRAMEWORK.md)，原有记录见 [VALIDATION.md](VALIDATION.md)。功能测试启动可显式使用 `RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000`；这不是默认延迟/性能 SLA 验收，框架不静默修改服务预算。
