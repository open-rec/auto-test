import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from openrec_test.client import Client, business, eventually
from openrec_test.lifecycle import CleanupError, cleanup_guard


class Response(io.BytesIO):
    code = 200


class ClientTests(unittest.TestCase):
    def test_journal_get_post_and_query_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Client("http://example.invalid/", journal=Path(directory) / "http.jsonl")
            client.opener = Mock()
            for body, method in [(None, "GET"), ({"name": "测试"}, "POST")]:
                client.opener.open.return_value = Response(b'{"ok":true}')
                self.assertEqual(client.request("/api", body, params={"id": "a &b"}), {"ok": True})
                req = client.opener.open.call_args.args[0]
                self.assertEqual(req.get_method(), method)
                self.assertIn("id=a+%26b", req.full_url)
            entries = [json.loads(line) for line in client.journal.read_text().splitlines()]
            self.assertEqual([entry["method"] for entry in entries], ["GET", "POST"])

    def test_expected_http_error_and_failure_body(self):
        client = Client("http://example.invalid")
        client.opener = Mock()
        def error():
            return urllib.error.HTTPError(client.base, 404, "missing", {}, io.BytesIO(b'{"detail":"missing"}'))
        client.opener.open.side_effect = error()
        self.assertEqual(client.request("/x", expected=404), {"detail": "missing"})
        client.opener.open.side_effect = error()
        with self.assertRaisesRegex(AssertionError, "HTTP 404"):
            client.request("/x")

    def test_http_200_is_not_business_success(self):
        with self.assertRaises(AssertionError):
            business({"code": 200, "status": False})
        self.assertEqual(business({"code": 200, "status": True, "data": [1]}), [1])

    def test_poll_waits_for_projection_without_retrying_exceptions(self):
        with patch("openrec_test.client.time.sleep"):
            probe = Mock(side_effect=[{}, {"id": "one"}])
            self.assertEqual(eventually(probe, bool), {"id": "one"})
            self.assertEqual(probe.call_count, 2)
        probe = Mock(side_effect=AssertionError("HTTP failure"))
        with self.assertRaisesRegex(AssertionError, "HTTP failure"):
            eventually(probe, bool)
        self.assertEqual(probe.call_count, 1)
        with self.assertRaisesRegex(AssertionError, "last value"):
            eventually(lambda: [], bool, timeout=0)


class LifecycleTests(unittest.TestCase):
    def test_cleanup_failure_preserves_original_exception(self):
        try:
            with cleanup_guard():
                raise AssertionError("original version was not restored")
        except CleanupError as error:
            self.assertIsInstance(error.__cause__, AssertionError)
            self.assertIn("original version", str(error))
        else:
            self.fail("cleanup failure must propagate")

    def test_successful_cleanup_does_not_hide_body_failure(self):
        with self.assertRaisesRegex(ValueError, "product failure"):
            try:
                raise ValueError("product failure")
            finally:
                with cleanup_guard():
                    pass
