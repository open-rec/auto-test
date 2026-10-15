# auto-test 发现问题的产品修复验证（2026-10-15）

这是 [框架首次实跑](VALIDATION_FRAMEWORK.md) 之后的产品修复记录；保留此前失败，不覆盖历史报告。

## 修复范围

- **rec-server**：Serving Graph 未指定实验时返回目标对应的实验列表和路由，保留显式实验查询；事件扩展字段兼容 JSON 字符串并正确处理 Jackson 3 转换异常；item/user 排名失败或超时后保留召回候选，独立复制排名输入以防迟到结果污染降级数据。超时 trace 不会被伪装成成功。
- **rank-engine**：过期特征快照在后台刷新，每目标至多一个任务，失败重试按刷新间隔节流；评分继续读取可用快照，显式刷新 API 仍同步；清理模型后不激活已失效的刷新结果。
- **rec-algorithm**：训练 JSON 读取关闭 Pandas 日期和数值类型猜测，保留时间数值、大数值特征及字符串 ID，避免训练读取阶段溢出。原有 `jobs/spark/point_in_time.py` 及其测试修改保留，本次未改动它们。
- **example**：实体删除验收完整消费 HDFS 输出，避免 `pipefail` 配合 `grep -q` 提前退出导致 SIGPIPE 误判。

本次没有变更端口、Kafka envelope 或对外请求 schema，也没有降低 auto-test 断言来消除产品失败。

## 单测

- rec-server：20 项定向测试通过，含 `EventItemContextEnricherTest`、`ServingGraphServiceTest`、`AbExperimentServiceTest`、`RankScoreFusionTest`、`RankFallbackTest`。
  使用 Java 21、Maven 3.9.9，在 `/tmp/openrec-product-fix-tests` 源码副本中构建，避开原有不可写构建产物；Maven 缓存使用工作区 `.cache/maven-repository-127058`。命令：
  ```sh
  mvn -o -Dmaven.repo.local=/home/xsank.mz/openrec/.cache/maven-repository-127058 \
    -pl server -am test \
    -Dtest=EventItemContextEnricherTest,ServingGraphServiceTest,AbExperimentServiceTest,RankScoreFusionTest,RankFallbackTest \
    -Dsurefire.failIfNoSpecifiedTests=false
  ```
- rank-engine：现有 `openrec/rank-engine:latest` 镜像只读挂载源码，`python -m pytest -q -p no:cacheprovider test`，54 项通过。
- rec-algorithm：同样使用现有镜像，`python -m pytest -q -p no:cacheprovider test/algorithm/rank/test_training.py`，12 项通过。
- Java formatter 和各仓库 `git diff --check` 通过。

## 运行方式与历史失败

```sh
python run.py standalone --restart --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
python run.py cluster --restart --local --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
python run.py cluster --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
```

实际 Python 为 `/tmp/openrec-regression-venv/bin/python`。cluster 已通过 `start.sh --local` 从本地源码重新构建启动；训练读取问题修复后，仅重建算法 runner，沿用 `--local` 的训练基础镜像和 PyPI 参数。本轮没有额外设置推荐超时环境变量；保留已有持久化业务状态，因此不是纯净 release tag 或延迟 SLA 验收。

- `reports/20261015T070606Z-cluster-e745246f/`：重建启动与 smoke 通过；实体删除验收误报失败。只读检查确认本轮 DELETE 已存在于 HDFS，随后修正脚本。
- `reports/20261015T072736Z-cluster-85633179/`：实体删除、特征一致性、分析、每日召回通过；模型训练因 Pandas 转换溢出失败，随后修正训练读取。

## 修复后结果

- [standalone 报告](reports/20261015T065910Z-standalone-b226484e/index.html)：41 项集成测试通过、22 项 cluster 专属测试跳过；启动和 smoke 通过。
- [cluster 全量报告](reports/20261015T073737Z-cluster-850951b5/index.html)：smoke、实体删除、特征一致性、数据分析、每日召回和模型生命周期脚本全部通过；26 项 API、12 项 demo API、24 项常规 UI 全部通过。最后模型 UI 的训练因验证集只有一种标签而失败，产品的 AUC 拒绝行为正确。
- 该模型 UI fixture 的 16 条事件此前使用相同秒级时间戳，时间切分后的验证集依赖随机样本 ID，可能只包含一种标签。改为递增时间戳，使最后四条保持两类标签，不改变产品门槛或测试断言。
- [模型 UI 修复后专项报告](reports/20261015T080449Z-cluster-e6b3bfe0/index.html)：预检、smoke、模型 UI 均通过；真实浏览器完成 LR/FM/LightGBM 训练、逐个部署及在线评分、回滚、复制特征配置，并恢复原活动模型。专项耗时约 166 秒。
- 最终 cluster **63 条已注册集成用例分轮通过**，不是同一份全量报告首次全绿。框架自身 `python -m unittest discover -s tests -v` 的 20 项单测也通过。

cluster 保持运行。以上覆盖范围以当前注册用例为准，未覆盖边界仍见 [COVERAGE.md](COVERAGE.md)。

## 再次完整回归（2026-10-15）

本轮按要求重新构建两种模式，cluster 启动使用 `--local`。首次 standalone 完整通过：[报告](reports/20261015T082036Z-standalone-b331a09b/index.html)。

cluster 重启暴露了重复验收后的状态依赖：`user_0` 最近十条点击全部是历次 `daily_accept_*` 测试物品，而重新初始化的样例召回索引没有这些物品，导致 item-CF、content、item-sequence embedding 三个通道无候选。

- [首次 cluster 重启报告](reports/20261015T082522Z-cluster-2e24c2a1/index.html)：Airflow 启动验收失败，后续用例正确跳过。
- 修正 `example_cluster/airflow/dags/openrec_cluster_bootstrap.py`，在预热和 smoke 请求中显式给出样例触发物品，不依赖可变的最近点击历史；保留六个必需通道及真实排名断言。
- [再次重启报告](reports/20261015T084545Z-cluster-d762e191/index.html)：`start.sh --local` 通过，随后 auto-test smoke 暴露相同历史依赖。同步修正 `checks.py`：从样例 CSV 选择同时存在于 item-CF、content、embedding 表的触发物品，显式传入推荐请求；不修改已有历史、不降低断言。
- example 召回验收测试 4 项通过（含本地 model 样例一致性验证）；auto-test 框架测试 21 项通过。

修正后的 [cluster 完整报告](reports/20261015T090156Z-cluster-fb20600a/index.html) 在**同一次 full 执行中全部通过**：

| 检查组 | 结果 |
| --- | --- |
| preflight / smoke | 通过 |
| 实体删除 / 特征一致性 / 数据分析 | 通过 |
| 每日召回端到端 | 通过 |
| LR/FM 训练、发布、回滚、重启恢复 | 通过 |
| API | 26 通过 |
| Web demo API | 12 通过 |
| 常规浏览器 UI | 24 通过 |
| LR/FM/LightGBM 模型 UI 全流程 | 1 通过 |

cluster 共 **63 项集成测试通过，0 失败、0 跳过**，并通过上述 5 项端到端脚本。这次不再是多轮专项结果的拼合。

共用 smoke 修正后，再次从源码重建并运行 standalone full：[最终 standalone 报告](reports/20261015T092111Z-standalone-9c40f614/index.html)。启动、预热、smoke 全通过；API 12 通过 / 14 跳过，demo API 12 通过，浏览器 UI 17 通过 / 7 跳过，模型 UI 1 跳过。合计 **41 通过、22 模式不适用跳过、0 失败**。

本轮最终结论：当前修复源码下，两种模式各自完整 full 回归通过。最后运行的是 standalone，因此 standalone 应用保持运行；cluster 应用已由模式切换正常停止，共享平台和数据卷保留。

## 所有修改完成后的再次整轮确认（2026-10-15）

按要求，在上述 example 和 auto-test 修改完成后，不再调整代码，重新从源码构建并完整回归两种模式：

| 模式 | 执行 | 最终结果 |
| --- | --- | --- |
| standalone | `run.py standalone --restart --suite full --continue-on-failure --browser-path /tmp/openrec-regression-browsers` | 启动、smoke 通过；41 项通过、22 项模式不适用跳过、0 失败 |
| cluster | `run.py cluster --restart --local --suite full --continue-on-failure --browser-path /tmp/openrec-regression-browsers` | 从启动到全部 full 用例同轮通过；63 项通过、0 跳过、0 失败；5 项端到端脚本全部通过 |

- [standalone 最终整轮报告](reports/20261015T092942Z-standalone-de3a1147/index.html)
- [cluster 最终整轮报告](reports/20261015T093542Z-cluster-5da43dc4/index.html)

本轮无新问题、无新增产品或测试代码修改。执行前后核对 rec-server、rank-engine、rec-algorithm、rec-console、example、auto-test 的 Git HEAD 和全部工作区改动文件 SHA-256，结果完全一致；指纹保存在两个报告目录的 `source-fingerprints.json`。验证完成后仅追加本节记录。

当前 cluster 保持运行，standalone 应用已随模式切换正常停止，共享数据卷保留。
