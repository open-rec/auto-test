#!/usr/bin/env python3
"""Run OpenRec distribution regression checks with API, browser and distribution acceptance suites."""
import argparse
import datetime as dt
import fcntl
import html
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
CLUSTER = {
    "entity-delete": "verify_entity_delete.sh",
    "feature-parity": "verify_feature_parity_e2e.sh",
    "analytics": "verify_data_analytics.sh",
    "daily-recall": "verify_daily_recall.sh",
    "rank-model": "verify_rank_model.sh",
}


FUNCTIONAL = {"api": ["integration/test_api.py"],
              "demo": ["integration/test_demo.py"],
              "ui": ["integration/test_ui.py"],
              "models-ui": ["integration/test_models_ui.py"]}

def plan(args):
    example = args.workspace / "example"
    cases = []
    selected = args.case or (
        ([*CLUSTER] if args.mode == "cluster" else []) + list(FUNCTIONAL)
        if args.suite == "full" else
        ["api", "demo"] if args.suite == "api" else ["ui"] if args.suite == "ui" else [])
    functional = [name for name in selected if name in FUNCTIONAL]
    if functional:
        cases.append(("preflight", [sys.executable, "-m", "openrec_test.preflight", *functional]))
    if getattr(args, "restart", False):
        cases.extend([
            ("stop-standalone", ["bash", str(example / "example_standalone/stop.sh")]),
            ("stop-cluster", ["bash", str(example / "example_cluster/stop.sh"), "--keep-platform"]),
        ])
    if args.start:
        command = ["bash", str(example / f"example_{args.mode}/start.sh")]
        if args.local:
            command.append("--local")
        cases.append(("startup", command))
    cases.append(("smoke", [sys.executable, str(ROOT / "checks.py"), args.mode,
                            "--workspace", str(args.workspace)]))
    for name in dict.fromkeys(selected):
        if name in FUNCTIONAL:
            cases.append((name, [sys.executable, "-m", "pytest", *FUNCTIONAL[name],
                "--mode", args.mode, "-v", "--tb=short"]))
        else:
            cases.append((name, ["bash", str(example / "example_cluster" / CLUSTER[name])]))
    return cases


def execute(command, cwd, logfile, timeout):
    """Bound the whole process group; allow shell EXIT traps time to clean up."""
    started = time.monotonic()
    with logfile.open("w") as output:
        output.write("$ " + shlex.join(command) + "\n")
        output.flush()
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=output,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as error:
            output.write(str(error) + "\n")
            return "failed", time.monotonic() - started, str(error)
        try:
            code = process.wait(timeout=timeout)
            return ("passed" if code == 0 else "failed", time.monotonic() - started,
                    f"exit code {code}")
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            message = "interrupted" if isinstance(error, KeyboardInterrupt) else "timeout"
            return "failed", time.monotonic() - started, message


def metadata(workspace):
    manifest = json.loads((workspace / "example/release/openrec.json").read_text())
    revisions = {}
    for name in ["example", "auto-test", *manifest["components"]]:
        path = workspace / name
        def git(*arguments):
            result = subprocess.run(["git", "-C", str(path), *arguments],
                                    capture_output=True, text=True, timeout=15)
            return result.stdout.strip() if result.returncode == 0 else None
        revisions[name] = {"head": git("rev-parse", "HEAD"),
                           "status": git("status", "--porcelain")}
    return {"manifest": manifest, "repositories": revisions}


def test_results(path):
    """Preserve individual failures/skips (including fixture errors) in the JSON report."""
    if not path.exists():
        return []
    results = []
    for case in ET.parse(path).getroot().iter("testcase"):
        problem = case.find("error")
        if problem is None:
            problem = case.find("failure")
        skipped = case.find("skipped")
        results.append({"name": case.get("name"), "classname": case.get("classname"),
            "seconds": float(case.get("time", "0")),
            "status": "failed" if problem is not None else "skipped" if skipped is not None else "passed",
            "message": problem.get("message", "") if problem is not None else
                skipped.get("message", "") if skipped is not None else ""})
    return results


def report(directory, document):
    temporary = directory / "results.json.tmp"
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(directory / "results.json")
    results = document["results"]
    suite = ET.Element("testsuite", name=f"openrec-{document['mode']}",
                       tests=str(len(results)),
                       failures=str(sum(r["status"] == "failed" for r in results)),
                       skipped=str(sum(r["status"] == "skipped" for r in results)))
    for result in results:
        case = ET.SubElement(suite, "testcase", name=result["name"],
                             classname=document["mode"], time=f"{result['seconds']:.3f}")
        if result["status"] == "failed":
            ET.SubElement(case, "failure", message=result["message"]).text = result["log"]
        elif result["status"] == "skipped":
            ET.SubElement(case, "skipped", message=result["message"])
        ET.SubElement(case, "system-out").text = result["log"]
    ET.ElementTree(suite).write(directory / "junit.xml", encoding="utf-8", xml_declaration=True)
    rows = []
    for result in results:
        name = html.escape(result["name"])
        status = html.escape(result["status"])
        message = html.escape(result["message"])
        rows.append(f'<tr><td>{name}</td><td>{status}</td><td>{result["seconds"]:.1f}s</td>'
                    f'<td>{message}</td><td><a href="{name}.log">log</a></td></tr>')
        for test in result.get("tests", []):
            rows.append('<tr><td colspan="5">' + html.escape(
                f'{test["status"]}: {test["classname"]}.{test["name"]} {test["message"]}') + '</td></tr>')
    (directory / "index.html").write_text('<!doctype html><meta charset="utf-8">'
        '<title>OpenRec regression</title><style>body{font-family:system-ui;margin:2rem}'
        'td{border-bottom:1px solid #ddd;padding:.5rem;white-space:pre-wrap}</style>'
        f'<h1>OpenRec {html.escape(document["mode"])}</h1>'
        '<p><a href="results.json">JSON</a> · <a href="junit.xml">JUnit</a></p><table>'
        + ''.join(rows) + '</table>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["cluster", "standalone"])
    parser.add_argument("--workspace", type=Path, default=ROOT.parent)
    parser.add_argument("--suite", choices=["smoke", "api", "ui", "full"], default="smoke")
    parser.add_argument("--case", action="append", choices=[*FUNCTIONAL, *CLUSTER])
    parser.add_argument("--start", action="store_true", help="run example startup including bootstrap acceptance")
    parser.add_argument("--restart", action="store_true", help="stop both example applications, keep platform volumes, then start selected mode")
    parser.add_argument("--local", action="store_true", help="cluster startup mirror defaults")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=7200, help="per-case seconds")
    parser.add_argument("--continue-on-failure", action="store_true", help="continue independent groups after failures; preflight/startup/smoke remain blocking")
    parser.add_argument("--projection-timeout", type=float, default=90)
    parser.add_argument("--browser-path", type=Path, help="Playwright browser cache (sets PLAYWRIGHT_BROWSERS_PATH for child processes)")
    parser.add_argument("--output", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    args.workspace = args.workspace.resolve()
    args.start = args.start or args.restart
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.projection_timeout <= 0:
        parser.error("--projection-timeout must be positive")
    if args.case and args.mode != "cluster" and any(case in CLUSTER for case in args.case):
        parser.error("distribution verification cases require cluster")
    if args.local and (not args.start or args.mode != "cluster"):
        parser.error("--local requires cluster --start")
    cases = plan(args)
    for name, command in cases:
        print(f"{name}: {shlex.join(command)}", flush=True)
    if args.dry_run:
        return 0
    if args.browser_path:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(args.browser_path.resolve())
    # Both modes share container names, ports and data. Serialize this checkout's runs.
    with (ROOT / ".run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another auto-test run is active")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        directory = args.output.resolve() / f"{stamp}-{args.mode}-{uuid.uuid4().hex[:8]}"
        directory.mkdir(parents=True)
        document = {"mode": args.mode, "suite": args.suite, "started_at": stamp,
                    "workspace": str(args.workspace), "results": [],
                    "functional_budgets": {
                        key: os.environ.get(key) for key in (
                            "RECOMMEND_DEADLINE_MS", "OPENREC_GRAPH_NODE_TIMEOUT_MS")
                    }, **metadata(args.workspace)}
        report(directory, document)
        failed = False
        blocked = False
        for name, command in cases:
            logfile = directory / f"{name}.log"
            if name in FUNCTIONAL:
                command = [*command, "--artifacts", str(directory / name),
                    "--junitxml", str(directory / f"{name}-junit.xml"),
                    "--projection-timeout", str(args.projection_timeout)]
            if blocked:
                status, seconds, message = "skipped", 0, "previous case failed"
            else:
                print(f"Running {name}; log: {logfile}", flush=True)
                status, seconds, message = execute(command, ROOT, logfile, args.timeout)
                failed = failed or status == "failed"
                blocked = status == "failed" and (not args.continue_on_failure or
                    name in ("preflight", "stop-standalone", "stop-cluster", "startup", "smoke") or name in CLUSTER)
                if name in FUNCTIONAL and status == "failed" and message != "exit code 1":
                    # Pytest exit 1 means completed tests with assertions; all other failures
                    # (interrupt, collection/internal error, timeout) leave uncertain state.
                    blocked = True
                junit = directory / f"{name}-junit.xml"
                if junit.exists():
                    root = ET.parse(junit).getroot()
                    # Cleanup failures are errors, not assertion failures: stop subsequent mutations.
                    blocked = blocked or any(int(s.get("errors", "0")) for s in root.iter("testsuite"))
                    blocked = blocked or any(p.get("name") == "cleanup_failed" and p.get("value") == "true"
                                             for p in root.iter("property"))
            details = test_results(directory / f"{name}-junit.xml")
            if status == "passed" and details and all(test["status"] == "skipped" for test in details):
                status, message = "skipped", "all tests unsupported in this deployment mode"
            document["results"].append({"name": name, "command": command, "status": status,
                                        "seconds": seconds, "message": message, "log": str(logfile),
                                        "tests": details})
            report(directory, document)
            print(f"{name}: {status} ({seconds:.1f}s)", flush=True)
        print(f"Reports: {directory}", flush=True)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
