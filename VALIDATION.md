> See [VALIDATION_FRAMEWORK.md](VALIDATION_FRAMEWORK.md) for execution records of the added API and browser framework. The original acceptance record follows.

# Full functional acceptance record

Date: 2026-10-15. Conclusion: the cluster and standalone functional acceptance checks listed in this repository at the time of this run were executed and passed.
Cluster startup and the functional regression after fixes are recorded in two separate reports, not a single failure-free run from start to finish.

This run used existing workspace sources and persistent volumes. It was neither a fresh installation with cleared data nor a performance acceptance test.
Both modes used example's existing CI functional configuration: a 10000 ms request budget and a minimum node budget of 1000 ms.
Default latency and cold requests after model restoration still require separate work, as described below.

## Final results

| Mode | Check | Result | Duration |
|---|---|---|---:|
| cluster | Full build and startup, platform checks, Airflow bootstrap, Web | Passed | 316.7 s |
| cluster | Online recommendations, six recall channels, scoring, user creation/deletion | Passed | 5.5 s |
| cluster | Entity deletion, Redis cleanup, Hive tombstones | Passed | 7.1 s |
| cluster | Kafka -> Spark -> Redis feature parity | Passed | 3.6 s |
| cluster | Analytics metrics pipeline | Passed | 17.3 s |
| cluster | Publishing seven recall types, alias switching, online verification | Passed | 149.9 s |
| cluster | LR/FM training, publishing, scoring, rollback, recovery after restart | Passed | 112.3 s |
| standalone | Build, import, warmup, recall, Rank/Kafka bypass, Web | Passed | 179.0 s |
| standalone | Online recommendations, absent rankScore, user creation/deletion | Passed | 1.4 s |

The final functional regression reports contain no failures or skips:

- Cluster startup evidence: `reports/20261015T034913Z-cluster-25391ad5/startup.log`.
  The later rank-model check in that run failed; see the fixed issue below. The original failure report is retained.
- All six cluster checks passed after the fix: `reports/20261015T043434Z-cluster-f2ec4f1a/`.
- Both standalone checks passed: `reports/20261015T044500Z-standalone-2e911b23/`.

Each directory contains individual logs, `results.json`, and `junit.xml`. The JSON records component HEADs, dirty files, and the distribution manifest.
Reports are not committed to Git; these paths exist only on the execution host used for this run.

Model acceptance produced LR `20261015-r0437381` and FM `20261015-r0437382`, then rolled back to the former and confirmed that rank-engine restored it after restart.
Inference was stopped during training; the online version remained unchanged until training completed.

## Commands executed

The following key steps were run from `auto-test/`, in chronological order:

```bash
# Stop existing cluster applications, retain platform/data, then run full startup acceptance
bash ../example/example_cluster/stop.sh --keep-platform
RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000 \
  python3 run.py cluster --start --local --suite full

# After fixing and rebuilding the algorithm runner, restore scoring through the existing warmup workflow
python3 ../example/scripts/wait-recommendation-ready.py --user-id user_0
RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000 \
  python3 run.py cluster --suite full

# Automatically stop cluster applications and switch modes, retaining shared platform/data
RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000 \
  python3 run.py standalone --start --suite full

python3 -m unittest discover -s tests -v
```

All 8 offline tests for the runner and assertions passed, as did `git diff --check` in the modified repositories.
This was not a full run of every component's unit tests; startup builds retained example's `-DskipTests` setting.

## Issue found and fixed during acceptance

`rec-algorithm/jobs/spark/point_in_time.py` used a strict double cast for event values.
Historical events contained JSON values such as `{"id":"item_1779"}`, which raised `CAST_INVALID_INPUT` under Spark 4 ANSI mode and prevented training sample generation.

The fix uses `try_cast` for behavior aggregation and stay preference calculations, treating invalid values as 0. This matches the existing online Java and local Python event feature contracts without deleting events or changing event counts. No protocol or feature-name migration is required.
The updated rec-algorithm runner image must be deployed alongside the fix; it was rebuilt from source and deployed for this run.
Container sources and generated runtime copies were not edited manually, and published refs in the release manifest were not changed.

The added `rec-algorithm/test/jobs/spark/test_point_in_time_values.py` passed against actual Spark 4.0.4 with ANSI mode enabled. It covers JSON, null, empty strings, positive and negative numbers, counts, sums, means, window aggregation, and preference calculations.
The command was run from the workspace:

```bash
docker run --rm --network none \
  -v /home/xsank.mz/openrec/rec-algorithm:/source:ro \
  -e PYTHONPATH=/source -e SPARK_LOCAL_IP=127.0.0.1 \
  --entrypoint /opt/spark/bin/spark-submit openrec/spark:4.0.4 \
  --master 'local[2]' /source/test/jobs/spark/test_point_in_time_values.py
```

Training failure evidence is retained in `reports/20261015T034913Z-cluster-25391ad5/rank-model.log`.
After the fix, all six cluster functional checks were rerun, covering the training consumer and distribution-level verification.

## Retained failures and acceptance limits

- Initial successful integration record: `reports/20261015T032709Z-cluster-7f3f57df/`.
- Recommendation failures under default budgets: `reports/20261015T032504Z-cluster-581c684d/` and `reports/20261015T034522Z-cluster-1baea148/`. Service logs for the latter confirmed that Rank exceeded 200 ms, leaving operation/collector without inputs and causing HTTP 500.
- After inference was restored following a training failure, the first request still timed out with a 1000 ms Rank budget: `reports/20261015T043124Z-cluster-cf8c6469/`. The existing `/ready` state did not prevent this cold-request failure. Full functional acceptance passed after explicit warmup and three consecutive successful verification rounds. No retries were added to overwrite the failure record.
- These results establish functional success after warmup, not compliance with default latency budgets or a fix for cold-request stability after model restart. Performance under default budgets and readiness coordination with the model lifecycle still require dedicated follow-up work.
- Browser interactions, publishing all user recall types, the Flink/Spark engine matrix, stress testing, and failure recovery were outside the suite's scope at the time of this run.

## Final environment and changes

Standalone was left running after acceptance (API 13579, console 8095, Web 12345). Cluster applications and Spark streaming jobs were stopped by the mode-switching scripts. The shared platform and persistent volumes were retained; down -v was not executed.

Source changes were in auto-test (runner, checks, tests, and report documentation) and rec-algorithm (the fix and regression test above).
At the time of this record, changes had not been committed or pushed. The rec-algorithm fix commit must be included in the distribution's compatible refs when publishing.
