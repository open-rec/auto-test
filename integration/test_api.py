from openrec_test.lifecycle import cleanup_guard
import copy
import time

import pytest

from openrec_test.client import business


def test_health_and_contracts(clients, mode):
    assert clients["server"].request("/ready")["ready"] is True
    business(clients["server"].request("/health"))
    assert "/api/recommend/item" in clients["server"].request("/v3/api-docs")["paths"]
    assert "/api/serving-graph" in clients["console"].request("/openapi.json")["paths"]
    if mode == "cluster":
        rank = clients["rank"].request("/health")
        assert rank["status"] == "success" and rank["data"]["ready"] is True


def test_mode_capabilities(clients, mode):
    config = clients["console"].request("/api/config")
    enabled = {key for key, value in config["features"].items() if value}
    assert enabled == ({"entities", "serving", "monitor"} if mode == "standalone" else
        {"recall", "entities", "serving", "dag", "monitor", "analytics", "airflow", "model"})
    if mode == "standalone":
        for path in ["/api/recall/releases/hot", "/api/dag-configs/openrec_daily_recall",
                     "/api/analytics/business", "/api/airflow/dags", "/api/models/features"]:
            clients["console"].request(path, expected=404)


def test_user_update_and_console_query(user, push, clients, poll):
    push("user", "UPDATE", [{"id": user, "name": "updated", "age": 31}])
    poll(lambda: business(clients["server"].request("/api/query/user/" + user)),
         lambda data: data and data.get("name") == "updated")
    assert clients["console"].request("/api/entities/users/" + user)["name"] == "updated"


def test_item_lifecycle(unique_id, push, clients, poll):
    path = "/api/query/item/" + unique_id
    row = {"id": unique_id, "title": "test item", "scene": "scene_0", "status": 1,
           "weight": 1, "category": "test", "tags": "test", "extFields": {},
           "pubTime": str(int(time.time()) - 60), "modifyTime": str(int(time.time()))}
    try:
        push("item", "INSERT", [row])
        poll(lambda: business(clients["server"].request(path)), lambda data: data and data.get("title") == "test item")
        push("item", "UPDATE", [{**row, "title": "updated item"}])
        poll(lambda: business(clients["server"].request(path)), lambda data: data and data.get("title") == "updated item")
        assert clients["console"].request("/api/entities/items/" + unique_id)["title"] == "updated item"
    finally:
        with cleanup_guard():
            push("item", "DELETE", [{"id": unique_id, "scene": "scene_0"}])
            poll(lambda: business(clients["server"].request(path)), lambda data: not data)


@pytest.mark.parametrize("route", ["/api/recommend", "/api/recommend/item"])
def test_recommendation_routes(route, clients, user, mode):
    data = business(clients["server"].request(route, {"requestId": user, "body": {
        "userId": user, "scene": "scene_0", "size": 8, "debug": True,
        "params": {"ab": "default", "query": "item"}}}))
    assert 0 < len(data["results"]) <= 8, data
    ids = [item["id"] for item in data["results"]]
    assert len(ids) == len(set(ids)), data
    for item in data["results"]:
        if mode == "standalone":
            assert item.get("rankScore") is None, item
        else:
            import math
            score = item.get("rankScore")
            assert type(score) in (int, float) and math.isfinite(score), item


def test_event_push_and_query(user, clients, push, poll):
    items = clients["web"].request("/api/tab/hot", params={"userId": user, "scene": "scene_0"})["items"]
    assert items
    item = items[0]["id"]
    push("event", "INSERT", [{"userId": user, "itemId": item, "scene": "scene_0", "type": "click",
         "value": "1", "time": str(int(time.time())), "traceId": user, "deviceId": "auto-test"}])
    poll(lambda: clients["console"].request("/api/entities/events", params={
        "user_id": user, "scene": "scene_0", "event_type": "click"}), lambda data: item in str(data))


def test_user_text_recommendation(clients, user):
    data = business(clients["server"].request("/api/recommend/user", {
        "requestId": user, "body": {"userId": user, "scene": "scene_0", "size": 8,
                                    "debug": True, "params": {"ab": "default", "query": "CN"}}}))
    results = data["results"]
    assert 0 < len(results) <= 8, data
    assert len({result["id"] for result in results}) == len(results), data
    assert any(node.get("channel") == "user_search" and node.get("status") == "SUCCESS"
               and node.get("candidateCount", 0) > 0 for node in data["recallDiagnostics"]), data


def test_graph_publish_rollback_validation(clients, unique_id):
    api = clients["console"]
    original = api.request("/api/serving-graph")["graph"]
    try:
        api.request("/api/serving-graph/experiments", {"name": unique_id})
        first = api.request("/api/serving-graph/publish", {"experiment": unique_id, "graph": original})
        changed = copy.deepcopy(original)
        changed["nodes"][0]["timeout"] += 10
        api.request("/api/serving-graph/publish", {"experiment": unique_id, "graph": changed})
        assert api.request("/api/serving-graph", params={"experiment": unique_id})["graph"] == changed
        api.request("/api/serving-graph/rollback", {"experiment": unique_id, "version": first["version"]})
        assert api.request("/api/serving-graph", params={"experiment": unique_id})["graph"] == original
        changed["nodes"][0]["timeout"] = 0
        api.request("/api/serving-graph/publish", {"experiment": unique_id, "graph": changed}, expected=400)
    finally:
        with cleanup_guard():
            api.request("/api/serving-graph/experiments/" + unique_id, method="DELETE")


def test_graph_experiment_enable_disable(clients, unique_id):
    api = clients["console"]
    try:
        api.request("/api/serving-graph/experiments", {"name": unique_id})
        graph = api.request("/api/serving-graph")["graph"]
        api.request("/api/serving-graph/publish", {"experiment": unique_id, "graph": graph})
        for enabled in [True, False]:
            api.request(f"/api/serving-graph/experiments/{unique_id}/enabled", {"enabled": enabled}, method="PUT")
            state = api.request("/api/serving-graph", params={"experiment": unique_id})
            assert state.get("experiments"), state
            assert state["experiments"][unique_id]["enabled"] is enabled, state
    finally:
        with cleanup_guard():
            api.request("/api/serving-graph/experiments/" + unique_id, method="DELETE")


@pytest.mark.cluster
@pytest.mark.parametrize("dag", ["openrec_daily_recall", "openrec_daily_user_recall"])
def test_dag_config_roundtrip(clients, dag):
    api = clients["console"]
    path = "/api/dag-configs/" + dag
    old = api.request(path)
    baseline = old["version"] or api.request(path + "/publish", old["config"])["version"]
    try:
        changed = {**old["config"], "retries": 0 if old["config"]["retries"] else 1}
        api.request(path + "/publish", changed)
        assert api.request(path)["config"] == changed
        api.request(path + "/rollback", {"version": baseline})
        assert api.request(path)["config"] == old["config"]
        api.request(path + "/publish", {**changed, "schedule": "invalid-cron"}, expected=400)
    finally:
        with cleanup_guard():
            api.request(path + "/rollback", {"version": baseline})
            assert api.request(path)["config"] == old["config"]


@pytest.mark.cluster
@pytest.mark.parametrize("algorithm", ["hot", "new", "item-cf-i2i", "content-i2i", "user-cf-u2i", "item-search", "user-search"])
def test_recall_catalog(clients, algorithm):
    data = clients["console"].request("/api/recall/releases/" + algorithm)
    assert data["active_indexes"] and data["releases"], data


@pytest.mark.cluster
def test_recall_switch_rollback(clients):
    api = clients["console"]
    path = "/api/recall/releases/hot"
    before = api.request(path)
    assert len(before["active_indexes"]) == 1, before
    current = before["active_indexes"][0]
    candidates = [r["index"] for r in before["releases"] if r["published"] and not r["active"]]
    assert candidates, "requires two published hot releases; run daily-recall first"
    try:
        api.request("/api/recall/releases/switch", {"algorithm": "hot", "target_index": candidates[0]})
        assert api.request(path)["active_indexes"] == [candidates[0]]
        api.request("/api/recall/releases/rollback", {"algorithm": "hot", "target_index": current})
        assert api.request(path)["active_indexes"] == [current]
    finally:
        with cleanup_guard():
            api.request("/api/recall/releases/switch", {"algorithm": "hot", "target_index": current})
            assert api.request(path)["active_indexes"] == [current]


@pytest.mark.cluster
def test_airflow_runs_tasks(clients):
    from urllib.parse import quote
    api = clients["console"]
    assert api.request("/api/airflow/dags")["dags"]
    path = "/api/airflow/dags/openrec_daily_recall"
    assert api.request(path)["tasks"]
    runs = api.request(path + "/runs")["dag_runs"]
    assert runs, "bootstrap must leave an Airflow run"
    run = quote(runs[0]["dag_run_id"], safe="")
    tasks = api.request(path + f"/runs/{run}/tasks")
    assert tasks["task_instances"], tasks


@pytest.mark.cluster
def test_model_catalog_runtime_and_validation(clients):
    api = clients["console"]
    assert api.request("/api/models/features")["features"]
    runtime = api.request("/api/models/runtime")
    assert runtime["ready"] is True and runtime["model"], runtime
    assert "releases" in api.request("/api/models/releases/global")
    assert "dag_runs" in api.request("/api/models/training")
    api.request("/api/models/training", {}, expected=422)
    api.request("/api/models/releases/publish", {"scene": "global", "version": "x", "target_type": "invalid"}, expected=422)


@pytest.mark.cluster
def test_analytics_validation(clients):
    clients["console"].request("/api/analytics/business",
        params={"date_from": "2026-10-15", "date_to": "2026-10-14"}, expected=400)


def test_global_blacklist_filter_and_restore(clients, user):
    import subprocess
    def redis(*args):
        return subprocess.check_output(["docker", "exec", "redis", "redis-cli", "--raw", *args],
                                       text=True, timeout=30).splitlines()
    def recommend():
        clients["web"].request("/api/reset", {"userId": user, "scene": "scene_0"})
        return business(clients["server"].request("/api/recommend/item", {"requestId": user,
            "body": {"userId": user, "scene": "scene_0", "size": 12, "params": {"ab": "default"}}}))["results"]
    before = set(redis("SMEMBERS", "black"))
    candidate = recommend()[0]["id"]
    try:
        business(clients["server"].request("/api/operate/blacklist", {"requestId": user, "body": [candidate]}))
        assert candidate not in [item["id"] for item in recommend()]
    finally:
        with cleanup_guard():
            # Serialization may quote IDs; remove the exact newly added Redis member.
            added = set(redis("SMEMBERS", "black")) - before
            for member in added:
                redis("SREM", "black", member)
            assert set(redis("SMEMBERS", "black")) == before
    assert candidate in [item["id"] for item in recommend()], "positive control after blacklist restoration"


def test_graph_routing_roundtrip(clients, unique_id):
    api = clients["console"]
    before = api.request("/api/serving-graph").get("routing")
    assert isinstance(before, dict), "runtime must expose routing before safe edits are possible"
    changed = {**before, "routes": {**before.get("routes", {}), unique_id: "default"}}
    try:
        api.request("/api/serving-graph/routing", changed, method="PUT")
        assert api.request("/api/serving-graph")["routing"] == changed
    finally:
        with cleanup_guard():
            api.request("/api/serving-graph/routing", before, method="PUT")
            assert api.request("/api/serving-graph")["routing"] == before


@pytest.mark.cluster
@pytest.mark.parametrize("target,ids", [("item", ["item_0", "item_3"]), ("user", ["user_1", "user_2"])])
def test_rank_engine_scores_both_targets(clients, target, ids):
    import math
    value = clients["rank"].request("/model/score", {
        "user_id": "user_0", "candidate_ids": ids, "target_type": target})
    assert value["status"] == "success" and value["code"] == 0, value
    assert set(value["data"]) == set(ids), value
    assert all(type(score) in (float, int) and math.isfinite(score) for score in value["data"].values()), value
    clients["rank"].request("/model/score", {"target_type": "invalid"}, expected=422)


def test_prometheus_metrics(clients, mode):
    assert "openrec_" in clients["server"].request("/actuator/prometheus")
    if mode == "cluster":
        assert "openrec_rank_" in clients["rank"].request("/metrics")
