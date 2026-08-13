"""
Flask web server for the Swedish person-data scraper.
Serves the browser UI and exposes a /scrape endpoint.
"""

import csv
import io
import json
import os
import threading
import time
import uuid

from flask import Flask, jsonify, render_template, request, Response

from scrapa_alla import hamta_personer, hamta_fran_url, KALLOR

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret")

# ── Background job store ─────────────────────────────────────────────────────
# Each job is stored as a dict with keys:
#   status      : "running" | "done" | "error"
#   events      : list of SSE-formatted strings buffered for the stream
#   results     : list of result dicts (filled when done)
#   error       : error string (set on failure)
#   created_at  : timestamp for cleanup
_jobs: dict = {}
_jobs_lock = threading.Lock()

_JOB_TTL_SECONDS = 3600  # Keep jobs for 1 hour


def _cleanup_old_jobs():
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items() if j["created_at"] < cutoff]
        for jid in stale:
            del _jobs[jid]


def _append_event(job_id: str, event_type: str, data: dict) -> None:
    """Append an SSE-formatted event to the job's event buffer (caller must hold no lock)."""
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["events"].append(payload)


def _append_terminal_event(job_id: str, event_type: str, data: dict,
                           terminal_status: str, extra: dict | None = None) -> None:
    """Atomically set terminal status AND append the terminal event in one lock.

    This prevents the SSE generator from observing a terminal status before the
    corresponding terminal event has been written to the event buffer.
    """
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = terminal_status
            if extra:
                _jobs[job_id].update(extra)
            _jobs[job_id]["events"].append(payload)


def _run_scrape_job(job_id: str, stad: str, kalla_val: str, max_antal: int):
    """Background worker: run the scraper and push SSE events."""

    block_state: dict = {"reason": None}

    def progress(event_type: str, **kwargs):
        if event_type == "blocked":
            block_state["reason"] = kwargs.get("anledning", "Scraping blockerades.")
        _append_event(job_id, event_type, kwargs)

    try:
        personer = hamta_personer(
            stad,
            kalla_val,
            max_antal,
            progress_callback=progress,
        )

        results = [
            {
                "name": p.get("namn", ""),
                "phone": p.get("telefon", ""),
                "address": p.get("adress", ""),
                "city": p.get("stad", ""),
                "source": p.get("kalla", ""),
            }
            for p in personer
        ]

        # Build the terminal event payload; include block_reason when applicable
        # so the frontend can distinguish "blocked (0 results)" from "genuinely empty".
        done_payload: dict = {"count": len(results), "results": results}
        if block_state["reason"]:
            done_payload["block_reason"] = block_state["reason"]

        # Atomically mark done and append the terminal event so the SSE
        # generator cannot see status="done" before the event is in the buffer.
        _append_terminal_event(
            job_id, "done",
            done_payload,
            terminal_status="done",
            extra={"results": results, "block_reason": block_state["reason"]},
        )

    except Exception as exc:
        _append_terminal_event(
            job_id, "error",
            {"message": str(exc)},
            terminal_status="error",
            extra={"error": str(exc)},
        )


def _run_url_scrape_job(job_id: str, start_url: str, max_antal: int):
    """Background worker for URL-mode: generic scraper from a pasted URL."""

    block_state: dict = {"reason": None}

    def progress(event_type: str, **kwargs):
        if event_type == "blocked":
            block_state["reason"] = kwargs.get("anledning", "Scraping blockerades.")
        _append_event(job_id, event_type, kwargs)

    try:
        personer = hamta_fran_url(
            start_url,
            max_antal,
            progress_callback=progress,
        )

        results = [
            {
                "name":    p.get("namn", ""),
                "phone":   p.get("telefon", ""),
                "address": p.get("adress", ""),
                "city":    p.get("stad", ""),
                "source":  p.get("kalla", ""),
            }
            for p in personer
        ]

        done_payload: dict = {"count": len(results), "results": results}
        if block_state["reason"]:
            done_payload["block_reason"] = block_state["reason"]

        _append_terminal_event(
            job_id, "done",
            done_payload,
            terminal_status="done",
            extra={"results": results, "block_reason": block_state["reason"]},
        )

    except Exception as exc:
        _append_terminal_event(
            job_id, "error",
            {"message": str(exc)},
            terminal_status="error",
            extra={"error": str(exc)},
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", kallor=KALLOR)


@app.route("/scrape", methods=["POST"])
def scrape():
    """Start a background scraping job; return a job_id immediately.

    Accepts two modes:
      • Standard mode:  { stad, kalla, max_antal }
      • URL mode:       { start_url, max_antal }
    """
    _cleanup_old_jobs()

    try:
        data = request.get_json(force=True)
        max_antal = int(data.get("max_antal") or 100)

        if max_antal <= 0:
            return jsonify({"success": False, "error": "max_antal måste vara större än 0."}), 400

        start_url = (data.get("start_url") or "").strip()

        if start_url:
            # ── URL mode ─────────────────────────────────────────────────────
            if not start_url.startswith("http"):
                return jsonify({"success": False, "error": "URL måste börja med http:// eller https://"}), 400

            job_id = str(uuid.uuid4())
            with _jobs_lock:
                _jobs[job_id] = {
                    "status": "running",
                    "events": [],
                    "results": [],
                    "error": None,
                    "created_at": time.time(),
                }

            thread = threading.Thread(
                target=_run_url_scrape_job,
                args=(job_id, start_url, max_antal),
                daemon=True,
            )
            thread.start()
            return jsonify({"success": True, "job_id": job_id})

        else:
            # ── Standard mode ─────────────────────────────────────────────────
            stad = (data.get("stad") or "").strip()
            kalla_val = str(data.get("kalla") or "").strip()

            if not stad:
                return jsonify({"success": False, "error": "Du måste ange en stad."}), 400
            if kalla_val not in KALLOR:
                return jsonify({"success": False, "error": f"Ogiltig källa: {kalla_val}"}), 400

            job_id = str(uuid.uuid4())
            with _jobs_lock:
                _jobs[job_id] = {
                    "status": "running",
                    "events": [],
                    "results": [],
                    "error": None,
                    "created_at": time.time(),
                }

            thread = threading.Thread(
                target=_run_scrape_job,
                args=(job_id, stad, kalla_val, max_antal),
                daemon=True,
            )
            thread.start()
            return jsonify({"success": True, "job_id": job_id})

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/scrape/events/<job_id>")
def scrape_events(job_id: str):
    """Server-Sent Events stream for a running scrape job."""

    def generate():
        cursor = 0
        # Send a heartbeat comment immediately so the browser connects
        yield ": connected\n\n"

        while True:
            # Single lock acquisition: read events and status atomically so we
            # never observe a terminal status before the terminal event is in
            # the buffer (the worker guarantees both are written under one lock).
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    yield f"event: error\ndata: {json.dumps({'message': 'Jobbet hittades inte.'})}\n\n"
                    return
                new_events = job["events"][cursor:]
                cursor += len(new_events)
                status = job["status"]

            for ev in new_events:
                yield ev

            if status in ("done", "error"):
                return

            time.sleep(0.3)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/download", methods=["POST"])
def download():
    """Convert POSTed JSON results to a CSV file for download."""
    try:
        data = request.get_json(force=True)
        results = data.get("results", [])

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["name", "phone", "address", "city", "source"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)

        csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM so Excel opens correctly
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=personer.csv"},
        )
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
