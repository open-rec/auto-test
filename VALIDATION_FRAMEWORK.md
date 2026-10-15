# API / 浏览器框架实跑记录（2026-10-15）

本文件记录 auto-test 扩展的验证，不修改 [此前发行验收记录](VALIDATION.md) 的历史结论。测试发现的产品失败保持失败，不做 xfail，不用自动重试消除失败记录。

后续产品修复及复测结果见 [产品修复验证记录](VALIDATION_FIXES.md)；下文保留首次实跑历史。

## 环境与命令

- Python 3.13、pytest 9.1.1、Playwright 1.64.0 / Chromium；沿用 `/tmp/openrec-regression-venv` 和 `/tmp/openrec-regression-browsers`，本次未重新安装系统依赖。
- 框架单测：`/tmp/openrec-regression-venv/bin/python -m unittest discover -s tests -v`，20 项通过。纯标准库环境会明确跳过 1 项 pytest 插件自测。
- 63 条集成用例可在两模式收集；standalone 的 cluster 专属功能明确 skipped。
- cluster 从本地源码执行 `run.py cluster --restart --local --suite full --continue-on-failure`，显式使用 `RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000`。这不是默认延迟 SLA 验收。
- 本次框架扩展的源码修改限于 auto-test；没有为了让测试变绿修改其他产品仓库。工作区原本已有 rec-algorithm 的 `point_in_time.py` 修复及测试，报告记录了这一 dirty 状态；本次不是完全无改动的 release tag 验收。

## 第一轮 cluster 全量

报告：[cluster HTML 汇总](reports/20261015T051300Z-cluster-1d85c3ff/index.html)。

| 检查 | 结果 |
| --- | --- |
| 依赖预检、停止示例并保留平台、`start.sh --local` | 通过；启动约 310 秒 |
| smoke | 通过 |
| entity-delete / feature-parity / analytics | 全部通过 |
| daily-recall | 通过；约 142 秒 |
| rank-model（LR/FM、离线训练、发布、回滚、重启恢复） | 通过；约 113 秒 |
| 新 API 组 | 23 通过、3 失败 |
| demo API 组 | 5 通过、7 失败 |
| UI 组（初版） | 18 通过、6 失败；其中包含下述已修正的测试问题 |
| models-ui（初版） | 三类训练成功；部署后的推荐断言因复用 fixture 用户而失败，已改用独立用户 |

初版 UI 测试修正：精确匹配 `↻ 强制刷新` 和 `排序目标`；DAG 回滚前发布新基线（回滚选最近发布记录，不是原活动版本）；停留上报通过切换 tab 触发，不假设最后一张卡必定在视口外；模型推荐验证不复用已有曝光历史的 `user_0`。

## smoke 阻断的独立记录

`reports/20261015T060136Z-cluster-7ed440c1/`：预检通过，smoke 失败，两个 UI 组正确 skipped。

rec-server 日志显示 2026-10-15 06:01:40 UTC 的 `rank=TIMED_OUT`，节点预算 1000 ms；operation / collector 随之失败，推荐返回 HTTP 500，而 `/ready` 仍为 READY。不能把这次运行写成通过。后续针对框架修正的直接 pytest 调试结果单独记录，不覆盖这个失败。

## 已确认的产品失败

1. Serving Graph：`GET /api/serving-graph` 返回 `experiments: null`、`routing: null`；发布后的实验启用调用失败，UI 显示 0 个 Runtime 实验，无法找到启用/关闭/删除行。API 与真实浏览器均能捕获。
2. cluster 样例物品反馈：hot 列表中的样例物品（本次如 `item_6689`）触发 click / collect / buy / stay / expose / dislike 返回 `ok: false`，批量曝光同样失败；直接事件 push 也返回 HTTP 500。UI 推荐可能命中新写入的正常物品，因此不能用 UI 中某次成功替代样例数据的 API 验证。
3. rank 节点偶发超过功能测试的 1000 ms 预算，详见独立 smoke 阻断记录。

更多未实现边界见 [COVERAGE.md](COVERAGE.md)。`full` 是当前注册套件的全量运行，不是所有产品功能已通过的声明。

## 修正后的 cluster UI 专项调试

报告：[cluster UI 专项 JUnit](reports/cluster-ui-debug/junit.xml)，共 25 条，22 通过、3 失败。命令为直接 pytest（未绕过后伪称完整 runner 通过）：

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/openrec-regression-browsers /tmp/openrec-regression-venv/bin/python -m pytest \
  integration/test_ui.py integration/test_models_ui.py --mode cluster \
  --artifacts reports/cluster-ui-debug --junitxml reports/cluster-ui-debug/junit.xml -v --tb=short
```

- 两种 DAG UI 配置回滚、召回 UI 切换回滚、分析查询与强制刷新、模型页 target 切换、曝光/停留、Airflow 暂停恢复均通过。
- LR / FM / LightGBM 三类训练、产物 gate/hash、训练不自动发布、三类模型部署和推荐分数、回滚、复制特征、原模型恢复均通过；训练 fixture 的实体清理也通过。
- 一次初始推荐返回空结果、实验 Runtime 列表缺失仍失败。
- 另一条失败是浏览器重置曝光后后台新曝光并发写入导致计数为 1：已修正 UI 断言为删除确认和重新出结果，静止状态归零继续由 demo API 用例验证。这条旧失败报告保留。

## 最终 standalone 全量

命令：

```bash
/tmp/openrec-regression-venv/bin/python run.py standalone --restart --suite full \
  --continue-on-failure --browser-path /tmp/openrec-regression-browsers
```

报告：[HTML 汇总](reports/20261015T062735Z-standalone-109384d4/index.html)、[JSON](reports/20261015T062735Z-standalone-109384d4/results.json)。启动约 161 秒，startup / smoke 通过；此次未显式覆盖服务 deadline 环境变量。

| 功能组 | 通过 | 失败 | 模式跳过 |
| --- | ---: | ---: | ---: |
| API | 10 | 2 | 14 |
| demo API | 12 | 0 | 0 |
| 浏览器 UI | 16 | 1 | 7 |
| 模型 UI | 0 | 0 | 1 |
| 合计 | 38 | 3 | 22 |

三个失败分别为实验启用、routing 读取、UI Runtime 实验列表缺失，均属于前述 Serving Graph 问题。曝光重置、真实曝光/停留和全局黑名单恢复用例已在最终版本通过。cluster 专属用例为 skipped，模型 UI 整组也明确标记 skipped，未算作通过。

测试结束后保留 **standalone 应用运行**，cluster 应用已停止，大数据平台与 volume 保留。没有提交 Git commit 或推送远端。报告目录由 Git 忽略，README、覆盖矩阵、验证说明和框架代码纳入本次改动。
