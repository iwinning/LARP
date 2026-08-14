"""
Flask web server for the Swedish person-data scraper.
Serves the browser UI and exposes a /scrape endpoint.
"""

import csv
import glob
import io
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

from flask import Flask, jsonify, render_template, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from scrapa_alla import hamta_personer, hamta_fran_url, KALLOR

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret")

# ── History storage ───────────────────────────────────────────────────────────
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "resultat")
MAX_HISTORY = 20
os.makedirs(HISTORY_DIR, exist_ok=True)

# ── Schedule storage ──────────────────────────────────────────────────────────
SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "schema.json")
_schedules_lock = threading.Lock()

# Tracks how many scheduled jobs are currently running
_scheduled_running: set = set()
_scheduled_running_lock = threading.Lock()


def _load_schedules() -> list:
    """Load schedules from disk. Returns list of schedule dicts."""
    if not os.path.exists(SCHEDULE_FILE):
        return []
    try:
        with open(SCHEDULE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_schedules(schedules: list) -> None:
    """Persist schedule list to disk."""
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)


# ── APScheduler ───────────────────────────────────────────────────────────────
_scheduler = BackgroundScheduler(timezone="Europe/Stockholm")


def _run_scheduled_job(schedule_id: str) -> None:
    """Called by APScheduler at the configured time. Runs a full scrape and saves to history."""
    with _schedules_lock:
        schedules = _load_schedules()
    schedule = next((s for s in schedules if s["id"] == schedule_id), None)
    if schedule is None:
        return

    job_id = str(uuid.uuid4())
    stader = schedule.get("stader", [])
    kalla_val = schedule.get("kalla", "")
    max_antal = int(schedule.get("max_antal", 5000))
    bara_med_telefon = bool(schedule.get("bara_med_telefon", False))
    housing_type = str(schedule.get("housing_type", "alla"))

    if not stader or kalla_val not in KALLOR:
        return

    kalla_namn = KALLOR[kalla_val].get("namn", kalla_val)
    job_metadata = {
        "mode": "standard",
        "stader": stader,
        "kalla": kalla_val,
        "kalla_namn": kalla_namn,
        "max_antal": max_antal,
        "bara_med_telefon": bara_med_telefon,
        "housing_type": housing_type,
        "scheduled": True,
        "schedule_id": schedule_id,
    }

    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "events": [],
            "results": [],
            "error": None,
            "created_at": time.time(),
            "metadata": job_metadata,
        }

    with _scheduled_running_lock:
        _scheduled_running.add(job_id)

    try:
        _run_scrape_job(job_id, stader, kalla_val, max_antal, bara_med_telefon, housing_type)
    finally:
        with _scheduled_running_lock:
            _scheduled_running.discard(job_id)


def _register_schedule(schedule: dict) -> None:
    """Add a cron job to the scheduler for the given schedule dict."""
    tid = schedule.get("tid", "02:00")
    try:
        hour, minute = tid.split(":")
    except ValueError:
        hour, minute = "2", "0"
    _scheduler.add_job(
        _run_scheduled_job,
        trigger=CronTrigger(hour=int(hour), minute=int(minute)),
        args=[schedule["id"]],
        id=schedule["id"],
        replace_existing=True,
    )


def _apply_all_schedules() -> None:
    """Load schedules from disk and register each in the scheduler."""
    for s in _load_schedules():
        try:
            _register_schedule(s)
        except Exception:
            pass


# Start scheduler and register existing schedules
_scheduler.start()
_apply_all_schedules()


def _save_job_to_history(job_id: str, results: list, metadata: dict) -> None:
    """Save completed job results + metadata to disk. Prune if > MAX_HISTORY."""
    record = {
        "id": job_id,
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "count": len(results),
        "metadata": metadata,
        "results": results,
    }
    path = os.path.join(HISTORY_DIR, f"{job_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False)

    # Prune oldest entries if we exceed the cap
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")),
                   key=os.path.getmtime)
    while len(files) > MAX_HISTORY:
        try:
            os.remove(files.pop(0))
        except OSError:
            pass


def _list_history() -> list:
    """Return summary list of saved runs, newest first."""
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")),
                   key=os.path.getmtime, reverse=True)
    entries = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
            entries.append({
                "id":       rec.get("id", ""),
                "saved_at": rec.get("saved_at", ""),
                "count":    rec.get("count", 0),
                "metadata": rec.get("metadata", {}),
            })
        except Exception:
            pass
    return entries


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
    results_to_save = None
    metadata_to_save = None
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["status"] = terminal_status
            if extra:
                _jobs[job_id].update(extra)
            _jobs[job_id]["events"].append(payload)
            if terminal_status == "done":
                results_to_save = _jobs[job_id].get("results", [])
                metadata_to_save = _jobs[job_id].get("metadata", {})

    if terminal_status == "done" and results_to_save is not None:
        try:
            _save_job_to_history(job_id, results_to_save, metadata_to_save or {})
        except Exception:
            pass


# ── Filter helpers ────────────────────────────────────────────────────────────

def _is_lagenhet(adress: str) -> bool:
    a = adress.lower()
    return "lgh" in a or "läg" in a or "apt" in a


def _is_postnummer(s: str) -> bool:
    """Return True if s looks like a Swedish postal code (5 digits, opt. space)."""
    return bool(re.fullmatch(r"\d{3}\s?\d{2}", s.strip()))


def _normera_postnummer(s: str) -> str:
    """Normalize postal code to 'XXX XX' format (with space)."""
    digits = re.sub(r"\s", "", s.strip())
    return f"{digits[:3]} {digits[3:]}"


def _adress_matchar_postnummer(adress: str, postnummer: str) -> bool:
    """Return True if address contains the given postal code (handles both formats)."""
    normerat = _normera_postnummer(postnummer)   # e.g. "168 56"
    kompakt  = normerat.replace(" ", "")          # e.g. "16856"
    adress_lower = adress.lower()
    return normerat.lower() in adress_lower or kompakt in adress_lower


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
        "age":          p.get("alder", ""),
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
        kalla_cfg = KALLOR.get(kalla_val, {})
        wait_ms = kalla_cfg.get("wait_ms", 10000)
        _append_event(job_id, "city_start", {
            "stad": stad,
            "stad_nr": stad_idx + 1,
            "antal_stader": len(stader),
            "totalt": len(alla_resultat),
            "wait_sek": round(wait_ms / 1000),
        })

        def progress(event_type: str, **kwargs):
            nonlocal block_reason
            if event_type == "blocked":
                block_reason = kwargs.get("anledning", "Scraping blockerades.")
            _append_event(job_id, event_type, {"stad": stad, **kwargs})

        # Rådata-gräns: om filtret är aktivt måste vi scrapa fler råposter
        # för att nå max_antal filtrerade resultat.
        # Telefonnummer-täckning på Merinfo är ~15-25 % → multiplicera med 10.
        # Boendetyp-filter skär ~50 % → multiplicera med 3.
        remaining = max_antal - len(alla_resultat)
        if bara_med_telefon and housing_type in ("villa", "lagenhet"):
            raw_limit = min(remaining * 15, 50000)
        elif bara_med_telefon:
            raw_limit = min(remaining * 10, 50000)
        elif housing_type in ("villa", "lagenhet"):
            raw_limit = min(remaining * 3, 50000)
        else:
            raw_limit = remaining

        try:
            # Merinfo already has tel:-links in search results — no profile fetches needed
            personer = hamta_personer(
                stad, kalla_val, raw_limit,
                max_profil_anrop=0,
                progress_callback=progress,
            )
        except Exception as exc:
            _append_event(job_id, "city_error", {"stad": stad, "fel": str(exc)})
            continue

        stad_ar_postnr = _is_postnummer(stad)
        hittade = 0
        for p in personer:
            if len(alla_resultat) >= max_antal:
                break
            r = _person_to_result(p, override_city=stad)
            # Om söktermen är ett postnummer: filtrera bort adresser som
            # inte tillhör exakt det postnumret (Merinfo returnerar hela området).
            if stad_ar_postnr and not _adress_matchar_postnummer(r["address"], stad):
                continue
            if _filter_person(r, bara_med_telefon, housing_type):
                alla_resultat.append(r)
                hittade += 1

        _append_event(job_id, "city_done", {
            "stad": stad,
            "stad_nr": stad_idx + 1,
            "antal_stader": len(stader),
            "hittade": hittade,
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
                        max_profil_anrop: int = 200,
                        wait_ms: int = 10000):
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
            wait_ms=wait_ms,
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

        if start_url:
            # ── URL mode ─────────────────────────────────────────────────────
            if not start_url.startswith("http"):
                return jsonify({"success": False,
                                "error": "URL måste börja med http:// eller https://"}), 400

            wait_per_page = int(data.get("wait_per_page") or 10)
            wait_per_page = max(3, min(30, wait_per_page))
            wait_ms = wait_per_page * 1000

            job_metadata = {
                "mode": "url",
                "start_url": start_url,
                "max_antal": max_antal,
                "bara_med_telefon": bara_med_telefon,
                "housing_type": housing_type,
                "wait_per_page": wait_per_page,
            }
            with _jobs_lock:
                _jobs[job_id] = {
                    "status": "running",
                    "events": [],
                    "results": [],
                    "error": None,
                    "created_at": time.time(),
                    "metadata": job_metadata,
                }

            thread = threading.Thread(
                target=_run_url_scrape_job,
                args=(job_id, start_url, max_antal, bara_med_telefon, housing_type),
                kwargs={"wait_ms": wait_ms},
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

            kalla_namn = KALLOR[kalla_val].get("namn", kalla_val) if kalla_val in KALLOR else kalla_val
            job_metadata = {
                "mode": "standard",
                "stader": stader,
                "kalla": kalla_val,
                "kalla_namn": kalla_namn,
                "max_antal": max_antal,
                "bara_med_telefon": bara_med_telefon,
                "housing_type": housing_type,
            }
            with _jobs_lock:
                _jobs[job_id] = {
                    "status": "running",
                    "events": [],
                    "results": [],
                    "error": None,
                    "created_at": time.time(),
                    "metadata": job_metadata,
                }

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


@app.route("/history")
def history():
    """Return list of saved scraping runs (newest first, max 20)."""
    return jsonify(_list_history())


@app.route("/history/<run_id>/csv")
def history_csv(run_id: str):
    """Return CSV for a saved scraping run."""
    # Sanitize: only allow UUID-like IDs
    safe_id = "".join(c for c in run_id if c.isalnum() or c == "-")
    path = os.path.join(HISTORY_DIR, f"{safe_id}.json")
    if not os.path.exists(path):
        return jsonify({"success": False, "error": "Körning hittades inte."}), 404
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        results = record.get("results", [])

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["name", "phone", "age", "address", "city", "housing_type", "source"],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)

        saved_at = record.get("saved_at", "")[:10]  # YYYY-MM-DD
        filename = f"personer_{saved_at}.csv"
        csv_bytes = output.getvalue().encode("utf-8-sig")
        return Response(
            csv_bytes,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/schedule", methods=["GET"])
def get_schedules():
    """Return all saved schedules."""
    with _schedules_lock:
        schedules = _load_schedules()
    return jsonify(schedules)


@app.route("/schedule/status")
def schedule_status():
    """Return whether any scheduled background job is currently running."""
    with _scheduled_running_lock:
        running_ids = list(_scheduled_running)
    return jsonify({"running": bool(running_ids), "job_ids": running_ids})


@app.route("/schedule", methods=["POST"])
def create_schedule():
    """Create a new schedule entry."""
    try:
        data = request.get_json(force=True)

        stader_raw = data.get("stader") or ""
        if isinstance(stader_raw, list):
            stader = [s.strip() for s in stader_raw if str(s).strip()]
        else:
            stader = [s.strip() for s in str(stader_raw).replace("\n", ",").split(",") if s.strip()]

        kalla_val = str(data.get("kalla") or "").strip()
        max_antal = int(data.get("max_antal") or 5000)
        bara_med_telefon = bool(data.get("bara_med_telefon", False))
        housing_type = str(data.get("housing_type") or "alla").strip()
        tid = str(data.get("tid") or "02:00").strip()

        if not stader:
            return jsonify({"success": False, "error": "Du måste ange minst en stad."}), 400
        if kalla_val not in KALLOR:
            return jsonify({"success": False, "error": f"Ogiltig källa: {kalla_val}"}), 400
        if housing_type not in ("alla", "villa", "lagenhet"):
            housing_type = "alla"
        # Validate time format HH:MM
        try:
            h, m = tid.split(":")
            assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except Exception:
            return jsonify({"success": False, "error": "Ogiltig tid — använd HH:MM."}), 400

        schedule = {
            "id": str(uuid.uuid4()),
            "stader": stader,
            "kalla": kalla_val,
            "kalla_namn": KALLOR[kalla_val].get("namn", kalla_val),
            "max_antal": max_antal,
            "bara_med_telefon": bara_med_telefon,
            "housing_type": housing_type,
            "tid": tid,
            "skapad": datetime.utcnow().isoformat() + "Z",
        }

        with _schedules_lock:
            schedules = _load_schedules()
            schedules.append(schedule)
            _save_schedules(schedules)

        _register_schedule(schedule)
        return jsonify({"success": True, "schedule": schedule})

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/schedule/<schedule_id>", methods=["DELETE"])
def delete_schedule(schedule_id: str):
    """Delete a schedule by ID."""
    safe_id = "".join(c for c in schedule_id if c.isalnum() or c == "-")
    with _schedules_lock:
        schedules = _load_schedules()
        new_schedules = [s for s in schedules if s["id"] != safe_id]
        if len(new_schedules) == len(schedules):
            return jsonify({"success": False, "error": "Schema hittades inte."}), 404
        _save_schedules(new_schedules)

    try:
        _scheduler.remove_job(safe_id)
    except Exception:
        pass  # Job may not exist in scheduler (e.g. after restart)

    return jsonify({"success": True})


@app.route("/download", methods=["POST"])
def download():
    """Convert POSTed JSON results to a CSV file for download."""
    try:
        data = request.get_json(force=True)
        results = data.get("results", [])

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["name", "phone", "age", "address", "city", "housing_type", "source"],
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
