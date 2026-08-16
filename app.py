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

def _classify_housing(adress: str) -> str:
    """Classify housing type from address string.
    Returns 'Lägenhet', 'Villa', or 'Okänd' when address is missing/unclear.
    """
    a = adress.lower().strip()
    if not a or a == "saknas":
        return "Okänd"
    if "lgh" in a or "läg" in a or " apt " in a:
        return "Lägenhet"
    return "Villa"


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


def _filter_person(p: dict, bara_med_telefon: bool, housing_type: str,
                   min_alder: int | None = None, max_alder: int | None = None) -> bool:
    """Return True if the person passes all active filters."""
    if bara_med_telefon and (not p.get("phone") or p.get("phone") == "Saknas"):
        return False
    if housing_type == "villa":
        if p.get("housing_type") != "Villa":
            return False
    elif housing_type == "lagenhet":
        if p.get("housing_type") != "Lägenhet":
            return False
    if min_alder is not None or max_alder is not None:
        age_str = p.get("age", "")
        if not age_str:
            return False  # Ålder okänd med aktivt åldersfilter → avvisas
        try:
            age = int(age_str)
        except (ValueError, TypeError):
            return False
        if min_alder is not None and age < min_alder:
            return False
        if max_alder is not None and age > max_alder:
            return False
    return True


def _extrahera_ort_fran_adress(adress: str) -> str:
    """Försök extrahera stadsnamnet ur en adresssträng som innehåller postnummer.
    Exempel: 'Storgatan 5, 168 56 Bromma' → 'Bromma'
    """
    m = re.search(r'\b\d{3}\s?\d{2}\s+([A-ZÅÄÖ][A-ZÅÄÖa-zåäö\s\-]+?)(?:\s*,|\s*$)', adress)
    return m.group(1).strip() if m else ""


def _person_to_result(p: dict, override_city: str = "") -> dict:
    adress = p.get("adress", "")
    # City priority: explicit override → scraped stad → extracted from address
    city = override_city or p.get("stad", "") or _extrahera_ort_fran_adress(adress)
    # Don't use a 5-digit postal code as the city label
    if re.fullmatch(r"\d{3}\s?\d{2}", city.strip()):
        city = _extrahera_ort_fran_adress(adress)
    return {
        "name":         p.get("namn", ""),
        "phone":        p.get("telefon", ""),
        "address":      adress,
        "age":          p.get("alder", ""),
        "city":         city,
        "housing_type": _classify_housing(p.get("adress", "")),
        "source":       p.get("kalla", ""),
    }


# ── Background workers ────────────────────────────────────────────────────────

def _tel_dedup_key(t: str) -> tuple | None:
    """Normalize phone to a comparable tuple key for dedup. Returns None if no phone."""
    if not t or t == "Saknas":
        return None
    digits = re.sub(r"\D", "", t)
    if digits.startswith("46") and len(digits) > 10:
        digits = digits[2:]
    elif digits.startswith("0046"):
        digits = digits[4:]
    elif digits.startswith("0") and len(digits) > 5:
        digits = digits[1:]
    return ("ph", digits) if digits else None


def _run_scrape_job(job_id: str, stader: list[str], kalla_val: str,
                    target_count: int, bara_med_telefon: bool, housing_type: str,
                    distribution_mode: str = "target",
                    min_alder: int | None = None, max_alder: int | None = None):
    """v0.3 scrape engine: chases qualified_count, not raw scanned_count.

    distribution_mode:
        "target"   — treat all areas as one pool, stop at qualified target
        "balanced" — soft quotas per area with deficit redistribution (multi-pass)
        "exhaust"  — drain every area up to per-area budget
    """
    # ── Hard scan budget (safety ceiling) ────────────────────────────────────
    EXHAUST_BUDGET_PER_AREA = 50_000   # each area gets its own cap in exhaust mode
    if distribution_mode == "exhaust":
        hard_scan_budget = EXHAUST_BUDGET_PER_AREA * max(len(stader), 1)
    else:
        hard_scan_budget = min(max(target_count * 20, 2_000), 200_000)

    # ── Mutable global state ──────────────────────────────────────────────────
    g = {"scanned": 0, "qualified": 0, "duplicate": 0, "rejected": 0}
    global_dedup: set[tuple] = set()
    all_results: list[dict] = []
    job_status = ["completed"]
    block_reason = [None]

    # ── Per-area state (next_page for balanced multi-pass) ────────────────────
    area_state: dict[str, dict] = {
        stad: {"status": "pending", "quota": 0, "scanned": 0, "qualified": 0, "next_page": 1}
        for stad in stader
    }

    stader_list = list(stader)
    stad_idx_map = {s: i for i, s in enumerate(stader_list)}
    kalla_cfg = KALLOR.get(kalla_val, {})
    wait_ms = kalla_cfg.get("wait_ms", 10000)

    # ── Helper: emit live progress ────────────────────────────────────────────
    def emit_progress():
        _append_event(job_id, "progress", {
            "status": "running",
            "distribution_mode": distribution_mode,
            "target_count": target_count if distribution_mode != "exhaust" else None,
            "scanned_count":   g["scanned"],
            "qualified_count": g["qualified"],
            "duplicate_count": g["duplicate"],
            "rejected_count":  g["rejected"],
            "areas": {
                s: {"status": st["status"], "quota": st["quota"],
                    "scanned": st["scanned"], "qualified": st["qualified"]}
                for s, st in area_state.items()
            },
        })

    def _start_area(stad: str, ast: dict, idx: int):
        _append_event(job_id, "city_start", {
            "stad": stad, "stad_nr": idx + 1,
            "antal_stader": len(stader_list),
            "totalt": g["qualified"],
            "wait_sek": round(wait_ms / 1000),
        })

    def _end_area(stad: str, ast: dict, idx: int):
        _append_event(job_id, "city_done", {
            "stad": stad, "stad_nr": idx + 1,
            "antal_stader": len(stader_list),
            "hittade": ast["qualified"],
            "totalt": g["qualified"],
        })

    # ── Core area runner ──────────────────────────────────────────────────────
    def _process_area(stad: str, ast: dict, area_budget: int) -> str:
        """
        Call hamta_personer for one area (may start mid-way via ast["next_page"]).
        Returns stop_reason: "quota" | "target" | "budget" | "exhausted" | "error"
        """
        if area_budget <= 0:
            return "budget"

        stad_postnr = _is_postnummer(stad)
        stop_reason_area = ["exhausted"]
        last_page = [ast["next_page"] - 1]

        def make_on_page(stad_=stad, ast_=ast, postnr_=stad_postnr):
            def on_page(sida_persons: list[dict], page_num: int) -> bool:
                last_page[0] = page_num

                for raw_p in sida_persons:
                    # ── Budget gate: check BEFORE counting ───────────────────
                    if g["scanned"] >= hard_scan_budget:
                        stop_reason_area[0] = "budget"
                        return False

                    g["scanned"] += 1
                    ast_["scanned"] += 1

                    # Cross-area dedup
                    tel  = raw_p.get("telefon", "Saknas")
                    tk   = _tel_dedup_key(tel)
                    naam = raw_p.get("namn", "")
                    addr = raw_p.get("adress", "")
                    ak   = ("na", naam.lower(), addr.lower())

                    if (tk and tk in global_dedup) or ak in global_dedup:
                        g["duplicate"] += 1
                        continue

                    r = _person_to_result(raw_p, override_city="" if postnr_ else stad_)

                    if postnr_ and not _adress_matchar_postnummer(r["address"], stad_):
                        g["rejected"] += 1
                        continue

                    if not _filter_person(r, bara_med_telefon, housing_type,
                                          min_alder, max_alder):
                        g["rejected"] += 1
                        continue

                    # ── Target gate: check BEFORE adding to prevent overshoot ─
                    if distribution_mode != "exhaust" and g["qualified"] >= target_count:
                        stop_reason_area[0] = "target"
                        return False

                    # ✓ Qualified
                    if tk:
                        global_dedup.add(tk)
                    global_dedup.add(ak)
                    g["qualified"] += 1
                    ast_["qualified"] += 1
                    all_results.append(r)

                    # ── Stop immediately after hitting exact target ────────────
                    if distribution_mode != "exhaust" and g["qualified"] >= target_count:
                        stop_reason_area[0] = "target"
                        return False

                    # ── Balanced: stop when area quota filled ─────────────────
                    if distribution_mode == "balanced" and ast_["qualified"] >= ast_["quota"]:
                        stop_reason_area[0] = "quota"
                        return False

                emit_progress()

                # Page-level fallback checks
                if g["scanned"] >= hard_scan_budget:
                    stop_reason_area[0] = "budget"
                    return False
                if distribution_mode == "target" and g["qualified"] >= target_count:
                    stop_reason_area[0] = "target"
                    return False
                return True
            return on_page

        def make_progress(stad_=stad):
            def progress(event_type: str, **kwargs):
                if event_type == "blocked":
                    block_reason[0] = kwargs.get("anledning", "Scraping blockerades.")
                _append_event(job_id, event_type, {"stad": stad_, **kwargs})
            return progress

        try:
            hamta_personer(
                stad, kalla_val,
                scan_budget=area_budget,
                max_profil_anrop=0,
                progress_callback=make_progress(),
                on_page=make_on_page(),
                start_page=ast["next_page"],
            )
        except Exception as exc:
            _append_event(job_id, "city_error", {"stad": stad, "fel": str(exc)})
            print(f"[{stad}] error: {exc}")
            return "error"

        ast["next_page"] = last_page[0] + 1
        return stop_reason_area[0]

    print(f"\n[JOB] mode={distribution_mode} target={target_count} "
          f"areas={len(stader_list)} budget={hard_scan_budget}")

    # ════════════════════════════════════════════════════════════════════════
    # TARGET MODE
    # ════════════════════════════════════════════════════════════════════════
    if distribution_mode == "target":
        for stad in stader_list:
            ast = area_state[stad]
            idx = stad_idx_map[stad]

            if g["qualified"] >= target_count:
                ast["status"] = "target_reached"
                continue
            remaining = hard_scan_budget - g["scanned"]
            if remaining <= 0:
                ast["status"] = "budget_reached"
                job_status[0] = "budget_reached"
                break

            ast["status"] = "running"
            ast["quota"] = target_count - g["qualified"]
            _start_area(stad, ast, idx)
            print(f"[{stad}] target remaining={target_count - g['qualified']}")

            reason = _process_area(stad, ast, area_budget=remaining)

            if reason == "error":
                ast["status"] = "error"
            elif reason in ("target",):
                ast["status"] = "target_reached"
                for s in stader_list[idx + 1:]:
                    if area_state[s]["status"] == "pending":
                        area_state[s]["status"] = "target_reached"
            elif reason == "budget" or g["scanned"] >= hard_scan_budget:
                ast["status"] = "budget_reached"
                job_status[0] = "budget_reached"
            else:
                ast["status"] = "exhausted"
                print(f"[{stad}] exhausted — qualified={ast['qualified']}")

            _end_area(stad, ast, idx)
            if ast["status"] in ("target_reached", "budget_reached"):
                break

    # ════════════════════════════════════════════════════════════════════════
    # BALANCED MODE — multi-pass with quota_reached revisitation
    # ════════════════════════════════════════════════════════════════════════
    elif distribution_mode == "balanced":
        n = len(stader_list)
        base = target_count // n
        rem  = target_count % n
        for i, s in enumerate(stader_list):
            area_state[s]["quota"] = base + (1 if i < rem else 0)

        MAX_PASSES = 20
        for pass_num in range(1, MAX_PASSES + 1):
            if g["qualified"] >= target_count or g["scanned"] >= hard_scan_budget:
                break

            if pass_num == 1:
                pass_areas = stader_list[:]
            else:
                pass_areas = [s for s in stader_list
                              if area_state[s]["status"] == "quota_reached"]
                if not pass_areas:
                    break   # no areas to revisit — all truly exhausted

                # Redistribute remaining deficit to quota_reached areas
                deficit = target_count - g["qualified"]
                n_pa = len(pass_areas)
                b2 = deficit // n_pa
                r2 = deficit % n_pa
                print(f"[BALANCER] pass={pass_num} deficit={deficit} areas={n_pa}")
                for i, s in enumerate(pass_areas):
                    area_state[s]["quota"] += b2 + (1 if i < r2 else 0)
                emit_progress()

            had_quota_reached = False

            for j, stad in enumerate(pass_areas):
                ast = area_state[stad]
                idx = stad_idx_map[stad]

                if g["qualified"] >= target_count:
                    ast["status"] = "target_reached"
                    continue
                remaining = hard_scan_budget - g["scanned"]
                if remaining <= 0:
                    ast["status"] = "budget_reached"
                    job_status[0] = "budget_reached"
                    break

                ast["status"] = "running"
                _start_area(stad, ast, idx)
                print(f"[{stad}] pass={pass_num} quota={ast['quota']} "
                      f"next_page={ast['next_page']}")

                reason = _process_area(stad, ast, area_budget=remaining)

                if reason == "error":
                    ast["status"] = "error"
                elif reason == "target":
                    ast["status"] = "target_reached"
                    _end_area(stad, ast, idx)
                    break
                elif reason == "quota":
                    # More pages likely exist — can revisit in next pass
                    ast["status"] = "quota_reached"
                    had_quota_reached = True
                    print(f"[{stad}] quota_reached — {ast['qualified']}/{ast['quota']} "
                          f"next_page={ast['next_page']}")
                elif reason == "budget" or g["scanned"] >= hard_scan_budget:
                    ast["status"] = "budget_reached"
                    job_status[0] = "budget_reached"
                else:
                    # Truly exhausted — redistribute shortfall forward in this pass
                    ast["status"] = "exhausted"
                    shortfall = ast["quota"] - ast["qualified"]
                    print(f"[{stad}] exhausted — {ast['qualified']}/{ast['quota']}")
                    fwd = [s for s in pass_areas[j + 1:]
                           if area_state[s]["status"] not in
                           ("exhausted", "error", "target_reached",
                            "budget_reached", "quota_reached")]
                    if shortfall > 0 and fwd:
                        ext = shortfall // len(fwd)
                        lft = shortfall % len(fwd)
                        for k, s in enumerate(fwd):
                            area_state[s]["quota"] += ext + (1 if k < lft else 0)
                        print(f"[BALANCER] shortfall={shortfall} → {len(fwd)} areas")

                _end_area(stad, ast, idx)
                if job_status[0] == "budget_reached":
                    break

            if not had_quota_reached:
                break   # nothing to revisit in subsequent passes

    # ════════════════════════════════════════════════════════════════════════
    # EXHAUST MODE — per-area budget so early large areas don't starve others
    # ════════════════════════════════════════════════════════════════════════
    else:
        for stad in stader_list:
            ast = area_state[stad]
            idx = stad_idx_map[stad]

            ast["status"] = "running"
            ast["quota"] = 999_999_999
            _start_area(stad, ast, idx)
            print(f"[{stad}] exhaust per-area-budget={EXHAUST_BUDGET_PER_AREA}")

            reason = _process_area(stad, ast, area_budget=EXHAUST_BUDGET_PER_AREA)

            ast["status"] = "error" if reason == "error" else "exhausted"
            _end_area(stad, ast, idx)

    # ── Final status ──────────────────────────────────────────────────────────
    if distribution_mode == "exhaust":
        job_status[0] = "completed"
    elif g["qualified"] >= target_count:
        job_status[0] = "completed"
    elif job_status[0] not in ("budget_reached",):
        job_status[0] = "partial"

    stop_reason = None
    if job_status[0] == "budget_reached":
        stop_reason = "Scan-budgeten nåddes."
    elif job_status[0] == "partial":
        stop_reason = "Alla valda områden är uttömda."

    print(f"[JOB] {job_status[0]} scanned={g['scanned']} qualified={g['qualified']} "
          f"dup={g['duplicate']} rej={g['rejected']}")

    done_payload: dict = {
        "count":           g["qualified"],
        "results":         all_results,
        "job_status":      job_status[0],
        "scanned_count":   g["scanned"],
        "qualified_count": g["qualified"],
        "duplicate_count": g["duplicate"],
        "rejected_count":  g["rejected"],
        "areas": {
            s: {"status": st["status"], "quota": st["quota"],
                "scanned": st["scanned"], "qualified": st["qualified"]}
            for s, st in area_state.items()
        },
    }
    if block_reason[0]:
        done_payload["block_reason"] = block_reason[0]
    if stop_reason:
        done_payload["stop_reason"] = stop_reason

    _append_terminal_event(
        job_id, "done", done_payload,
        terminal_status="done",
        extra={"results": all_results, "block_reason": block_reason[0]},
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
        distribution_mode = str(data.get("distribution_mode") or "target").strip()
        min_alder_raw = data.get("min_alder")
        max_alder_raw = data.get("max_alder")
        min_alder = int(min_alder_raw) if min_alder_raw not in (None, "", "null") else None
        max_alder = int(max_alder_raw) if max_alder_raw not in (None, "", "null") else None

        if max_antal <= 0:
            return jsonify({"success": False,
                            "error": "max_antal måste vara större än 0."}), 400
        if housing_type not in ("alla", "villa", "lagenhet"):
            housing_type = "alla"
        if distribution_mode not in ("balanced", "target", "exhaust"):
            distribution_mode = "target"

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
                "distribution_mode": distribution_mode,
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
                kwargs={"distribution_mode": distribution_mode,
                        "min_alder": min_alder, "max_alder": max_alder},
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
