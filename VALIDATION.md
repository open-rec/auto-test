> 新增 API / 浏览器框架的实跑记录见 [VALIDATION_FRAMEWORK.md](VALIDATION_FRAMEWORK.md)。以下为原有验收记录。

# 完整功能验收记录

日期：2026-10-15。结论：本仓库当前列出的 cluster 与 standalone 功能验收均已实际执行通过。
cluster 启动与修复后的业务回归分两次报告记录，并非一次从头到尾无失败。

本次使用现有 workspace 源码和持久化数据卷；不是清空数据后的全新安装，也不是性能验收。
两种模式采用 example 已有 CI 功能配置：请求预算 10000 ms，节点预算下限 1000 ms。
默认时延与模型恢复后的冷请求问题仍需单独处理，详见下文。

## 最终结果

| 模式 | 用例 | 结果 | 耗时 |
|---|---|---|---:|
| cluster | 完整构建启动、平台检查、Airflow bootstrap、Web | 通过 | 316.7 s |
| cluster | 在线推荐、六路召回、评分、用户增删 | 通过 | 5.5 s |
| cluster | 实体删除、Redis 清理、Hive tombstone | 通过 | 7.1 s |
| cluster | Kafka → Spark → Redis 特征一致性 | 通过 | 3.6 s |
| cluster | 分析指标链路 | 通过 | 17.3 s |
| cluster | 七种召回发布、alias 切换、在线验证 | 通过 | 149.9 s |
| cluster | LR/FM 训练、发布、评分、回滚、重启恢复 | 通过 | 112.3 s |
| standalone | 构建、导入、预热、召回、Rank/Kafka bypass、Web | 通过 | 179.0 s |
| standalone | 在线推荐、无 rankScore、用户增删 | 通过 | 1.4 s |

最终业务回归报告均无失败或跳过项：

- cluster 启动证据：`reports/20261015T034913Z-cluster-25391ad5/startup.log`。
  该轮后续 rank-model 失败，见下面的已修复问题；保留原始失败报告。
- 修复后 cluster 六项全部通过：`reports/20261015T043434Z-cluster-f2ec4f1a/`。
- standalone 两项全部通过：`reports/20261015T044500Z-standalone-2e911b23/`。

各目录含逐项日志、`results.json` 和 `junit.xml`。组件 HEAD、dirty 文件清单、分发 manifest
见 JSON。报告不提交 Git，上述路径仅存在于本次执行机器。

模型验收生成 LR `20261015-r0437381`、FM `20261015-r0437382`，最终回滚到前者，
并确认 rank-engine 重启后恢复该版本。训练时 inference 停止，训练完成前在线版本未改变。

## 实际执行命令

以下命令从 `auto-test/` 执行，按实际先后顺序列出关键步骤：

```bash
# 先停止已有 cluster 应用，保留平台和数据，再完整启动验收
bash ../example/example_cluster/stop.sh --keep-platform
RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000 \
  python3 run.py cluster --start --local --suite full

# 修复并重建算法 runner 后，使用已有预热流程恢复评分服务
python3 ../example/scripts/wait-recommendation-ready.py --user-id user_0
RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000 \
  python3 run.py cluster --suite full

# 自动停止 cluster 应用并切换模式，保留共享平台和数据
RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000 \
  python3 run.py standalone --start --suite full

python3 -m unittest discover -s tests -v
```

执行器及断言的 8 项离线测试通过；修改仓库的 `git diff --check` 通过。
这不是对所有组件单元测试套件的全量执行，启动构建沿用 example 的 `-DskipTests`。

## 验收发现并修复的问题

`rec-algorithm/jobs/spark/point_in_time.py` 对事件 value 使用严格 double cast。
历史事件包含 `{"id":"item_1779"}` 这样的 JSON 值，Spark 4 ANSI 模式下抛出
`CAST_INVALID_INPUT`，使训练样本生成失败。

修复：行为聚合及停留偏好计算使用 `try_cast`，无效值按 0 处理，与已有在线 Java 和本地
Python 事件特征约定一致，不删除事件或改变事件计数。无需协议/特征名称迁移。
需要伴随部署更新后的 rec-algorithm runner 镜像；本次已从源码重建并部署。
没有手改容器源码或生成的 runtime 副本，也没有修改 release manifest 的已发布 refs。

新增 `rec-algorithm/test/jobs/spark/test_point_in_time_values.py`，在真实 Spark 4.0.4、ANSI
开启条件下验证 JSON、null、空字符串、正负数、计数、求和、均值、窗口聚合和偏好计算，测试通过。
实际命令（从 workspace 执行）：

```bash
docker run --rm --network none \
  -v /home/xsank.mz/openrec/rec-algorithm:/source:ro \
  -e PYTHONPATH=/source -e SPARK_LOCAL_IP=127.0.0.1 \
  --entrypoint /opt/spark/bin/spark-submit openrec/spark:4.0.4 \
  --master 'local[2]' /source/test/jobs/spark/test_point_in_time_values.py
```

训练失败证据保留在 `reports/20261015T034913Z-cluster-25391ad5/rank-model.log`。
修复后已重跑六项 cluster 业务回归，涵盖训练消费者及分发级验证。

## 保留的失败与验收边界

- 初次接入成功记录：`reports/20261015T032709Z-cluster-7f3f57df/`。
- 默认预算下的推荐失败：`reports/20261015T032504Z-cluster-581c684d/`、
  `reports/20261015T034522Z-cluster-1baea148/`。后者已从服务日志确认 Rank 超过 200 ms，
  operation/collector 缺少输入，导致 HTTP 500。
- 模型训练失败后恢复 inference，首次请求在 1000 ms Rank 预算下仍超时：
  `reports/20261015T043124Z-cluster-cf8c6469/`。已有 `/ready` 状态并未阻止该冷请求失败。
  显式执行预热、连续三轮验证成功后，完整业务验收通过；未添加重试来覆盖失败记录。
- 因此，本次证明预热后的功能链路通过，不证明默认时延达标，也不证明模型重启后的冷请求
  稳定性已修复。默认预算性能与模型生命周期 readiness 联动仍需后续专项工作。
- 浏览器交互、全部用户召回发布、Flink/Spark 双引擎矩阵、压力和故障恢复不在当前套件范围内。

## 最终环境与改动

验收结束后保留 standalone 运行（API 13579、console 8095、Web 12345）。cluster 应用及
Spark 流处理任务已由模式切换脚本停止；共享平台和持久化数据卷保留，没有执行 down -v。

源码改动位于 auto-test（执行器、检查、测试、报告说明）及 rec-algorithm（上述修复与回归测试）。
改动尚未提交或推送；发布时需要将 rec-algorithm 修复提交纳入分发兼容 refs。
