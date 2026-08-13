"""
Endpoint-level tests for the background scraping SSE stream.

Verifies that:
- A successful job delivers exactly one 'done' terminal event before the stream closes.
- A failing job delivers exactly one 'error' terminal event before the stream closes.
- The terminal event is always present even when the job finishes before the
  SSE generator's first poll (i.e. no race between status and event buffer).
"""

import json
import threading
import time
import unittest

# Patch hamta_personer before importing app so the scraper never runs Playwright.
import unittest.mock as mock

_mock_hamta = mock.MagicMock()

with mock.patch.dict("sys.modules", {}):
    import importlib, sys

    # Pre-patch at module level so app.py gets the mock on import.
    with mock.patch("scrapa_alla.hamta_personer", _mock_hamta):
        import app as flask_app


class SseStreamTest(unittest.TestCase):
    def setUp(self):
        flask_app.app.config["TESTING"] = True
        self.client = flask_app.app.test_client()
        # Reset all jobs between tests.
        with flask_app._jobs_lock:
            flask_app._jobs.clear()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _parse_sse(self, raw: bytes) -> list[dict]:
        """Parse a raw SSE byte stream into a list of {event, data} dicts."""
        events = []
        current = {}
        for line in raw.decode("utf-8").splitlines():
            if line.startswith("event:"):
                current["event"] = line[len("event:"):].strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line[len("data:"):].strip())
            elif line == "" and current:
                events.append(current)
                current = {}
        return events

    def _start_job(self, stad="Stockholm", kalla="1", max_antal=10) -> str:
        resp = self.client.post(
            "/scrape",
            data=json.dumps({"stad": stad, "kalla": kalla, "max_antal": max_antal}),
            content_type="application/json",
        )
        body = json.loads(resp.data)
        self.assertTrue(body["success"], body)
        return body["job_id"]

    def _collect_sse(self, job_id: str) -> list[dict]:
        resp = self.client.get(f"/scrape/events/{job_id}")
        return self._parse_sse(resp.data)

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_successful_job_delivers_done_event(self):
        """A completed scrape produces exactly one 'done' terminal event."""
        _mock_hamta.return_value = [
            {"namn": "Anna Svensson", "telefon": "0701234567",
             "adress": "Storgatan 1", "stad": "Stockholm", "kalla": "Eniro"}
        ]

        job_id = self._start_job()

        # Wait for the background thread to finish before reading the stream.
        deadline = time.time() + 5
        while time.time() < deadline:
            with flask_app._jobs_lock:
                status = flask_app._jobs.get(job_id, {}).get("status")
            if status == "done":
                break
            time.sleep(0.05)

        events = self._collect_sse(job_id)
        terminal = [e for e in events if e.get("event") == "done"]

        self.assertEqual(len(terminal), 1, f"Expected exactly 1 'done' event; got: {events}")
        self.assertEqual(terminal[0]["data"]["count"], 1)
        self.assertEqual(terminal[0]["data"]["results"][0]["name"], "Anna Svensson")

    def test_failed_job_delivers_error_event(self):
        """A scrape that raises an exception produces exactly one 'error' terminal event."""
        _mock_hamta.side_effect = RuntimeError("Playwright crashed")

        job_id = self._start_job()

        deadline = time.time() + 5
        while time.time() < deadline:
            with flask_app._jobs_lock:
                status = flask_app._jobs.get(job_id, {}).get("status")
            if status == "error":
                break
            time.sleep(0.05)

        events = self._collect_sse(job_id)
        terminal = [e for e in events if e.get("event") == "error"]

        self.assertEqual(len(terminal), 1, f"Expected exactly 1 'error' event; got: {events}")
        self.assertIn("Playwright crashed", terminal[0]["data"]["message"])

        # Reset side_effect for subsequent tests.
        _mock_hamta.side_effect = None

    def test_no_race_when_job_finishes_before_first_poll(self):
        """Terminal event must be present even if job completes before SSE first polls."""
        _mock_hamta.return_value = []

        job_id = self._start_job()

        # Spin until done — the SSE generator hasn't opened yet.
        deadline = time.time() + 5
        while time.time() < deadline:
            with flask_app._jobs_lock:
                status = flask_app._jobs.get(job_id, {}).get("status")
            if status == "done":
                break
            time.sleep(0.02)

        self.assertEqual(status, "done", "Job did not finish in time")

        # NOW open the stream — the generator sees status=done on its first iteration.
        events = self._collect_sse(job_id)
        terminal = [e for e in events if e.get("event") == "done"]

        self.assertEqual(
            len(terminal), 1,
            f"Expected 'done' event even when stream opens after job finishes; got: {events}"
        )

    def test_invalid_source_rejected_before_job_starts(self):
        """POSTing an unknown källa returns a validation error without starting a job."""
        resp = self.client.post(
            "/scrape",
            data=json.dumps({"stad": "Malmö", "kalla": "99", "max_antal": 10}),
            content_type="application/json",
        )
        body = json.loads(resp.data)
        self.assertFalse(body["success"])
        self.assertIn("Ogiltig källa", body["error"])

    def test_missing_city_rejected(self):
        resp = self.client.post(
            "/scrape",
            data=json.dumps({"stad": "", "kalla": "1", "max_antal": 10}),
            content_type="application/json",
        )
        body = json.loads(resp.data)
        self.assertFalse(body["success"])


if __name__ == "__main__":
    unittest.main()
