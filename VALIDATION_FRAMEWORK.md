# API / browser framework execution record (2026-10-15)

This document records validation of the auto-test extensions without changing the historical conclusions in the [earlier distribution acceptance record](VALIDATION.md). Product failures remain failures: no xfail markers or automatic retries are used to remove failure records.

See the [product fix validation record](VALIDATION_FIXES.md) for subsequent fixes and retest results. The initial execution history is preserved below.

## Environment and commands

- Python 3.13, pytest 9.1.1, Playwright 1.64.0 / Chromium. The existing `/tmp/openrec-regression-venv` and `/tmp/openrec-regression-browsers` were reused; system dependencies were not reinstalled.
- Framework unit tests: `/tmp/openrec-regression-venv/bin/python -m unittest discover -s tests -v`, with 20 passing. A standard-library-only environment explicitly skips 1 pytest plugin self-test.
- All 63 integration cases can be collected in both modes. Cluster-only features are explicitly skipped in standalone.
- Cluster was rebuilt from local sources using `run.py cluster --restart --local --suite full --continue-on-failure`, with explicit `RECOMMEND_DEADLINE_MS=10000 OPENREC_GRAPH_NODE_TIMEOUT_MS=1000`. This was not acceptance of the default latency SLA.
- Source changes for this framework extension were confined to auto-test. Other product repositories were not changed to make tests pass. The workspace already contained a rec-algorithm `point_in_time.py` fix and its test; reports recorded this dirty state. This was not acceptance of an entirely unmodified release tag.

## First full cluster run

Report: [cluster HTML summary](reports/20261015T051300Z-cluster-1d85c3ff/index.html).

| Check | Result |
| --- | --- |
| Dependency preflight, stopping examples while retaining the platform, `start.sh --local` | Passed; startup took about 310 seconds |
| smoke | Passed |
| entity-delete / feature-parity / analytics | All passed |
| daily-recall | Passed; about 142 seconds |
| rank-model (LR/FM, offline training, publishing, rollback, recovery after restart) | Passed; about 113 seconds |
| New API group | 23 passed, 3 failed |
| Demo API group | 5 passed, 7 failed |
| UI group (initial version) | 18 passed, 6 failed; included the test issues corrected below |
| models-ui (initial version) | All three model types trained successfully; post-deployment recommendation assertions failed because a fixture user was reused, then were changed to use an independent user |

Initial UI test corrections: exact matching for the localized force-refresh button and ranking-target label; publishing a new baseline before DAG rollback (rollback selects the most recent published record, not the originally active version); triggering stay reporting by switching tabs instead of assuming the last card is outside the viewport; and avoiding reuse of `user_0`, which already had exposure history, for model recommendation validation.

## Separate record of a smoke-blocked run

`reports/20261015T060136Z-cluster-7ed440c1/`: preflight passed, smoke failed, and both UI groups were correctly skipped.

rec-server logs recorded `rank=TIMED_OUT` at 2026-10-15 06:01:40 UTC with a 1000 ms node budget. operation / collector then failed and recommendations returned HTTP 500, while `/ready` remained READY. This run cannot be reported as passing. Later direct pytest debugging of framework corrections is recorded separately and does not replace this failure.

## Confirmed product failures

1. Serving Graph: `GET /api/serving-graph` returned `experiments: null` and `routing: null`. Enabling a published experiment failed; the UI showed 0 Runtime experiments and could not locate rows for enable/disable/delete actions. Both API tests and a real browser detected the issue.
2. Cluster fixture item feedback: click / collect / buy / stay / expose / dislike on fixture items in the hot list (such as `item_6689` in this run) returned `ok: false`. Batch exposure also failed, and direct event push returned HTTP 500. UI recommendations could select newly written, valid items, so an individual UI success could not replace API validation against fixture data.
3. The rank node occasionally exceeded the functional test budget of 1000 ms; see the separate smoke-blocked record.

See [COVERAGE.md](COVERAGE.md) for additional unimplemented coverage. `full` runs all currently registered suites; it is not a claim that every product feature has passed.

## Focused cluster UI debugging after test corrections

Report: [focused cluster UI JUnit](reports/cluster-ui-debug/junit.xml), with 25 cases: 22 passed and 3 failed. This was a direct pytest invocation, not a successful full runner execution:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/openrec-regression-browsers /tmp/openrec-regression-venv/bin/python -m pytest \
  integration/test_ui.py integration/test_models_ui.py --mode cluster \
  --artifacts reports/cluster-ui-debug --junitxml reports/cluster-ui-debug/junit.xml -v --tb=short
```

- Both DAG UI configuration rollbacks, recall UI switching and rollback, analytics queries and forced refresh, model page target switching, exposure/stay reporting, and Airflow pause/resume passed.
- LR / FM / LightGBM training, artifact gate/hash checks, no automatic deployment after training, deployment and recommendation scores for all three model types, rollback, feature copying, and original model restoration passed. Training fixture entity cleanup also passed.
- An empty initial recommendation result and the missing Runtime experiment list still caused failures.
- Another failure occurred when a background exposure write raced with browser exposure reset, leaving the count at 1. The UI assertion was corrected to check deletion confirmation and renewed results; the demo API case continues to verify a zero count when no concurrent activity occurs. The old failure report is retained.

## Final full standalone run

Command:

```bash
/tmp/openrec-regression-venv/bin/python run.py standalone --restart --suite full \
  --continue-on-failure --browser-path /tmp/openrec-regression-browsers
```

Reports: [HTML summary](reports/20261015T062735Z-standalone-109384d4/index.html), [JSON](reports/20261015T062735Z-standalone-109384d4/results.json). Startup took about 161 seconds; startup and smoke passed. Service deadline environment variables were not explicitly overridden for this run.

| Group | Passed | Failed | Mode-specific skips |
| --- | ---: | ---: | ---: |
| API | 10 | 2 | 14 |
| demo API | 12 | 0 | 0 |
| Browser UI | 16 | 1 | 7 |
| Model UI | 0 | 0 | 1 |
| Total | 38 | 3 | 22 |

The three failures were experiment activation, routing retrieval, and the missing UI Runtime experiment list, all attributable to the Serving Graph issue above. Exposure reset, actual exposure/stay reporting, and global blacklist restoration passed in the final version. Cluster-only cases and the entire model UI group were explicitly skipped and were not counted as passes.

At the end of this run, **standalone applications were left running**, cluster applications were stopped, and the big data platform and volumes were retained. No Git commits or pushes were made at this stage. Report directories are ignored by Git; the README, coverage matrix, validation documentation, and framework code were included in these changes.
