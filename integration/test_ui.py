"""Real Chromium actions: assertions check both backend responses and visible state."""
from openrec_test.lifecycle import cleanup_guard
import datetime as dt

import pytest
from playwright.sync_api import expect


def response(page, path, action, method="GET", kind=None):
    def matches(value):
        return (path in value.url and value.request.method == method and
                (kind is None or (value.request.post_data_json or {}).get("type") == kind))
    with page.expect_response(matches, timeout=180000) as pending:
        action()
    result = pending.value
    assert result.ok, f"HTTP {result.status}: {result.text()[:2000]}"
    value = result.json()
    assert value.get("ok", True) is not False, value
    assert not value.get("error"), value
    return value


def demo(page, clients, user):
    page.goto(clients["web"].base, wait_until="domcontentloaded")
    page.locator("#userId").fill(user)
    with page.expect_response(lambda value: "/api/tab/guess" in value.url and f"userId={user}" in value.url) as pending:
        page.locator("#userId").press("Tab")
    value = pending.value.json()
    assert pending.value.ok and value["userId"] == user and value["count"] > 0, value
    expect(page.locator("#grid .card").first).to_be_visible()
    expect(page.locator("#grid .unresolved")).to_have_count(0)


def console(page, clients, label):
    page.goto(clients["console"].base, wait_until="domcontentloaded")
    page.locator(".nav-item").filter(has_text=label).click()


@pytest.mark.parametrize("tab", ["hot", "new", "guess"])
def test_demo_navigation(user, clients, page, tab):
    demo(page, clients, user)
    value = response(page, "/api/tab/" + tab,
                     lambda: page.locator(f'#tabs [data-tab="{tab}"]').click())
    assert value["count"] > 0, value
    value = response(page, "/api/tab/" + tab, lambda: page.locator("#scene").select_option("scene_1"))
    assert value["scene"] == "scene_1" and value["count"] > 0, value
    response(page, "/api/tab/", lambda: page.locator("#reload").click())
    expect(page.locator("#grid .card").first).to_be_visible()


@pytest.mark.parametrize("kind", ["collect", "buy"])
def test_demo_action_buttons(user, clients, page, poll, kind):
    demo(page, clients, user)
    response(page, "/api/feedback", lambda: page.locator(f'#grid [data-act="{kind}"]').first.click(), "POST", kind)
    poll(lambda: clients["web"].request("/api/state", params={"userId": user, "scene": "scene_0"}),
         lambda value: value["counters"][kind] > 0)


def test_demo_detail_related_back(user, clients, page):
    demo(page, clients, user)
    response(page, "/api/feedback", lambda: page.locator("#grid .card-title").first.click(), "POST", "click")
    expect(page.locator("#detail")).to_be_visible()
    expect(page.locator("#detailEntity")).not_to_be_empty()
    expect(page.locator("#relatedGrid .card").first).to_be_visible()
    page.locator("#detailBack").click()
    expect(page.locator("#grid")).to_be_visible()


@pytest.mark.parametrize("scope", ["id", "category", "tags"])
def test_demo_dislikes_and_reset(user, clients, page, poll, scope):
    demo(page, clients, user)
    page.locator('#grid [data-act="feedback"]').first.click()
    response(page, "/api/feedback", lambda: page.locator(f'#grid [data-act="dislike-{scope}"]').first.click(), "POST", "dislike")
    poll(lambda: clients["web"].request("/api/state", params={"userId": user, "scene": "scene_0"}),
         lambda value: value["counters"]["dislike"] > 0)
    value = response(page, "/api/reset/dislike", lambda: page.locator("#resetDislike").click(), "POST")
    assert value["counters"]["dislike"] == 0, value
    value = response(page, "/api/reset", lambda: page.locator("#reset").click(), "POST")
    # The page immediately reloads and reports fresh exposures; a zero counter races
    # with those writes. The API suite checks zero after a quiescent reset.
    assert value["deleted"]["expose"] is True, value
    expect(page.locator("#grid .card").first).to_be_visible()


def test_console_mode_navigation(page, clients, mode):
    page.goto(clients["console"].base, wait_until="domcontentloaded")
    expect(page.locator(".nav-item.disabled")).to_have_count(5 if mode == "standalone" else 0)


@pytest.mark.parametrize("kind", ["USER", "ITEM", "EVENT"])
def test_console_entity_search(user, clients, page, kind):
    console(page, clients, "实体排查")
    page.get_by_role("button", name=kind, exact=True).click()
    identity = user if kind != "ITEM" else "item_2"
    page.locator(".query-form input").first.fill(identity)
    value = response(page, "/api/entities/", lambda: page.get_by_role("button", name="查询", exact=True).click())
    if kind != "EVENT":
        assert value["id"] == identity, value
    else:
        assert value is not None


def test_console_graph_editor_publish_rollback(page, clients, unique_id):
    api = clients["console"]
    console(page, clients, "Serving Graph")
    expect(page.locator(".graph-node").first).to_be_visible()
    for label in ["适应窗口", "＋", "−", "1:1"]:
        page.get_by_role("button", name=label, exact=True).click()
    try:
        page.get_by_placeholder("实验名，例如 test1 或 rank_v2").fill(unique_id)
        response(page, "/api/serving-graph/experiments", lambda: page.get_by_role("button", name="新增实验", exact=True).click(), "POST")
        button = page.get_by_role("button", name="发布实验 " + unique_id, exact=True)
        first = response(page, "/api/serving-graph/publish", button.click, "POST")
        field = page.get_by_label("超时（ms）")
        field.fill(str(int(field.input_value()) + 1))
        response(page, "/api/serving-graph/publish", button.click, "POST")
        row = page.locator(".history-list > div").filter(has_text=first["version"])
        response(page, "/api/serving-graph/rollback", lambda: row.get_by_role("button", name="回滚至此", exact=True).click(), "POST")
        assert api.request("/api/serving-graph", params={"experiment": unique_id})["version"] == first["version"]
    finally:
        with cleanup_guard():
            api.request("/api/serving-graph/experiments/" + unique_id, method="DELETE")


def test_console_monitor_dashboard(page, clients):
    console(page, clients, "监控大盘")
    expect(page.frame_locator("iframe").locator("body")).to_contain_text("API QPS by AB", timeout=60000)


@pytest.mark.cluster
@pytest.mark.parametrize("dag,label", [("openrec_daily_recall", "ITEM DAG"), ("openrec_daily_user_recall", "USER DAG")])
def test_console_dag_edit_publish_rollback(page, clients, dag, label):
    api = clients["console"]
    path = "/api/dag-configs/" + dag
    old = api.request(path)
    baseline = old["version"] or api.request(path + "/publish", old["config"])["version"]
    try:
        console(page, clients, "离线任务 DAG")
        page.get_by_role("button", name=label, exact=True).click()
        field = page.get_by_label("失败重试次数")
        expect(field).to_have_value(str(old["config"]["retries"]))
        # Rollback selects the latest *published* predecessor, not necessarily the active version.
        response(page, "/publish", lambda: page.get_by_role("button", name="校验并发布", exact=True).click(), "POST")
        field.fill("0" if old["config"]["retries"] else "1")
        response(page, "/publish", lambda: page.get_by_role("button", name="校验并发布", exact=True).click(), "POST")
        response(page, "/rollback", lambda: page.get_by_role("button", name="回滚配置", exact=True).click(), "POST")
        assert api.request(path)["config"] == old["config"]
    finally:
        with cleanup_guard():
            api.request(path + "/rollback", {"version": baseline})
            assert api.request(path)["config"] == old["config"]


@pytest.mark.cluster
def test_console_airflow_task_logs(page, clients):
    console(page, clients, "Airflow 自动化")
    page.locator(".dag-list button").filter(has_text="openrec_daily_recall").click()
    response(page, "/tasks", lambda: page.locator(".run").first.click())
    response(page, "/logs", lambda: page.locator(".task-row").first.click())


@pytest.mark.cluster
def test_console_recall_switch_rollback(page, clients):
    api = clients["console"]
    before = api.request("/api/recall/releases/hot")["active_indexes"]
    assert len(before) == 1
    try:
        console(page, clients, "召回索引")
        response(page, "/api/recall/releases/switch", lambda: page.locator("button.switch").first.click(), "POST")
        assert api.request("/api/recall/releases/hot")["active_indexes"] != before
        response(page, "/api/recall/releases/rollback", lambda: page.get_by_role("button", name="↶ 回滚上一版本", exact=True).click(), "POST")
        assert api.request("/api/recall/releases/hot")["active_indexes"] == before
    finally:
        with cleanup_guard():
            api.request("/api/recall/releases/switch", {"algorithm": "hot", "target_index": before[0]})
            assert api.request("/api/recall/releases/hot")["active_indexes"] == before


@pytest.mark.cluster
def test_console_analytics_query_refresh(page, clients):
    console(page, clients, "数据分析")
    expect(page.locator(".analytics-filter button").first).to_be_enabled(timeout=180000)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    page.get_by_label("开始日期").fill(today)
    page.get_by_label("结束日期").fill(today)
    for label in ["查询", "↻ 强制刷新"]:
        value = response(page, "/api/analytics/business", lambda: page.get_by_role("button", name=label, exact=True).click())
        assert "summary" in value and "daily" in value, value


@pytest.mark.cluster
def test_console_model_tabs_and_target(page, clients):
    console(page, clients, "Rank Model")
    for target in ["user", "item"]:
        response(page, "/api/models/releases/global?target_type=" + target,
                 lambda: page.get_by_label("排序目标", exact=True).select_option(target))
        for label in ["全局特征中心", "离线训练", "在线部署"]:
            page.get_by_role("button", name=label, exact=True).click()
            expect(page.locator(".notice.error")).to_have_count(0)
    # Lifecycle training/deployment is separately exercised by rank-model acceptance.


def test_console_experiment_controls(page, clients, unique_id):
    api = clients["console"]
    deleted = False
    try:
        console(page, clients, "Serving Graph")
        page.get_by_placeholder("实验名，例如 test1 或 rank_v2").fill(unique_id)
        response(page, "/api/serving-graph/experiments", lambda: page.get_by_role("button", name="新增实验", exact=True).click(), "POST")
        response(page, "/api/serving-graph/publish", lambda: page.get_by_role("button", name="发布实验 " + unique_id, exact=True).click(), "POST")
        row = page.locator(".experiment-list article").filter(has_text=unique_id)
        expect(row).to_be_visible()
        for label in ["启用", "关闭"]:
            response(page, "/enabled", lambda: row.get_by_role("button", name=label, exact=True).click(), "PUT")
        response(page, "/api/serving-graph/experiments/" + unique_id,
                 lambda: row.get_by_role("button", name="删除", exact=True).click(), "DELETE")
        deleted = True
        expect(row).to_have_count(0)
    finally:
        with cleanup_guard():
            if not deleted:
                api.request("/api/serving-graph/experiments/" + unique_id, method="DELETE")


def test_demo_browser_exposure_and_stay(user, clients, page, poll):
    demo(page, clients, user)
    state = lambda: clients["web"].request("/api/state", params={"userId": user, "scene": "scene_0"})
    poll(state, lambda value: value["counters"]["expose"] > 0)
    # Actual dwell is the behavior under test; not a substitute for waiting on network readiness.
    page.wait_for_timeout(1500)
    response(page, "/api/feedback", lambda: page.locator('#tabs [data-tab="hot"]').click(), "POST", "stay")
    poll(state, lambda value: value["counters"]["stay"] > 0)


@pytest.mark.cluster
def test_console_airflow_pause_resume(page, clients):
    api = clients["console"]
    path = "/api/airflow/dags/openrec_daily_recall"
    original = api.request(path)["dag"]["is_paused"]
    try:
        console(page, clients, "Airflow 自动化")
        page.locator(".dag-list button").filter(has_text="openrec_daily_recall").click()
        for paused in [not original, original]:
            label = "暂停" if paused else "启用"
            response(page, path, lambda: page.get_by_role("button", name=label, exact=True).click(), "PATCH")
            assert api.request(path)["dag"]["is_paused"] is paused
    finally:
        with cleanup_guard():
            api.request(path, {"is_paused": original}, method="PATCH")
            assert api.request(path)["dag"]["is_paused"] is original
