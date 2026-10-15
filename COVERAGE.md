# Functional coverage matrix

The following lists implemented test entry points, not a claim that every case has passed. `S/C` means both modes; cluster-only features are explicitly skipped in standalone. Refer to execution reports for actual results.

| Feature | Mode | Automated test entry point |
| --- | --- | --- |
| Service mode, health, readiness, OpenAPI | S/C | smoke, api health / capabilities |
| Successful diagnostics for all recall channels, cluster ranking, standalone bypass | S/C | smoke |
| Rank Engine item/user scores and invalid target validation | C | api rank_engine_scores_both_targets |
| rec-server / rank-engine Prometheus metrics | S/C | api prometheus_metrics |
| Recommendation compatibility / item routes, size, deduplication, rankScore | S/C | api recommendation_routes |
| User creation/query/update/deletion and console consistency | S/C | user fixture, api user_update |
| Item creation/query/update/deletion and console consistency | S/C | api item_lifecycle |
| Event ingestion and projection, console event queries | S/C | api event_push |
| Demo guess/hot/new/related and entity resolution | S/C | demo tabs |
| click/collect/buy/stay/expose/dislike, batch exposure, invalid input, reset | S/C | demo feedback |
| Demo browser tab/scene/user switching, refresh, collection/purchase, related recommendations in details and return navigation | S/C | ui demo_navigation/action/detail |
| Persistence of demo browser exposure and stay events | S/C | ui demo_browser_exposure_and_stay |
| Three demo negative feedback types and clearing negative feedback/exposure | S/C | ui demo_dislikes |
| Disabled standalone console menus and API 404 responses | S/C | api capabilities, ui mode_navigation |
| Console USER/ITEM/EVENT queries | S/C | ui entity_search |
| Serving Graph experiment creation, node editing, publishing, rollback, invalid timeouts, deletion | S/C | api graph_publish, ui graph_editor |
| Experiment enable/disable, runtime list, UI deletion | S/C | api graph_experiment_enable_disable, ui experiment_controls |
| AB routing changes and restoration | S/C | api graph_routing_roundtrip |
| Global blacklist filtering and positive control after restoration | S/C | api global_blacklist (local Docker Redis) |
| Loading actual Grafana panels | S/C | ui monitor_dashboard |
| ITEM / USER DAG configuration editing, publishing, rollback, invalid cron | C | api dag_config, ui dag_edit |
| Recall version listing, switching, rollback, original alias restoration | C | api recall_catalog/switch, ui recall_switch |
| Airflow DAGs, runs, tasks, log display, pause/resume | C | api airflow, ui airflow_task_logs/pause_resume |
| Analytics date validation, queries, forced refresh | C | api analytics_validation, ui analytics_query_refresh |
| Model feature center, runtime, versions, training list, parameter validation | C | api model_catalog, ui model_tabs |
| LR / FM / LightGBM UI training, job success, artifact summaries, gate checks, no automatic deployment after training | C | models-ui (4 users x 4 items in a dedicated scene) |
| UI deployment of all three model types, recommendation ranking scores, rollback, feature configuration copying, original model restoration | C | models-ui |
| Delete propagation: Redis / HBase / Hive | C | entity-delete -> example_cluster/verify_entity_delete.sh |
| Online/offline feature parity | C | feature-parity -> verify_feature_parity_e2e.sh |
| Statistics and business summaries | C | analytics -> verify_data_analytics.sh |
| Daily recall jobs, publishing, activation | C | daily-recall -> verify_daily_recall.sh |
| Ranking training, publishing, rollback | C | rank-model -> verify_rank_model.sh |

## Coverage gaps

- Independent USER recommendation warmup, the full USER recall lifecycle, and USER ranking model training. Current tests cover only USER DAG configuration and the model target selection page.
- End-to-end recommendation differences when switching AB experiments in the Web demo. Experiment activation and Runtime listing failures were fixed and their existing API/UI tests pass; recommendation-effect comparisons between variants remain uncovered.
- Boundary behavior at the 50% viewport threshold, stay-duration precision, and exact exclusion of candidates after negative feedback. Current tests cover browser event reporting and persistence, but do not establish all recommendation effects.
- Manual trigger buttons for arbitrary DAGs. Actual job execution is covered by distribution scripts and model training.
- Failure recovery, restart persistence, concurrency, authorization/security, load and latency SLAs, mobile devices, and multiple browsers. Current browser scenarios use desktop Chromium.
- The full Spark/Flink engine matrix. Tests use the current distribution scripts' default engine.

When adding features, update this matrix, the corresponding tests, and execution records for both modes. Page loading, HTTP 200, empty results, or mode-specific skips are not substitutes for functional success.
