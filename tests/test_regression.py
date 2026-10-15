import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import checks
import run


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.response = {"status": True, "code": 200, "data": {
            "results": [{"id": "one", "rankScore": 0.5}],
            "recallDiagnostics": [{"channel": channel, "status": "SUCCESS", "candidateCount": 1}
                                  for channel in checks.CHANNELS]}}

    def test_preselection_channels_do_not_need_final_result_membership(self):
        checks.validate_recommendation(self.response, "cluster")

    def test_bad_business_results_fail(self):
        for update in [{"status": False}, {"code": 503}, {"data": {"results": []}}]:
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                checks.validate_recommendation({**self.response, **update}, "cluster")

    def test_empty_or_failed_recall_is_not_success(self):
        for update in [{"candidateCount": 0}, {"status": "TIMEOUT"}]:
            response = copy.deepcopy(self.response)
            response["data"]["recallDiagnostics"][0].update(update)
            with self.subTest(update=update), self.assertRaises(RuntimeError):
                checks.validate_recommendation(response, "cluster")

    def test_rank_bypass_and_invalid_scores(self):
        for score in [None, True, "0.5", float("nan"), float("inf")]:
            self.response["data"]["results"][0]["rankScore"] = score
            with self.subTest(score=score), self.assertRaises(RuntimeError):
                checks.validate_recommendation(self.response, "cluster")
        self.response["data"]["results"][0].pop("rankScore")
        checks.validate_recommendation(self.response, "standalone")
        self.response["data"]["results"][0]["rankScore"] = 0
        with self.assertRaises(RuntimeError):
            checks.validate_recommendation(self.response, "standalone")


class RunnerTests(unittest.TestCase):
    def test_cluster_plan_keeps_state_changes_after_smoke(self):
        args = argparse.Namespace(workspace=Path("/workspace with spaces"), mode="cluster",
                                  start=True, local=True, case=None, suite="full")
        cases = run.plan(args)
        self.assertEqual([name for name, _ in cases], ["startup", "smoke", *run.CLUSTER])
        self.assertEqual(cases[0][1][-1], "--local")
        self.assertIn("workspace with spaces", cases[-1][1][-1])

    def test_subprocess_failures_timeouts_and_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            for script, timeout, status, message in [
                ("print('ok')", 5, "passed", "exit code 0"),
                ("raise SystemExit(7)", 5, "failed", "exit code 7"),
                ("import time; time.sleep(30)", 0.05, "failed", "timeout"),
            ]:
                result = run.execute([sys.executable, "-c", script], path, path / "log", timeout)
                self.assertEqual((result[0], result[2]), (status, message))
                self.assertTrue((path / "log").read_text().startswith("$ "))

    def test_junit_records_failure_and_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            document = {"mode": "cluster", "results": [
                {"name": name, "status": status, "seconds": 0, "message": "test <&>", "log": "log"}
                for name, status in [("a", "passed"), ("b", "failed"), ("c", "skipped")]]}
            run.report(Path(tmp), document)
            suite = ET.parse(Path(tmp) / "junit.xml").getroot()
            self.assertEqual(suite.attrib["failures"], "1")
            self.assertEqual(suite.attrib["skipped"], "1")
            self.assertEqual(json.loads((Path(tmp) / "results.json").read_text()), document)

    def test_invalid_standalone_full_is_rejected(self):
        result = subprocess.run([sys.executable, str(run.ROOT / "run.py"), "standalone",
                                 "--suite", "full", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --start", result.stderr)


if __name__ == "__main__":
    unittest.main()
