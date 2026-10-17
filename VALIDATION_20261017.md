# OpenRec 重构验收记录（2026-10-17）

## 结论与执行方式

两种模式的完整功能回归均通过。Standalone 为 42 通过、23 项模式不适用跳过；
cluster 为 65 通过、0 失败、0 跳过，五项分发级端到端检查全部通过。

- [Standalone 完整报告](reports/20261017T144443Z-standalone-80e59291/index.html)
- [Cluster 完整报告](reports/20261017T154645Z-cluster-27d4ed70/index.html)
- [补充测试、部署信息和源码指纹](reports/20261017T154645Z-cluster-27d4ed70/supplemental/)

上述 `reports/` 链接指向验收主机的本地输出，该目录按仓库规则不提交到 Git；
从远端克隆仓库后只能查看本文中的结果摘要，不能直接访问这些本地报告。

Standalone 通过源码构建启动。Cluster 源码镜像重建在排序依赖下载阶段被用户要求停止；
最终使用本地现有镜像，加上最新 Java JAR、Python 源码、算法分发 ZIP 和控制台前端产物，
通过 `docker compose ... up -d --no-build --pull never --wait` 启动。
因此 cluster 结果是当前代码在现有镜像依赖环境中的回归，不是新 Dockerfile 全量构建验收。
未放宽推荐 deadline 或图节点超时。

最终命令（Python 为 `/tmp/openrec-regression-venv/bin/python`）：

```bash
python run.py standalone --restart --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
python run.py cluster --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
```

验收期间工作区收到其他开发提交，报告记录了各轮实际版本。最终部署挂载和镜像 ID 见
`supplemental/deployment.txt`，源码指纹见 `supplemental/source-state.json`。
最终检查时又收到 `example` 提交 `84b7bf2`，将 standalone 启动的 I2I 数据检查改为查询
Elasticsearch。已补验 shell 语法及分发测试；此前 standalone 完整启动报告不覆盖这一条
后续启动检查变更，未为此重新构建镜像或重启 standalone。

## 修复与迁移

1. Standalone 新增 USER 搜索启动检查，但原预热只覆盖 ITEM，导致 USER 返回 503。
   启动客户端现在可一次预热两个 target；standalone 和 cluster bootstrap 均使用两路预热。
2. USER 预热进一步发现 standalone 未初始化 U2U 召回数据，缺失
   `openrec-recall-user-als-u2u-active`。初始化器现导入 UserCF、content 和 ALS 三类 U2U 表，
   使用已有 `scene,left_user,right_user,score` 契约。
3. 测试环境持久化 ITEM DAG 仍为 `sparse`，USER DAG 仍为 `user_emb_u2u` 且缺少文本搜索。
   已通过控制台配置存储发布迁移版本：分别改为 `text_search`、`user_als_u2u`，并补入 USER
   `text_search`。保留历史版本，停止本轮遗留旧 run 后请求重新解析。
   迁移记录见 `supplemental/dag-migration.log`。
4. 按用户要求，Airflow 发布任务统一为 **`publish-<tableName>`**，以 rec-server 的
   `item_graph.json`、`user_graph.json` 为权威。例如 `publish-item-search`、
   `publish-user-search`、`publish-item-seq-emb`、`publish-user-als-u2u`。
   内部算法参数仍使用下划线；任务名不再直接取内部算法名。历史 run 的任务名不改写。
5. auto-test 新增 USER 文本推荐检查，验证候选非空、大小、去重及 `user_search` 成功诊断。
   分发测试逐项对照 graph 校验 Airflow 任务名。更新了覆盖说明和过时的 model 变更说明。

两次 standalone 阻断报告和一次旧 DAG 阻断报告均保留，没有修改断言隐藏失败。
Java 首轮测试还发现旧节点数量断言；工作区随后已有修复提交 `6474bf3`，按该提交复测通过。

## 功能结果

| 检查 | Standalone | Cluster |
|---|---:|---:|
| API | 13 通过，15 跳过 | 28 通过 |
| Demo API | 12 通过 | 12 通过 |
| Chromium UI | 17 通过，7 跳过 | 24 通过 |
| LR/FM/LightGBM 模型 UI 全流程 | 1 跳过 | 1 通过 |
| 删除传播、特征一致性、统计分析、每日召回、模型生命周期 | 不适用 | 5 项全部通过 |

Cluster 模型生命周期包含推理停止时离线训练、显式发布、在线评分、原子回滚与重启恢复。
模型 UI 包含三种模型的训练、部署、评分、回滚、复制特征配置及原模型恢复。

## 补充验证

| 组件 / 检查 | 结果 |
|---|---:|
| auto-test 框架 unittest | 21 通过 |
| example 分发与跨组件契约 unittest | 42 通过 |
| rec-server Maven 全套（含 graph/proto/contrib） | 169 通过，3 跳过 |
| data-processor Maven 全套（含 Spark/Flink） | 31 通过 |
| rank-engine pytest | 132 通过，10 跳过 |
| rec-console pytest | 74 通过，1 跳过 |
| rec-algorithm pytest | 148 通过，2 跳过 |
| example 初始化器测试（含真实 ES 原子替换） | 8 通过 |
| 文本向量召回真实 vLLM + ES 集成测试 | 1 通过（默认 Maven 套件中跳过的可选用例） |
| 控制台 TypeScript / Vite 构建 | 通过 |
| 排序特征与 SDK 字段契约脚本 | 通过 |

Java 测试在独立源码副本中执行，使用 Java 21 和已有 Maven 缓存。Python 测试使用容器内
运行依赖及本地 pytest 工具；算法测试另在一次性容器中安装 gensim，并将模型输出设为临时目录。
初次测试环境缺少依赖、输出目录权限和沙箱限制导致的失败未计作通过，补齐环境后的日志已归档。

在线向量一致性使用本机已有 vLLM 的 `http://127.0.0.1:38123/v1/embeddings`：
8 个样本最小余弦相似度 **0.99999801**，超过 **0.999** 门槛。

## 有限并发测量

完整功能回归结束后，使用 cluster 的 ITEM 推荐接口、12 个候选、debug 诊断和显式触发物品，
每档 100 次请求，共 300 次。所有请求业务成功、完整必需召回通道和有效排序分数均为 100%。

| 并发 | 实测请求/秒 | P50 ms | P95 ms | P99 ms |
|---:|---:|---:|---:|---:|
| 1 | 25.8 | 39.5 | 49.5 | 84.4 |
| 4 | 84.1 | 44.7 | 83.1 | 90.0 |
| 16 | 106.2 | 129.7 | 198.9 | 207.9 |

[逐请求数据](reports/20261017T154645Z-cluster-27d4ed70/supplemental/performance.json)
和测量脚本均已保留。这是共享主机的短测，不是饱和吞吐、长期稳定性或生产 SLA 验收；
没有重构前同环境基线，不能据此量化性能提升。

## 最终环境及边界

Cluster 保持运行，代码和 JAR 挂载使用 `/tmp/openrec-acceptance-20261017`；停止该部署前
不要清理此目录。Standalone 应用已正常停止，共享数据卷保留。
源码启动阶段自动重建了模型产物，这些是生成器输出，未手工编辑或提交。
集群验收保留 Kafka/Hive 历史、版本和训练产物，不能视为全环境事务回滚。

本轮集群使用 Spark 流处理；Flink 仅通过组件测试。没有覆盖完整 GPU/多浏览器矩阵、长期压测、
故障注入、安全验收或 USER 全生命周期训练。详细覆盖边界见 [COVERAGE.md](COVERAGE.md)。
