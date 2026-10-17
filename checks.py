"""Live smoke assertions for initialized local OpenRec deployments."""
import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

CHANNELS = {"item_cf_i2i", "content_i2i", "user_cf_u2i", "item_seq_emb", "sparse", "hot"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=30).strip()


def request(port, path, body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        print(f"{path}: HTTP {error.code}: {error.read().decode(errors='replace')}", flush=True)
        raise
    print(f"{path}: {json.dumps(payload)}", flush=True)
    return payload


def validate_recommendation(response, mode):
    require(response.get("code") == 200 and response.get("status") is True, "recommendation failed")
    data = response.get("data") or {}
    results = data.get("results") or []
    require(bool(results), "empty recommendation")
    channels = {node.get("channel") for node in data.get("recallDiagnostics", [])
                if node.get("status") == "SUCCESS" and node.get("candidateCount", 0) > 0}
    require(not CHANNELS - channels, f"missing successful recall channels: {sorted(CHANNELS - channels)}")
    for item in results:
        score = item.get("rankScore")
        if mode == "cluster":
            require(type(score) in (int, float) and math.isfinite(score),
                    f"missing/invalid rank score: {item}")
        else:
            require(score is None, f"standalone unexpectedly ranked item: {item}")


def fixture_sample(workspace, mode):
    def rows(path):
        with path.open(newline="") as source:
            return list(csv.DictReader(source))
    left = {row["left_item"] for row in rows(workspace / "model/recall/item/item_cf_i2i.csv")
            if row["scene"] == "scene_0"}
    # Explicit triggers keep smoke independent of persisted test click history.
    for table, key in [("content_i2i", "left_item"), ("item_seq_emb", "item")]:
        left &= {row[key] for row in rows(workspace / f"model/recall/item/{table}.csv")
                 if row["scene"] == "scene_0"}
    users = {row["user"] for row in rows(workspace / "model/recall/item/user_cf_u2i.csv")
             if row["scene"] == "scene_0"}
    for row in rows(workspace / "example/data/test/event.csv"):
        if (row["scene"] == "scene_0" and row["type"] == "click"
                and row["item_id"] in left and row["user_id"] in users):
            user = "user_0" if mode == "cluster" else row["user_id"]
            require(user in users, f"fixture user has no UserCF recall: {user}")
            return user, row["item_id"]
    raise RuntimeError("no fixture sample covers all required recall tables")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["cluster", "standalone"])
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    # Fail before fixture mutations when pointed at the wrong deployment mode.
    for container, key in [("rec-server", "SPRING_PROFILES_ACTIVE"), ("rec-console", "OPENREC_MODE")]:
        env = json.loads(docker("inspect", "--format", "{{json .Config.Env}}", container))
        values = dict(value.split("=", 1) for value in env)
        require(values.get(key) == args.mode, f"{container}: expected {args.mode} deployment")
    request(13579, "/health")
    require(request(13579, "/ready").get("ready") is True, "recommendation is not ready")
    request(8095, "/health")
    if args.mode == "cluster":
        rank = request(8123, "/health")
        require(rank.get("status") == "success" and (rank.get("data") or {}).get("ready") is True,
                "rank-engine is not ready")
    user, trigger_item = fixture_sample(args.workspace, args.mode)
    key = f"event:{{{user}}}:scene_0:expose"
    backup = key + ":auto-test-backup:" + uuid.uuid4().hex
    existed = docker("exec", "redis", "redis-cli", "EXISTS", key) == "1"
    if existed:
        docker("exec", "redis", "redis-cli", "RENAME", key, backup)
    try:
        response = request(13579, "/api/recommend", {
            "requestId": f"auto-test-{uuid.uuid4().hex}",
            "body": {"scene": "scene_0", "size": 12, "userId": user, "itemIds": [trigger_item],
                     "deviceId": "auto-test", "type": "click", "debug": True,
                     "params": {"ab": "default", "query": "item"}}})
        validate_recommendation(response, args.mode)
    finally:
        docker("exec", "redis", "redis-cli", "DEL", key)
        if existed and docker("exec", "redis", "redis-cli", "EXISTS", backup) == "1":
            docker("exec", "redis", "redis-cli", "RENAME", backup, key)
    user_id = f"auto_test_{uuid.uuid4().hex}"
    def push(operation):
        result = request(13579, "/api/push/user", {"requestId": user_id + operation,
            "body": {"cmd": operation, "data": [{"id": user_id, "name": "auto-test"}]}})
        require(result.get("status") is True, f"push {operation} failed")
    def wait_exists(expected):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if docker("exec", "redis", "redis-cli", "EXISTS", f"user:{{{user_id}}}") == expected:
                return
            time.sleep(1)
        raise RuntimeError(f"user projection did not reach expected state {expected}")
    try:
        push("INSERT")
        wait_exists("1")
    finally:
        push("DELETE")
        wait_exists("0")
    print(f"{args.mode} smoke passed", flush=True)


if __name__ == "__main__":
    main()
