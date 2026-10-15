# Validation of product fixes identified by auto-test (2026-10-15)

This records product fixes following the [initial framework execution](VALIDATION_FRAMEWORK.md). Earlier failures and historical reports are preserved.

## Fix scope

- **rec-server**: Serving Graph returns experiments and routing for the selected target when no experiment is specified, while retaining explicit experiment queries. Event extension fields accept JSON strings and correctly handle Jackson 3 conversion exceptions. Item/user ranking failures or timeouts preserve recall candidates; ranking inputs are copied independently to prevent late results from corrupting fallback data. Timeout traces remain marked as timeouts.
- **rank-engine**: Expired feature snapshots refresh in the background, with at most one task per target and failed retries throttled by the refresh interval. Scoring continues to use an available snapshot; the explicit refresh API remains synchronous. Stale refresh results are not activated after model cleanup.
- **rec-algorithm**: Training JSON loading disables Pandas date and numeric type inference, preserving numeric timestamps, large numeric features, and string IDs to avoid overflow during loading. Existing changes to `jobs/spark/point_in_time.py` and its tests were retained without further edits in this stage.
- **example**: Entity deletion acceptance consumes the complete HDFS output, avoiding false failures caused by SIGPIPE when `grep -q` exits early under `pipefail`.

No ports, Kafka envelopes, or external request schemas changed. auto-test assertions were not weakened to hide product failures.

## Unit tests

- rec-server: 20 focused tests passed, including `EventItemContextEnricherTest`, `ServingGraphServiceTest`, `AbExperimentServiceTest`, `RankScoreFusionTest`, and `RankFallbackTest`.
  The build used Java 21 and Maven 3.9.9 in a source copy at `/tmp/openrec-product-fix-tests`, avoiding existing unwritable build artifacts. The Maven cache was the workspace's `.cache/maven-repository-127058`. Command:

  ```sh
  mvn -o -Dmaven.repo.local=/home/xsank.mz/openrec/.cache/maven-repository-127058 \
    -pl server -am test \
    -Dtest=EventItemContextEnricherTest,ServingGraphServiceTest,AbExperimentServiceTest,RankScoreFusionTest,RankFallbackTest \
    -Dsurefire.failIfNoSpecifiedTests=false
  ```

- rank-engine: source mounted read-only into the existing `openrec/rank-engine:latest` image; `python -m pytest -q -p no:cacheprovider test`, with 54 passing.
- rec-algorithm: the same existing image; `python -m pytest -q -p no:cacheprovider test/algorithm/rank/test_training.py`, with 12 passing.
- The Java formatter and `git diff --check` in each repository passed.

## Execution and historical failures

```sh
python run.py standalone --restart --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
python run.py cluster --restart --local --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
python run.py cluster --suite full --continue-on-failure \
  --browser-path /tmp/openrec-regression-browsers
```

The Python executable was `/tmp/openrec-regression-venv/bin/python`. Cluster was rebuilt and started from local sources through `start.sh --local`. After the training input fix, only the algorithm runner was rebuilt, retaining the training base image and PyPI settings used with `--local`. No additional recommendation timeout environment variables were set in this round. Existing persistent application state was retained, so this was not acceptance of a clean release tag or a latency SLA.

- `reports/20261015T070606Z-cluster-e745246f/`: rebuild/startup and smoke passed; entity deletion acceptance reported a false failure. Read-only inspection confirmed that this run's DELETE was present in HDFS, after which the script was corrected.
- `reports/20261015T072736Z-cluster-85633179/`: entity deletion, feature parity, analytics, and daily recall passed. Model training failed because of Pandas conversion overflow; training input loading was then corrected.

## Results after fixes

- [Standalone report](reports/20261015T065910Z-standalone-b226484e/index.html): 41 integration tests passed, 22 cluster-only tests skipped; startup and smoke passed.
- [Full cluster report](reports/20261015T073737Z-cluster-850951b5/index.html): smoke, entity deletion, feature parity, analytics, daily recall, and model lifecycle scripts all passed, as did 26 API, 12 demo API, and 24 regular UI tests. Training in the final model UI case failed because the validation set contained only one label class; the product correctly rejected the AUC evaluation.
- The model UI fixture previously assigned the same second-level timestamp to all 16 events. After a time-based split, validation set membership depended on random sample IDs and could contain only one label class. Timestamps were made increasing so the final four events retain both classes, without changing product gates or test assertions.
- [Focused report after the model UI fix](reports/20261015T080449Z-cluster-e6b3bfe0/index.html): preflight, smoke, and model UI passed. A real browser completed LR/FM/LightGBM training, individual deployment and online scoring, rollback, feature configuration copying, and restoration of the original active model. This focused run took about 166 seconds.
- At this stage, all **63 registered cluster integration cases passed across separate runs**, not within a single full report. The framework's 20 unit tests also passed with `python -m unittest discover -s tests -v`.

Cluster was left running. Coverage is limited to the currently registered cases; see [COVERAGE.md](COVERAGE.md) for remaining gaps.

## Another full regression (2026-10-15)

Both modes were rebuilt as requested, with `--local` used for cluster startup. The first standalone run passed in full: [report](reports/20261015T082036Z-standalone-b331a09b/index.html).

Cluster restart exposed a state dependency after repeated acceptance runs: the ten most recent clicks for `user_0` were all `daily_accept_*` test items from earlier runs. Reinitialized fixture recall indexes did not contain these items, leaving the item-CF, content, and item-sequence embedding channels without candidates.

- [First cluster restart report](reports/20261015T082522Z-cluster-2e24c2a1/index.html): Airflow startup acceptance failed, and subsequent cases were correctly skipped.
- `example_cluster/airflow/dags/openrec_cluster_bootstrap.py` was corrected to pass explicit fixture trigger items in warmup and smoke requests instead of depending on mutable recent-click history. Assertions for all six required channels and actual ranking were retained.
- [Next restart report](reports/20261015T084545Z-cluster-d762e191/index.html): `start.sh --local` passed, after which auto-test smoke exposed the same history dependency. `checks.py` was updated to select a trigger item present in the item-CF, content, and embedding fixture CSVs and pass it explicitly in recommendation requests. Existing history and assertion strength were preserved.
- All 4 example recall acceptance tests passed, including local model fixture consistency checks. All 21 auto-test framework tests passed.

The corrected [full cluster report](reports/20261015T090156Z-cluster-fb20600a/index.html) recorded **all checks passing within one full execution**:

| Check group | Result |
| --- | --- |
| preflight / smoke | Passed |
| Entity deletion / feature parity / analytics | Passed |
| Daily recall end-to-end | Passed |
| LR/FM training, publishing, rollback, recovery after restart | Passed |
| API | 26 passed |
| Web demo API | 12 passed |
| Regular browser UI | 24 passed |
| Full LR/FM/LightGBM model UI workflow | 1 passed |

Cluster had **63 integration tests passed, 0 failed, and 0 skipped**, plus all 5 end-to-end scripts above. These results came from a single full execution rather than a combination of focused runs.

After the shared smoke fix, standalone was rebuilt from source and the full suite rerun: [final standalone report](reports/20261015T092111Z-standalone-9c40f614/index.html). Startup, warmup, and smoke all passed. API: 12 passed / 14 skipped; demo API: 12 passed; browser UI: 17 passed / 7 skipped; model UI: 1 skipped. Total: **41 passed, 22 skipped as inapplicable to the mode, 0 failed**.

Conclusion for this round: both modes passed their full regressions with the corrected sources. Standalone ran last and was left running; cluster applications were stopped normally during the mode switch, with the shared platform and volumes retained.

## Full confirmation after all changes (2026-10-15)

As requested, after the example and auto-test changes above were complete, both modes were rebuilt from source and fully regressed without further code changes:

| Mode | Execution | Final result |
| --- | --- | --- |
| standalone | `run.py standalone --restart --suite full --continue-on-failure --browser-path /tmp/openrec-regression-browsers` | Startup and smoke passed; 41 passed, 22 skipped as inapplicable to the mode, 0 failed |
| cluster | `run.py cluster --restart --local --suite full --continue-on-failure --browser-path /tmp/openrec-regression-browsers` | Startup through all full-suite cases passed in the same run; 63 passed, 0 skipped, 0 failed; all 5 end-to-end scripts passed |

- [Final complete standalone report](reports/20261015T092942Z-standalone-de3a1147/index.html)
- [Final complete cluster report](reports/20261015T093542Z-cluster-5da43dc4/index.html)

This round found no new issues and introduced no product or test code changes. Git HEADs and SHA-256 hashes of all modified workspace files in rec-server, rank-engine, rec-algorithm, rec-console, example, and auto-test were checked before and after execution and matched exactly. Fingerprints are stored in `source-fingerprints.json` in both report directories. Only this record section was appended after validation.

At the end of this round, cluster remained running; standalone applications had been stopped normally during the mode switch, and shared volumes were retained.
