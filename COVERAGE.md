# 功能覆盖矩阵

以下是已实现测试入口，不等同于所有用例已通过。`S/C` 表示两模式均运行；cluster 专属在 standalone 中显式 skipped。完整实跑结果以报告为准。

| 功能 | 模式 | 自动测试入口 |
| --- | --- | --- |
| 服务模式、健康、ready、OpenAPI | S/C | smoke、api health / capabilities |
| 全召回通道成功诊断、cluster 排序、standalone bypass | S/C | smoke |
| Rank Engine item/user 分数及非法 target 校验 | C | api rank_engine_scores_both_targets |
| rec-server / rank-engine Prometheus 指标 | S/C | api prometheus_metrics |
| 推荐兼容路由 / item 路由、size、去重、rankScore | S/C | api recommendation_routes |
| 用户新增/查询/更新/删除、控制台一致性 | S/C | user fixture、api user_update |
| 物品新增/查询/更新/删除、控制台一致性 | S/C | api item_lifecycle |
| 事件写入与投影、控制台事件查询 | S/C | api event_push |
| demo guess/hot/new/related、实体解析 | S/C | demo tabs |
| click/collect/buy/stay/expose/dislike、批量曝光、错误输入、reset | S/C | demo feedback |
| demo 真实切换 tab/scene/user、刷新、收藏/购买、详情相关推荐返回 | S/C | ui demo_navigation/action/detail |
| demo 浏览器曝光与停留事件持久化 | S/C | ui demo_browser_exposure_and_stay |
| demo 三种负反馈及清除负反馈/曝光 | S/C | ui demo_dislikes |
| standalone 控制台菜单禁用及 API 404 | S/C | api capabilities、ui mode_navigation |
| 控制台 USER/ITEM/EVENT 查询 | S/C | ui entity_search |
| Serving Graph 实验创建、修改节点、发布、回滚、非法超时、删除 | S/C | api graph_publish、ui graph_editor |
| 实验启用/停用、运行时列表、UI 删除 | S/C | api graph_experiment_enable_disable、ui experiment_controls |
| AB routing 修改和恢复 | S/C | api graph_routing_roundtrip |
| 全局黑名单过滤及恢复后的正向对照 | S/C | api global_blacklist（本机 Docker Redis） |
| Grafana 真实面板加载 | S/C | ui monitor_dashboard |
| ITEM / USER DAG 配置修改、发布、回滚、非法 cron | C | api dag_config、ui dag_edit |
| 召回版本列表、切换、回滚、恢复原别名 | C | api recall_catalog/switch、ui recall_switch |
| Airflow DAG、run、task、日志展示、暂停/恢复 | C | api airflow、ui airflow_task_logs/pause_resume |
| 分析日期校验、查询、强制刷新 | C | api analytics_validation、ui analytics_query_refresh |
| 模型特征中心、runtime、版本、训练列表、参数校验 | C | api model_catalog、ui model_tabs |
| LR / FM / LightGBM UI 训练、任务成功、产物摘要、gate、训练不自动发布 | C | models-ui（独立 scene 的 4 用户 × 4 物品 fixture） |
| UI 部署三类模型、推荐排序分数、回滚、复制特征配置、恢复原模型 | C | models-ui |
| 删除传播：Redis / HBase / Hive | C | entity-delete → example_cluster/verify_entity_delete.sh |
| 在线离线特征一致性 | C | feature-parity → verify_feature_parity_e2e.sh |
| 统计与业务汇总 | C | analytics → verify_data_analytics.sh |
| 日召回任务、发布、激活 | C | daily-recall → verify_daily_recall.sh |
| 排序训练、发布和回滚 | C | rank-model → verify_rank_model.sh |

## 尚未完整覆盖的边界

- USER 推荐独立预热、USER 召回全生命周期及 USER 排序模型训练；现在仅测试 USER DAG 配置和模型目标切换页面。
- Web demo AB 实验切换的端到端推荐差异；实验启用接口失败时必须先修复产品，再验证这些链路。
- 浏览器 viewport 50% 阈值的边界、停留秒数精度、负反馈后候选实际排除的精确断言；当前覆盖浏览器事件上报和持久化，尚不能证明全部推荐效果。
- 任意 DAG 手动触发按钮；实际任务执行由发行脚本和模型训练覆盖。
- 各种故障恢复、重启持久化、并发、权限安全、负载和延迟 SLA、移动端、多浏览器；当前 Chromium 桌面场景。
- Spark/Flink 双引擎全矩阵；沿用当前发行脚本默认引擎。

新增功能时应同时更新本表、对应测试和两模式实跑记录。不要用页面能打开、HTTP 200、空结果或模式 skip 替代功能成功。
