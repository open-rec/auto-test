"""Slow, serial training/deployment flow; requires an initialized cluster."""
from openrec_test.lifecycle import cleanup_guard
import datetime as dt
import json
import time

import pytest
from playwright.sync_api import expect

from openrec_test.client import business, eventually
from test_ui import console, response

pytestmark = pytest.mark.cluster


@pytest.fixture
def training_scene(unique_id, push, clients, poll):
    now = int(time.time())
    users = [{"id": f"{unique_id}_u{i}", "name": "auto-test", "age": 20 + i,
              "gender": "male" if i % 2 else "female", "registerTime": str(now - 1000),
              "tags": ["auto-test"], "extFields": {}} for i in range(4)]
    items = [{"id": f"{unique_id}_i{i}", "title": "auto-test", "scene": unique_id,
              "weight": i + 1, "category": "auto-test", "tags": "auto-test", "status": 1,
              "pubTime": str(now - 100), "modifyTime": str(now), "extFields": {}} for i in range(4)]
    try:
        push("user", "INSERT", users)
        push("item", "INSERT", items)
        for kind, rows in [("user", users), ("item", items)]:
            for row in rows:
                poll(lambda k=kind, r=row: business(clients["server"].request(f"/api/query/{k}/{r['id']}")), bool)
        # Event timestamps must follow entity snapshots for the point-in-time join.
        eventually(lambda: int(time.time()), lambda stamp: stamp > now, timeout=5)
        events = [{"userId": user["id"], "itemId": item["id"], "scene": unique_id,
                   "type": "click" if (u + i) % 2 else "expose", "value": "1",
                   "time": str(int(time.time())), "traceId": f"{unique_id}_{u}_{i}"}
                  for u, user in enumerate(users) for i, item in enumerate(items)]
        push("event", "INSERT", events)
        for row in users:
            poll(lambda r=row: clients["web"].request("/api/state", params={"userId": r["id"], "scene": unique_id}),
                 lambda value: value["counters"]["click"] >= 2 and value["counters"]["expose"] >= 2)
        yield unique_id
    finally:
        with cleanup_guard():
            # Execute both cleanup operations even if one fails; pytest reports teardown errors.
            errors = []
            for kind, rows in [("item", items), ("user", users)]:
                try:
                    push(kind, "DELETE", rows)
                    for row in rows:
                        poll(lambda k=kind, r=row: business(clients["server"].request(f"/api/query/{k}/{r['id']}")), lambda value: not value)
                except Exception as error:
                    errors.append(str(error))
            assert not errors, errors


def test_model_training_deployment_rollback_copy(training_scene, clients, page, artifacts):
    api = clients["console"]
    original = api.request("/api/models/releases/global")["active"]
    assert original and original.get("version"), "requires an active version to restore; run rank-model first"
    (artifacts / "original-model.json").write_text(json.dumps(original, indent=2))
    console(page, clients, "Rank Model")
    versions = []
    try:
        for model_type in ["lr", "fm", "lightgbm"]:
            page.get_by_role("button", name="离线训练", exact=True).click()
            page.get_by_label("模型类型").select_option(model_type)
            page.get_by_label("业务日期").fill(dt.datetime.now(dt.timezone.utc).date().isoformat())
            page.get_by_label("版本修订号").fill("r" + str(time.time_ns()))
            page.get_by_label("训练场景范围").fill(training_scene)
            page.get_by_label("训练轮数", exact=True).fill("2")
            page.get_by_label("最低 AUC").fill("0")
            # Expand the actual disclosure controls before interacting with their inputs.
            for details in page.locator(".feature-selection details").all():
                if details.get_attribute("open") is None:
                    details.locator("summary").click()
            while page.locator(".feature-selection input:checked").count():
                page.locator(".feature-selection input:checked").first.uncheck()
            features = ["user.age", "item.weight"]
            if model_type == "fm":
                features += ["user.gender", "item.category"]
            for feature in features:
                page.locator(".feature-selection label").filter(has_text=feature).get_by_role("checkbox").check()
            before = api.request("/api/models/releases/global")["active_version"]
            run = response(page, "/api/models/training",
                lambda: page.get_by_role("button", name="开始训练", exact=True).click(), "POST")
            (artifacts / f"{model_type}-run.json").write_text(json.dumps(run, indent=2))
            def status():
                return next((r for r in api.request("/api/models/training")["dag_runs"]
                             if r["dag_run_id"] == run["dag_run_id"]), {})
            completed = eventually(status, lambda r: r.get("state") in ("success", "failed"), timeout=600, interval=2)
            assert completed["state"] == "success", completed
            assert api.request("/api/models/releases/global")["active_version"] == before, "training must not auto-deploy"
            releases = api.request("/api/models/releases/" + training_scene)["releases"]
            release = next(r for r in releases if r["model_type"] == model_type)
            assert release["gate"]["passed"] and release["feature_sha256"] and release["model_sha256"], release
            versions.append(release)
        (artifacts / "trained-models.json").write_text(json.dumps(versions, indent=2))
        page.get_by_role("button", name="在线部署", exact=True).click()
        page.get_by_role("button", name="↻ 刷新", exact=True).click()
        for release in versions:
            row = page.locator(".model-version-list article").filter(has_text=training_scene + "/" + release["version"])
            response(page, "/api/models/releases/publish",
                lambda: row.get_by_role("button", name="部署此版本", exact=True).click(), "POST")
            assert api.request("/api/models/runtime")["model"]["type"] == release["model_type"]
            data = business(clients["server"].request("/api/recommend/item", {"requestId": training_scene,
                "body": {"userId": training_scene + "_u0", "scene": "scene_0", "size": 5, "params": {"ab": "default"}}}))
            assert data["results"] and all(item.get("rankScore") is not None for item in data["results"]), data
        rolled = response(page, "/api/models/releases/rollback",
            lambda: page.get_by_role("button", name="回滚保留版本", exact=True).click(), "POST")
        assert rolled["active_version"] == versions[-2]["version"], rolled
        row = page.locator(".model-version-list article").filter(has_text=training_scene + "/" + versions[0]["version"])
        row.locator("summary").click()
        row.get_by_role("button", name="复制特征配置并重新训练", exact=True).click()
        expect(page.get_by_label("模型类型")).to_have_value("lr")
        for feature in ["user.age", "item.weight"]:
            expect(page.locator(".feature-selection label").filter(has_text=feature).get_by_role("checkbox")).to_be_checked()
    finally:
        with cleanup_guard():
            api.request("/api/models/releases/publish", {"scene": original["scene"], "version": original["version"], "target_type": "item"})
            assert api.request("/api/models/releases/global")["active_version"] == original["version"]
