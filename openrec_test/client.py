"""Strict HTTP assertions and bounded polling for asynchronous projections."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def eventually(probe, predicate, timeout=90, interval=0.5):
    deadline = time.monotonic() + timeout
    while True:
        value = probe()  # Unexpected HTTP/business errors must not be hidden by retries.
        if predicate(value):
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"projection timeout after {timeout}s; last value: {value!r}")
        time.sleep(min(interval, remaining))


def business(value):
    assert value.get("code") == 200 and value.get("status") is True, value
    return value.get("data")


class Client:
    def __init__(self, base, timeout=30, journal=None):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.journal = journal
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None, method=None, expected=200, params=None):
        if params:
            path += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(self.base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method=method)
        started = time.monotonic()
        try:
            response = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.code
            raw = response.read().decode("utf-8", errors="replace")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        if self.journal:
            with self.journal.open("a") as output:
                output.write(json.dumps({"method": req.get_method(), "path": path, "status": status,
                    "seconds": time.monotonic() - started, "request": body, "response": value}, ensure_ascii=False) + "\n")
        assert status == expected, f"{req.get_method()} {path}: HTTP {status}, expected {expected}: {raw[:3000]}"
        return value
