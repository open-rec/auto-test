"""Explicit live-test fixtures; these tests are never part of the default unit run."""
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest

from openrec_test.client import Client, business, eventually
from openrec_test.lifecycle import CleanupError


def pytest_addoption(parser):
    group = parser.getgroup("openrec")
    group.addoption("--mode", choices=["standalone", "cluster"], required=True)
    group.addoption("--artifacts", type=Path, required=True)
    group.addoption("--projection-timeout", type=float, default=90)


def pytest_collection_modifyitems(config, items):
    if config.getoption("--mode") == "standalone":
        for item in items:
            if item.get_closest_marker("cluster"):
                item.add_marker(pytest.mark.skip(reason="cluster capability disabled in standalone"))


@pytest.fixture(scope="session")
def mode(pytestconfig):
    return pytestconfig.getoption("--mode")


@pytest.fixture
def artifacts(request):
    root = request.config.getoption("--artifacts")
    name = request.node.name[:90].replace("/", "_")
    path = root / (name + "-" + hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:8])
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def clients(artifacts):
    return {name: Client(os.environ.get(f"OPENREC_TEST_{name.upper()}_URL", default),
                        timeout=180 if name == "console" else 30,
                        journal=artifacts / f"{name}-http.jsonl")
            for name, default in {"server": "http://127.0.0.1:13579",
                "web": "http://127.0.0.1:12345", "console": "http://127.0.0.1:8095",
                "rank": "http://127.0.0.1:8123"}.items()}


@pytest.fixture(autouse=True, scope="session")
def correct_mode(mode):
    console = Client(os.environ.get("OPENREC_TEST_CONSOLE_URL", "http://127.0.0.1:8095"))
    assert console.request("/api/config")["mode"] == mode, "wrong deployment mode; refusing mutations"


@pytest.fixture
def poll(pytestconfig):
    def wait(probe, predicate):
        return eventually(probe, predicate, timeout=pytestconfig.getoption("--projection-timeout"))
    return wait


@pytest.fixture
def unique_id():
    return "autotest_" + uuid.uuid4().hex


@pytest.fixture
def push(clients):
    def send(kind, cmd, rows):
        return business(clients["server"].request("/api/push/" + kind,
            {"requestId": uuid.uuid4().hex, "body": {"cmd": cmd, "data": rows}}))
    return send


@pytest.fixture
def user(unique_id, push, clients, poll):
    # Register teardown before the first write: acceptance could time out after committing.
    try:
        push("user", "INSERT", [{"id": unique_id, "name": "auto-test", "age": 30, "tags": ["test"]}])
        poll(lambda: business(clients["server"].request("/api/query/user/" + unique_id)),
             lambda data: data and data.get("id") == unique_id)
        yield unique_id
    finally:
        push("user", "DELETE", [{"id": unique_id}])
        poll(lambda: business(clients["server"].request("/api/query/user/" + unique_id)), lambda data: not data)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "report_" + report.when, report)
    cleanup_failed = (call.excinfo is not None and isinstance(call.excinfo.value, CleanupError))
    if cleanup_failed or (report.when == "teardown" and report.failed):
        item.user_properties.append(("cleanup_failed", "true"))
        report.user_properties.append(("cleanup_failed", "true"))
        item.session.shouldstop = "state cleanup failed; refusing subsequent mutations"


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, artifacts, request):
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    context.tracing.start(screenshots=True, snapshots=True, sources=True)
    page = context.new_page()
    page.set_default_timeout(30000)
    errors, network, console_messages = [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: console_messages.append({"type": message.type, "text": message.text})
            if message.type in ("warning", "error") else None)
    page.on("response", lambda response: network.append({"url": response.url, "status": response.status})
            if response.status >= 400 else None)
    page.on("requestfailed", lambda req: network.append({"url": req.url, "failure": req.failure}))
    page.on("dialog", lambda dialog: dialog.accept())
    try:
        yield page
    finally:
        try:
            (artifacts / "browser-errors.json").write_text(json.dumps({"javascript": errors, "network": network, "console": console_messages}, indent=2))
            # Keep evidence for successful and failed tests; isolated contexts prevent state leaks.
            if not page.is_closed():
                page.screenshot(path=str(artifacts / "page.png"), full_page=True)
                (artifacts / "page.html").write_text(page.content())
            context.tracing.stop(path=str(artifacts / "trace.zip"))
        finally:
            context.close()
        assert not errors, f"uncaught browser errors: {errors}"
