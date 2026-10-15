import json

import pytest


@pytest.mark.parametrize("tab", ["guess", "hot", "new", "related"])
def test_demo_tabs(clients, user, tab):
    params = {"userId": user, "scene": "scene_0", "size": 8, "ab": "default"}
    if tab == "related":
        hot = clients["web"].request("/api/tab/hot", params=params)
        params["itemId"] = hot["items"][0]["id"]
    value = clients["web"].request("/api/tab/" + tab, params=params)
    assert not value.get("error"), value
    assert value["count"] > 0 and value["count"] == len(value["items"]), value
    assert all(item["resolved"] for item in value["items"]), value


@pytest.mark.parametrize("kind", ["click", "collect", "buy", "stay", "expose", "dislike"])
def test_feedback_persists(clients, user, poll, kind):
    web = clients["web"]
    params = {"userId": user, "scene": "scene_0"}
    item = web.request("/api/tab/hot", params=params)["items"][0]["id"]
    value = json.dumps({"id": item}) if kind == "dislike" else "2"
    result = web.request("/api/feedback", {**params, "itemId": item, "type": kind, "value": value})
    assert result["ok"] is True, result
    poll(lambda: web.request("/api/state", params=params), lambda state: state["counters"][kind] > 0)
    if kind in ("dislike", "expose", "click"):
        path = "/api/reset/dislike" if kind == "dislike" else "/api/reset"
        result = web.request(path, {**params, "clearClicks": True})
        assert result["ok"] is True, result
        poll(lambda: web.request("/api/state", params=params), lambda state: state["counters"][kind] == 0)


def test_feedback_batch_and_validation(clients, user, poll):
    web = clients["web"]
    params = {"userId": user, "scene": "scene_0"}
    ids = [item["id"] for item in web.request("/api/tab/hot", params=params)["items"][:2]]
    assert len(ids) == 2
    result = web.request("/api/feedback/batch", {**params, "itemIds": ids, "type": "expose"})
    assert result["ok"] is True and result["count"] == 2, result
    poll(lambda: web.request("/api/state", params=params), lambda state: state["counters"]["expose"] >= 2)
    assert web.request("/api/feedback", {**params, "type": "click"})["ok"] is False
    assert web.request("/api/feedback/batch", {**params, "itemIds": [], "type": "expose"})["ok"] is False


def test_demo_config(clients, mode):
    config = clients["web"].request("/api/config")
    assert config["exposureMode"] == ("viewport" if mode == "cluster" else "server"), config
    assert clients["web"].request("/api/experiments")
