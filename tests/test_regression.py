import argparse
import copy
import contextlib
import io
import importlib.util
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import checks
import run


class FixtureSampleTests(unittest.TestCase):
    def test_sample_requires_all_trigger_channels_in_both_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {
                "model/recall/item/item_cf_i2i.csv": "scene,left_item\nscene_0,missing\nscene_0,valid\n",
                "model/recall/item/content_i2i.csv": "scene,left_item\nscene_0,missing\nscene_0,valid\n",
                "model/recall/item/item_seq_emb.csv": "scene,item\nscene_0,valid\n",
                "model/recall/item/user_cf_u2i.csv": "scene,user\nscene_0,user_0\nscene_0,fixture_user\n",
                "example/data/test/event.csv": (
                    "scene,type,user_id,item_id\n"
                    "scene_0,click,fixture_user,missing\n"
                    "scene_0,click,fixture_user,valid\n"
                ),
            }
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            self.assertEqual(("user_0", "valid"), checks.fixture_sample(root, "cluster"))
            self.assertEqual(("fixture_user", "valid"), checks.fixture_sample(root, "standalone"))
            (root / "model/recall/item/item_seq_emb.csv").write_text("scene,item\n")
            with self.assertRaisesRegex(RuntimeError, "no fixture sample"):
                checks.fixture_sample(root, "cluster")


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
        self.assertEqual([name for name, _ in cases], ["preflight", "startup", "smoke", *run.CLUSTER, *run.FUNCTIONAL])
        self.assertEqual(cases[1][1][-1], "--local")
        self.assertIn("workspace with spaces", cases[3][1][-1])

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

    def test_standalone_full_can_validate_existing_deployment(self):
        result = subprocess.run([sys.executable, str(run.ROOT / "run.py"), "standalone",
                                 "--suite", "full", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("test_ui.py", result.stdout)


    def test_restart_plan_checks_dependencies_before_stopping(self):
        args = argparse.Namespace(workspace=Path("/workspace"), mode="cluster", start=True,
                                  restart=True, local=True, case=["ui", "ui"], suite="smoke")
        names = [name for name, _ in run.plan(args)]
        self.assertEqual(names, ["preflight", "stop-standalone", "stop-cluster", "startup", "smoke", "ui"])
        self.assertIn("--keep-platform", run.plan(args)[2][1])

    def test_test_results_preserve_fixture_errors_and_skip_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.xml"
            path.write_text('<testsuites><testsuite><testcase name="cleanup" time="1">'
                '<error message="restore failed"/></testcase><testcase name="mode">'
                '<skipped message="cluster only"/></testcase></testsuite></testsuites>')
            results = run.test_results(path)
            self.assertEqual([r["status"] for r in results], ["failed", "skipped"])
            self.assertEqual(results[0]["message"], "restore failed")
            self.assertEqual(results[1]["message"], "cluster only")

    def test_mode_specific_case_and_local_constraints(self):
        for arguments in [["standalone", "--case", "rank-model"], ["cluster", "--local"],
                          ["cluster", "--projection-timeout", "0"]]:
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(run.ROOT / "run.py"), *arguments,
                    "--dry-run"], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)

    def test_preflight_failure_prevents_startup_and_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = ["run.py", "cluster", "--start", "--local", "--suite", "api", "--output", tmp]
            with patch.object(sys, "argv", argv), patch.object(run, "ROOT", root), \
                 patch.object(run, "metadata", return_value={}), \
                 patch.object(run, "execute", return_value=("failed", 0, "missing dependency")) as execute, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run.main(), 1)
            self.assertEqual(execute.call_count, 1)
            document = json.loads(next(root.glob("*/results.json")).read_text())
            self.assertEqual(document["results"][0]["name"], "preflight")
            self.assertTrue(all(r["status"] == "skipped" for r in document["results"][1:]))

    def test_cleanup_failure_blocks_continue_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def execute(command, cwd, logfile, timeout):
                if logfile.stem == "api":
                    (logfile.parent / "api-junit.xml").write_text(
                        '<testsuites><testsuite errors="0"><testcase name="restore">'
                        '<properties><property name="cleanup_failed" value="true"/></properties>'
                        '<failure message="restore failed"/></testcase></testsuite></testsuites>')
                    return "failed", 0, "exit code 1"
                return "passed", 0, "exit code 0"
            argv = ["run.py", "standalone", "--suite", "api", "--continue-on-failure", "--output", tmp]
            with patch.object(sys, "argv", argv), patch.object(run, "ROOT", root), \
                 patch.object(run, "metadata", return_value={}), patch.object(run, "execute", side_effect=execute), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run.main(), 1)
            document = json.loads(next(root.glob("*/results.json")).read_text())
            self.assertEqual(document["results"][-1]["name"], "demo")
            self.assertEqual(document["results"][-1]["status"], "skipped")

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "optional pytest dependency not installed")
    def test_pytest_stops_after_cleanup_error_and_exports_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conftest.py").write_text(
                "from integration.conftest import pytest_runtest_makereport\n")
            (root / "test_cleanup.py").write_text(
                "from openrec_test.lifecycle import cleanup_guard\n"
                "def test_a_restore():\n"
                "    with cleanup_guard():\n"
                "        raise ValueError('restore failed')\n"
                "def test_b_must_not_run():\n"
                "    raise AssertionError('later mutation ran')\n")
            result = subprocess.run([sys.executable, "-m", "pytest", str(root), "-q",
                "--junitxml", str(root / "junit.xml")], cwd=run.ROOT, capture_output=True, text=True,
                env={**os.environ, "PYTHONPATH": str(run.ROOT)}, timeout=30)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            xml = ET.parse(root / "junit.xml").getroot()
            self.assertEqual(len(list(xml.iter("testcase"))), 1)
            self.assertTrue(any(p.get("name") == "cleanup_failed" and p.get("value") == "true"
                                for p in xml.iter("property")))


if __name__ == "__main__":
    unittest.main()
