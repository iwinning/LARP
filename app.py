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
_jobs: dict = {}
_jobs_lock = threading.Lock()
_JOB_TTL_SECONDS = 3600


def _cleanup_old_jobs():
    cutoff = time.time() - _JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [jid for jid, j in _jobs.items() if j["created_at"] < cutoff]
        for jid in stale:
            del _jobs[jid]


def _append_event(job_id: str, event_type: str, data: dict) -> None:
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["events"].append(payload)


def _append_terminal_event(job_id: str, event_type: str, data: dict,
                            terminal_status: str, extra: dict | None = None) -> None:
    payload = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = terminal_status
            if extra:
                _jobs[job_id].update(extra)
            _jobs[job_id]["events"].append(payload)


# ── Filter helpers ────────────────────────────────────────────────────────────

def _is_lagenhet(adress: str) -> bool:
    a = adress.lower()
    return "lgh" in a or "läg" in a or "apt" in a


def _filter_person(p: dict, bara_med_telefon: bool, housing_type: str) -> bool:
    """Return True if the person passes all active filters."""
    if bara_med_telefon and (not p.get("phone") or p.get("phone") == "Saknas"):
        return False
    if housing_type == "villa":
        adress = p.get("address", "")
        if not adress or adress == "Saknas" or _is_lagenhet(adress):
            return False
    elif housing_type == "lagenhet":
        adress = p.get("address", "")
        if not adress or adress == "Saknas" or not _is_lagenhet(adress):
            return False
    return True


def _person_to_result(p: dict, override_city: str = "") -> dict:
    return {
        "name":         p.get("namn", ""),
        "phone":        p.get("telefon", ""),
        "address":      p.get("adress", ""),
        "city":         override_city or p.get("stad", ""),
        "housing_type": "Lägenhet" if _is_lagenhet(p.get("adress", "")) else "Villa",
        "source":       p.get("kalla", ""),
    }


# ── Background workers ────────────────────────────────────────────────────────

def _run_scrape_job(job_id: str, stader: list[str], kalla_val: str,
                    max_antal: int, bara_med_telefon: bool, housing_type: str):
    """Scrape multiple cities sequentially, stream progress events."""

    alla_resultat: list[dict] = []
    block_reason: str | None = None

    for stad_idx, stad in enumerate(stader):
        _append_event(job_id, "city_start", {
            "stad": stad,
            "stad_nr": stad_idx + 1,
            "antal_stader": len(stader),
            "totalt": len(alla_resultat),
        })

        def progress(event_type: str, **kwargs):
            nonlocal block_reason
            if event_type == "blocked":
                block_reason = kwargs.get("anledning", "Scraping blockerades.")
            _append_event(job_id, event_type, {"stad": stad, **kwargs})

        try:
            # Merinfo already has tel:-links in search results — no profile fetches needed
            personer = hamta_personer(
                stad, kalla_val, max_antal,
                max_profil_anrop=0,
                progress_callback=progress,
            )
        except Exception as exc:
            _append_event(job_id, "city_error", {"stad": stad, "fel": str(exc)})
            continue

        for p in personer:
            r = _person_to_result(p, override_city=stad)
            if _filter_person(r, bara_med_telefon, housing_type):
                alla_resultat.append(r)

        _append_event(job_id, "city_done", {
            "stad": stad,
            "stad_nr": stad_idx + 1,
            "antal_stader": len(stader),
            "hittade": len([p for p in personer
                            if _filter_person(_person_to_result(p, stad),
                                              bara_med_telefon, housing_type)]),
            "totalt": len(alla_resultat),
        })

        if len(alla_resultat) >= max_antal:
            break

    done_payload: dict = {"count": len(alla_resultat), "results": alla_resultat}
    if block_reason:
        done_payload["block_reason"] = block_reason

    _append_terminal_event(
        job_id, "done", done_payload,
        terminal_status="done",
        extra={"results": alla_resultat, "block_reason": block_reason},
    )


def _run_url_scrape_job(job_id: str, start_url: str, max_antal: int,
                        bara_med_telefon: bool, housing_type: str,
                        max_profil_anrop: int = 200):
    """Background worker for URL-mode."""

    block_reason: str | None = None

    def progress(event_type: str, **kwargs):
        nonlocal block_reason
        if event_type == "blocked":
            block_reason = kwargs.get("anledning", "Scraping blockerades.")
        _append_event(job_id, event_type, kwargs)

    try:
        personer = hamta_fran_url(
            start_url, max_antal,
            max_profil_anrop=max_profil_anrop,
            progress_callback=progress,
        )

        results = []
        for p in personer:
            r = _person_to_result(p)
            if _filter_person(r, bara_med_telefon, housing_type):
                results.append(r)

        done_payload: dict = {"count": len(results), "results": results}
        if block_reason:
            done_payload["block_reason"] = block_reason

        _append_terminal_event(
            job_id, "done", done_payload,
            terminal_status="done",
            extra={"results": results, "block_reason": block_reason},
        )

    except Exception as exc:
        _append_terminal_event(
            job_id, "error", {"message": str(exc)},
            terminal_status="error",
            extra={"error": str(exc)},
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", kallor=KALLOR)


@app.route("/scrape", methods=["POST"])
def scrape():
    """Start a background scraping job.

    Standard mode: { stader, kalla, max_antal, bara_med_telefon, housing_type }
    URL mode:      { start_url, max_antal, bara_med_telefon, housing_type }
    """
    _cleanup_old_jobs()

    try:
        data = request.get_json(force=True)
        max_antal        = int(data.get("max_antal") or 100)
        bara_med_telefon = bool(data.get("bara_med_telefon", False))
        housing_type     = str(data.get("housing_type") or "alla").strip()

        if max_antal <= 0:
            return jsonify({"success": False,
                            "error": "max_antal måste vara större än 0."}), 400
        if housing_type not in ("alla", "villa", "lagenhet"):
            housing_type = "alla"

        start_url = (data.get("start_url") or "").strip()

        job_id = str(uuid.uuid4())
        with _jobs_lock:
            _jobs[job_id] = {
                "status": "running",
                "events": [],
                "results": [],
                "error": None,
                "created_at": time.time(),
            }

        if start_url:
            # ── URL mode ─────────────────────────────────────────────────────
            if not start_url.startswith("http"):
                return jsonify({"success": False,
                                "error": "URL måste börja med http:// eller https://"}), 400

            thread = threading.Thread(
                target=_run_url_scrape_job,
                args=(job_id, start_url, max_antal, bara_med_telefon, housing_type),
                daemon=True,
            )

        else:
            # ── Standard mode ─────────────────────────────────────────────────
            # Accept either "stader" (list) or legacy "stad" (string)
            stader_raw = data.get("stader") or data.get("stad") or ""
            if isinstance(stader_raw, list):
                stader = [s.strip() for s in stader_raw if str(s).strip()]
            else:
                stader = [s.strip() for s in str(stader_raw).replace("\n", ",").split(",")
                          if s.strip()]

            kalla_val = str(data.get("kalla") or "").strip()

            if not stader:
                return jsonify({"success": False,
                                "error": "Du måste ange minst en stad/ort."}), 400
            if kalla_val not in KALLOR:
                return jsonify({"success": False,
                                "error": f"Ogiltig källa: {kalla_val}"}), 400

            thread = threading.Thread(
                target=_run_scrape_job,
                args=(job_id, stader, kalla_val, max_antal,
                      bara_med_telefon, housing_type),
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
        yield ": connected\n\n"

        while True:
            with _jobs_lock:
                job = _jobs.get(job_id)
                if job is None:
                    yield (f"event: error\ndata: "
                           f"{json.dumps({'message': 'Jobbet hittades inte.'})}\n\n")
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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
            fieldnames=["name", "phone", "address", "city", "housing_type", "source"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)

        csv_bytes = output.getvalue().encode("utf-8-sig")  # BOM for Excel
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=personer.csv"},
        )
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
