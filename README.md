# OpenRec 自动化回归

跨仓库产品回归的统一入口。`example/release/openrec.json` 仍是分发兼容性依据，
部署和现有验收脚本由 `example` 维护；这里负责测试编排、公共在线断言、执行日志和报告。
组件单元测试继续留在各自仓库，不复制部署配置或生成的运行目录。

完整实际验收结果、修复及限制见 [VALIDATION.md](VALIDATION.md)。

## 快速使用

需要 Linux、Python 3.10+（无第三方 Python 依赖）、Docker 访问权限；完整启动还需要
example 文档所列的 Java 21、Maven 和平台依赖。各组件仓库须与 auto-test 位于同一工作区。
从 `auto-test` 执行：

```bash
# 查看计划，不访问服务、不写测试数据
python3 run.py cluster --suite full --dry-run

# 在已启动且完成初始化的 cluster 上运行在线冒烟
python3 run.py cluster

# cluster 全量业务回归（不重新部署）
python3 run.py cluster --suite full

# 从构建、启动、Airflow bootstrap 开始的 cluster 全量回归
python3 run.py cluster --start --local --suite full

# 只运行冒烟和选定用例；--case 可重复，按指定顺序执行
python3 run.py cluster --case entity-delete --case feature-parity

# standalone 完整启动验收和在线冒烟
python3 run.py standalone --start --suite full

# 已初始化的 standalone 只运行在线冒烟
python3 run.py standalone
```

`--workspace /path/to/openrec` 指定其他工作区。每个用例默认限时 7200 秒，
可用 `--timeout` 调整。执行遇到失败即停止后续用例，并将其标为 skipped；退出码非零。
测试顺序是 smoke → 实体删除 → 特征一致性 → 分析指标 → 每日召回 → 模型生命周期。
召回、模型测试放在后面，因为它们会改变在线发布状态。

## 覆盖范围

| 模式 / 用例 | 检查内容 | 实现来源 |
|---|---|---|
| 两种模式 / smoke | 服务模式、health、ready、六路召回诊断、非空结果 | `checks.py` |
| cluster / smoke | 每个结果都有有限数值 rankScore；Push → Redis 新用户写入和删除 | `checks.py` |
| standalone / smoke | 不产生 rankScore；直接写入 Redis 的用户增删 | `checks.py` |
| cluster / startup | 基础设施、数据初始化、Airflow bootstrap、推荐链路 | `example_cluster/start.sh` |
| cluster / entity-delete | Kafka 删除消息、Redis 删除及 new 清理、Hive tombstone | `verify_entity_delete.sh` |
| cluster / feature-parity | Kafka 乱序 INSERT/DELETE、在线特征与离线 golden 对齐 | `verify_feature_parity_e2e.sh` |
| cluster / analytics | Push → Kafka → Spark → Hive → console 的 CTR/CVR/GMV 等 | `verify_data_analytics.sh` |
| cluster / daily-recall | Hive 累计数据、Airflow 召回、ES alias 激活、在线召回 | `verify_daily_recall.sh` |
| cluster / rank-model | LR/FM 训练、推理停止时训练、显式发布、评分、回滚和重启保留 | `verify_rank_model.sh` |
| standalone / startup | 构建、数据导入、预热、六路召回、Rank/Kafka bypass、Web 启动 | `example_standalone/start.sh` |

cluster 的五个业务用例调用 workspace 中 `example/example_cluster/` 的原始脚本。
standalone full 必须带 `--start`，避免将单纯冒烟误报为完整验收。
尚未覆盖浏览器交互、用户召回全部发布链路、Flink/Spark 双引擎矩阵、故障恢复和性能压测。
smoke 的模式检查与 Push 投影检查不能单独证明每一个内部节点被执行；完整链路由相关
业务用例及 bootstrap 补充验证。

## 数据、模式与执行约束

- 仅在 OpenRec 开发/验收环境运行。现有脚本使用 localhost、固定容器名和默认示例配置。
- cluster 与 standalone 共用端口、存储和容器名，必须串行。执行器通过本仓库锁防止重复
  启动；手动脚本、其他 checkout 和 CI 主机仍需自行协调。
- 默认连接现有环境，不部署或停止服务。`--start` 执行 example 的完整启动流程，可能切换
  当前模式、导入 fixture 或在失败时按原脚本清理应用。运行后保留环境供诊断。
- smoke 清理示例用户的 expose key，并通过 Push API 删除本次创建的唯一用户；Hive/Kafka
  历史会保留。其他脚本会写入 fixture、切换召回 alias、发布模型、重启 rank-engine；模型
  验收最终保留其测试 LR 版本，不自动恢复测试前模型。不要并行处理真实业务数据。
- 超时会向子进程组发 TERM，给清理 trap 15 秒，再强制终止。已提交到 Airflow/Spark 的远端
  作业不会因此自动取消，应根据日志检查；硬中断也可能留下测试状态。
- 业务日期使用脚本默认的当前 UTC 日期。分析和模型脚本会实时写入数据，不统一传历史
  日期，避免写入今天分区却验证历史分区。
- 不自动放宽在线超时。若需要复现 example 的功能 CI 配置，可在 `--start` 时显式设置
  `RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000`；这不是性能测试。

## 报告与维护

每次执行生成 `reports/<UTC时间>-<模式>-<唯一ID>/`：

- 每个用例一份 `.log`，保存完整 stdout/stderr；可用 `tail -f` 查看进度。
- `results.json`：命令、状态、耗时、日志路径、release manifest、组件实际 HEAD 和工作区状态。
- 新报告同时记录显式传入的请求/节点功能验收预算；未设置时记录 null，不推断当前服务配置。
- `junit.xml`：供 CI 归档和展示，失败和未执行项分别记录。

HEAD 与 manifest 可以不同，因为本地回归需要测试未发布修改；报告会同时记录二者，不自动
checkout 或修改分发 refs。dirty 状态只记录文件清单，不是源码快照；精确复现需保存相应改动。
报告包含业务响应，按测试数据的权限管理，不提交到 Git。

本仓库执行器及断言的离线测试：

```bash
python3 -m unittest discover -s tests -v
```

CI 可先调用上述离线测试，再在专用 OpenRec runner 上调用对应的 `run.py` 命令，始终归档
`reports/`。本仓库暂不自动安装或清理宿主机基础设施，也不自动创建定时任务。
