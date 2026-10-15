#!/usr/bin/env python3
"""Run OpenRec distribution regression checks using only the Python standard library."""
import argparse
import datetime as dt
import fcntl
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


def plan(args):
    example = args.workspace / "example"
    cases = []
    if args.start:
        command = ["bash", str(example / f"example_{args.mode}/start.sh")]
        if args.local:
            command.append("--local")
        cases.append(("startup", command))
    cases.append(("smoke", [sys.executable, str(ROOT / "checks.py"), args.mode,
                            "--workspace", str(args.workspace)]))
    selected = args.case or (list(CLUSTER) if args.suite == "full" and args.mode == "cluster" else [])
    for name in selected:
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
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
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


def report(directory, document):
    (directory / "results.json").write_text(json.dumps(document, indent=2) + "\n")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["cluster", "standalone"])
    parser.add_argument("--workspace", type=Path, default=ROOT.parent)
    parser.add_argument("--suite", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--case", action="append", choices=list(CLUSTER))
    parser.add_argument("--start", action="store_true", help="run example startup including bootstrap acceptance")
    parser.add_argument("--local", action="store_true", help="cluster startup mirror defaults")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=7200, help="per-case seconds")
    parser.add_argument("--output", type=Path, default=ROOT / "reports")
    args = parser.parse_args()
    args.workspace = args.workspace.resolve()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.case and args.mode != "cluster":
        parser.error("--case is only available for cluster")
    if args.local and (not args.start or args.mode != "cluster"):
        parser.error("--local requires cluster --start")
    if args.mode == "standalone" and args.suite == "full" and not args.start:
        parser.error("standalone full requires --start to include import and startup acceptance")
    cases = plan(args)
    for name, command in cases:
        print(f"{name}: {shlex.join(command)}", flush=True)
    if args.dry_run:
        return 0
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
        for name, command in cases:
            logfile = directory / f"{name}.log"
            if failed:
                status, seconds, message = "skipped", 0, "previous case failed"
            else:
                print(f"Running {name}; log: {logfile}", flush=True)
                status, seconds, message = execute(command, args.workspace, logfile, args.timeout)
                failed = status == "failed"
            document["results"].append({"name": name, "command": command, "status": status,
                                        "seconds": seconds, "message": message, "log": str(logfile)})
            report(directory, document)
            print(f"{name}: {status} ({seconds:.1f}s)", flush=True)
        print(f"Reports: {directory}", flush=True)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
