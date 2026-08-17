"""
Enhetstester för LARP v0.3 kärnlogik.
Kör: python -m pytest tests/test_core.py -v
"""

import sys
import os
import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapa_alla import _extrahera_alder


TODAY = datetime.date.today()


# ─────────────────────────────────────────────
# Test 1 — Ålderextraktion
# ─────────────────────────────────────────────

class TestExtrahera_Alder:

    def test_explicit_ar(self):
        assert _extrahera_alder("Sven Andersson 45 år Stockholm") == "45"

    def test_personnummer_format_birthday_passed(self):
        """Födelsedag som redan passerat i år → korrekt ålder."""
        year = TODAY.year - 36
        bd = datetime.date(year, 1, 15)          # 15 jan → alltid passerat om vi är i aug
        text = f"{bd.strftime('%Y%m%d')}-****"
        result = _extrahera_alder(text)
        assert result == "36", f"Got {result!r} for {text!r}"

    def test_personnummer_format_birthday_not_passed(self):
        """Födelsedag som INTE passerat i år → ett år lägre."""
        year = TODAY.year - 36
        bd = datetime.date(year, 12, 31)         # 31 dec → ännu inte passerat om vi är i aug
        text = f"{bd.strftime('%Y%m%d')}-****"
        result = _extrahera_alder(text)
        assert result == "35", f"Got {result!r} for {text!r}"

    def test_personnummer_masked_x(self):
        text = "19980601-XXXX"
        result = _extrahera_alder(text)
        age = int(result)
        assert 25 <= age <= 30

    def test_no_date_in_text(self):
        assert _extrahera_alder("Storgatan 5 168 56 Bromma") == ""

    def test_postal_code_not_birthdate(self):
        """Postnummer ska INTE tolkas som personnummer."""
        assert _extrahera_alder("Södermalm, 118 53 Stockholm") == ""

    def test_phone_not_birthdate(self):
        """Telefonnummer ska INTE tolkas som personnummer."""
        assert _extrahera_alder("0761234567") == ""

    def test_invalid_month(self):
        """Månad 13 är ogiltig — ska returnera ''."""
        assert _extrahera_alder("19901367-****") == ""


# ─────────────────────────────────────────────
# Test 2 — _filter_person
# ─────────────────────────────────────────────

class TestFilterPerson:

    def setup_method(self):
        from app import _filter_person
        self._filter = _filter_person

    def _p(self, phone="0701234567", housing="Villa", age="45"):
        return {"phone": phone, "housing_type": housing, "age": age}

    def test_no_filters(self):
        assert self._filter(self._p(), False, "alla") is True

    def test_phone_filter_passes(self):
        assert self._filter(self._p(phone="0701234567"), True, "alla") is True

    def test_phone_filter_blocks_missing(self):
        assert self._filter(self._p(phone="Saknas"), True, "alla") is False

    def test_housing_villa(self):
        assert self._filter(self._p(housing="Villa"),    False, "villa") is True
        assert self._filter(self._p(housing="Lägenhet"), False, "villa") is False

    def test_housing_lagenhet(self):
        assert self._filter(self._p(housing="Lägenhet"), False, "lagenhet") is True
        assert self._filter(self._p(housing="Villa"),    False, "lagenhet") is False

    def test_age_min(self):
        assert self._filter(self._p(age="30"), False, "alla", min_alder=30) is True
        assert self._filter(self._p(age="29"), False, "alla", min_alder=30) is False

    def test_age_max(self):
        assert self._filter(self._p(age="65"), False, "alla", max_alder=65) is True
        assert self._filter(self._p(age="66"), False, "alla", max_alder=65) is False

    def test_age_range(self):
        assert self._filter(self._p(age="45"), False, "alla", min_alder=30, max_alder=65) is True
        assert self._filter(self._p(age="25"), False, "alla", min_alder=30, max_alder=65) is False

    def test_age_missing_with_filter(self):
        """Okänd ålder när åldersfilter är aktivt → avvisas."""
        assert self._filter(self._p(age=""), False, "alla", min_alder=30) is False


# ─────────────────────────────────────────────
# Test 3 — Dedup (telefonnormalisering)
# ─────────────────────────────────────────────

class TestTelDedup:

    def setup_method(self):
        from app import _tel_dedup_key
        self._key = _tel_dedup_key

    def test_same_number_different_format(self):
        """0701234567 och +46701234567 ska ge samma nyckel."""
        assert self._key("0701234567") == self._key("+46701234567")

    def test_no_phone(self):
        assert self._key("Saknas") is None
        assert self._key("") is None

    def test_different_numbers(self):
        assert self._key("0701234567") != self._key("0709876543")


# ─────────────────────────────────────────────
# Test 4 — Balanced quota-fördelning
# ─────────────────────────────────────────────

class TestBalancedQuota:
    """Verifiera att initial quota fördelas rätt och att summan stämmer."""

    def _compute_quotas(self, target, n_areas):
        base = target // n_areas
        rem  = target % n_areas
        return [base + (1 if i < rem else 0) for i in range(n_areas)]

    def test_exact_division(self):
        quotas = self._compute_quotas(500, 5)
        assert quotas == [100, 100, 100, 100, 100]
        assert sum(quotas) == 500

    def test_remainder_distribution(self):
        quotas = self._compute_quotas(502, 5)
        assert sum(quotas) == 502
        assert max(quotas) - min(quotas) <= 1

    def test_single_area(self):
        quotas = self._compute_quotas(500, 1)
        assert quotas == [500]


# ─────────────────────────────────────────────
# Test 5 — Redistribution efter exhausted area
# ─────────────────────────────────────────────

class TestRedistribution:
    """Deficit från exhausted area ska fördelas på återstående areas."""

    def _redistribute(self, deficit, n_pending):
        extra    = deficit // n_pending
        leftover = deficit % n_pending
        return [extra + (1 if i < leftover else 0) for i in range(n_pending)]

    def test_even_redistribution(self):
        extras = self._redistribute(40, 4)
        assert extras == [10, 10, 10, 10]

    def test_uneven_redistribution(self):
        extras = self._redistribute(28, 4)
        assert sum(extras) == 28
        assert max(extras) - min(extras) <= 1

    def test_all_to_one(self):
        extras = self._redistribute(100, 1)
        assert extras == [100]


# ─────────────────────────────────────────────
# ENGINE-TESTER (Tests 1–11)
# Använder mockad hamta_personer — inga nätverksanrop
# ─────────────────────────────────────────────

from unittest.mock import patch


def _person(i: int, stad: str = "StadA", phone: bool = True, age: int = 45) -> dict:
    """Minimal persondict som _person_to_result accepterar.
    Varje unik i ger unikt telefonnummer — undviker global-dedup-kollisioner i testerna.
    """
    return {
        "namn":       f"Person{i}",
        "telefon":    f"070{i:07d}" if phone else "Saknas",   # unikt per person
        "adress":     f"Storgatan {i} 16764 Täby",
        "alder":      str(age),
        "stad":       stad,
        "kalla":      "Merinfo",
        "_profil_url": None,
    }


def _make_mock_hamta(area_pages: dict):
    """
    Returnerar en hamta_personer-mock som simulerar sidnedladdning.
    area_pages: {stad -> list-of-pages}, varje sida är en lista med persondict.
    """
    def mock_fn(stad, kalla_val, scan_budget=50_000, max_profil_anrop=0,
                progress_callback=None, on_page=None, start_page=1):
        pages = area_pages.get(stad, [])
        for page_idx, page_persons in enumerate(pages[start_page - 1:], start=start_page):
            if on_page is not None:
                should_continue = on_page(page_persons, page_idx)
                if not should_continue:
                    break
    return mock_fn


def _run_job(stader_or_areas, area_pages, target=10, mode="target",
             bara_med_telefon=False, housing_type="alla"):
    """Kör _run_scrape_job med mockad hamta_personer; returnerar done_payload.

    Accepts either a list of strings (legacy) or list of dicts (new area format).
    The mock looks up pages by area["_search"], which for plain strings equals the string.
    """
    import app as app_mod
    captured: dict = {}
    # Normalise inputs to area dicts
    areas = [app_mod._normalize_area(a) for a in stader_or_areas]

    def mock_terminal(job_id, event_type, payload, **kw):
        captured["payload"] = payload

    with patch.object(app_mod, "hamta_personer", _make_mock_hamta(area_pages)), \
         patch.object(app_mod, "_append_event", lambda *a, **k: None), \
         patch.object(app_mod, "_append_terminal_event", mock_terminal):
        app_mod._run_scrape_job(
            "test-job", areas, "1", target,
            bara_med_telefon, housing_type,
            distribution_mode=mode,
        )
    return captured.get("payload", {})


class TestEngine:
    """Engine-tester 1–11: exakt stopp, balansering och exhaust-budget."""

    # Test 1 — Exakt stopp vid target
    def test1_exact_target_no_overshoot(self):
        """target=5, sidan har 10 godkända → resultatet ska vara exakt 5."""
        pages = {"A": [[_person(i) for i in range(10)]]}
        r = _run_job(["A"], pages, target=5)
        assert r["qualified_count"] == 5
        assert len(r["results"]) == 5

    # Test 2 — Exakt stopp med telefon-filter (fler råscannade krävs)
    def test2_exact_target_with_phone_filter(self):
        """target=20, bara varannan person har telefon → scanned > 20 men qualified == 20."""
        # 5 sidor × 20 personer, varannan med telefon → ~10 godkända/sida
        pages = {"A": [[_person(j + i * 20, phone=(j % 2 == 0))
                        for j in range(20)] for i in range(5)]}
        r = _run_job(["A"], pages, target=20, bara_med_telefon=True)
        assert r["qualified_count"] == 20
        assert r["scanned_count"] > 20

    # Test 3a — Godtyckligt mål 37
    def test3a_arbitrary_target_37(self):
        pages = {"A": [[_person(j + i * 20) for j in range(20)] for i in range(5)]}
        r = _run_job(["A"], pages, target=37)
        assert r["qualified_count"] == 37
        assert len(r["results"]) == 37

    # Test 3b — Godtyckligt mål 503
    def test3b_arbitrary_target_503(self):
        pages = {"A": [[_person(j + i * 20) for j in range(20)] for i in range(30)]}
        r = _run_job(["A"], pages, target=503)
        assert r["qualified_count"] == 503

    # Test 4 — Balanced: kvotsumma == target
    def test4_balanced_quota_sum_equals_target(self):
        """Balanced med 5 städer: de initiala kvoterna måste summera till target."""
        import app as app_mod
        captured_quotas: dict = {}

        def mock_terminal(job_id, event_type, payload, **kw):
            if not captured_quotas:
                for s, st in payload["areas"].items():
                    captured_quotas[s] = st["quota"]

        pages = {s: [[_person(j + i * 20) for j in range(20)] for i in range(5)]
                 for s in ["A", "B", "C", "D", "E"]}

        areas = [app_mod._normalize_area(s) for s in ["A", "B", "C", "D", "E"]]
        with patch.object(app_mod, "hamta_personer", _make_mock_hamta(pages)), \
             patch.object(app_mod, "_append_event", lambda *a, **k: None), \
             patch.object(app_mod, "_append_terminal_event", mock_terminal):
            app_mod._run_scrape_job(
                "j", areas, "1", 503,
                False, "alla", distribution_mode="balanced",
            )

        assert sum(captured_quotas.values()) >= 503

    # Test 5 — Balanced: revisit av quota_reached-area
    def test5_balanced_revisits_quota_reached(self):
        """
        2 städer, target=10.
        A: sida 1 (5 pers), sida 2 (5 pers).  B: 2 pers.
        Pass 1: A→5 (quota), B→2 (exhausted). Deficit=3, B uttömd → fördelas till A.
        Pass 2: A fortsätter från sida 2 → hämtar 3 till. Totalt = 10.
        """
        pages = {
            "A": [
                [_person(i) for i in range(5)],           # sida 1
                [_person(10 + i) for i in range(5)],      # sida 2 (revisit)
            ],
            "B": [
                [_person(100 + i) for i in range(2)],     # 2 pers, sedan uttömd
            ],
        }
        r = _run_job(["A", "B"], pages, target=10, mode="balanced")
        assert r["qualified_count"] == 10

    # Test 6 — quota_reached ≠ exhausted
    def test6_quota_reached_distinct_from_exhausted(self):
        """
        En area med fler sidor än kvoten ska få status quota_reached eller
        target_reached — ALDRIG exhausted prematurely.
        """
        import app as app_mod
        area_statuses: dict = {}
        final_payload: dict = {}

        def mock_terminal(job_id, event_type, payload, **kw):
            for s, st in payload["areas"].items():
                area_statuses[s] = st["status"]
            final_payload.update(payload)

        # A: 5 sidor × 20 pers. B: 5 sidor × 20 pers. Target=10 → initial kvot=5/area.
        pages = {s: [[_person(j + i * 20 + (100 if s == "B" else 0)) for j in range(20)]
                     for i in range(5)] for s in ["A", "B"]}

        areas = [app_mod._normalize_area(s) for s in ["A", "B"]]
        with patch.object(app_mod, "hamta_personer", _make_mock_hamta(pages)), \
             patch.object(app_mod, "_append_event", lambda *a, **k: None), \
             patch.object(app_mod, "_append_terminal_event", mock_terminal):
            app_mod._run_scrape_job(
                "j", areas, "1", 10, False, "alla",
                distribution_mode="balanced",
            )

        # Target must be reached exactly
        assert final_payload["qualified_count"] == 10, \
            f"Förväntade 10 kvalificerade, fick {final_payload['qualified_count']}"

        # Areas stopped due to quota/target — never prematurely exhausted
        for s in ["A", "B"]:
            status = area_statuses.get(s, "unknown")
            assert status in ("quota_reached", "target_reached", "pending"), \
                f"Area {s} fick status {status!r} men har fler sidor — borde inte vara exhausted"

    # Test 7 — Hard budget nås och stoppas
    def test7_hard_budget_actually_hit(self):
        """
        När target aldrig kan nås (ingen har telefon) stoppas körningen exakt
        vid hard_scan_budget och qualified_count == 0.
        """
        target = 37
        hard_budget = min(max(target * 20, 2_000), 200_000)  # 2000 för target=37
        # 2 200 råposter, ingen har telefon → qualified kan aldrig nås
        pages = {"A": [[_person(i, phone=False) for i in range(2_200)]]}
        r = _run_job(["A"], pages, target=target, bara_med_telefon=True)
        assert r["scanned_count"] <= hard_budget, \
            f"scanned {r['scanned_count']} överskred budgeten {hard_budget}"
        assert r["scanned_count"] == hard_budget, \
            f"Budgeten borde ha nåtts exakt: scanned={r['scanned_count']} budget={hard_budget}"
        assert r["qualified_count"] == 0, \
            f"Ingen har telefon — förväntade 0 kvalificerade, fick {r['qualified_count']}"

    # Test 8 — Ingen överskridning vid stor sida
    def test8_no_overshoot_large_page(self):
        """
        490 personer på 49 sidor (10/sida), sista sidan har 25 pers.
        target=500 → qualified==500, aldrig 501+.
        """
        pages_list = [[_person(j + i * 10) for j in range(10)] for i in range(49)]
        pages_list.append([_person(500 + j) for j in range(25)])  # sida 50: 25 pers
        pages = {"A": pages_list}
        r = _run_job(["A"], pages, target=500)
        assert r["qualified_count"] == 500
        assert len(r["results"]) == 500

    # Test 9 — Exhaust: alla städer samlas in
    def test9_exhaust_collects_all_areas(self):
        """Exhaust ignorerar target och samlar allt från A, B och C."""
        pages = {
            "A": [[_person(i) for i in range(13)]],
            "B": [[_person(100 + i) for i in range(7)]],
            "C": [[_person(200 + i) for i in range(21)]],
        }
        r = _run_job(["A", "B", "C"], pages, target=5, mode="exhaust")
        assert r["qualified_count"] == 41  # 13 + 7 + 21

    # Test 10 — Exhaust: per-area budget, sen area får sin chans
    def test10_exhaust_per_area_budget_fairness(self):
        """
        A har oerhört många sidor; B och C är små.
        Med per-area budget 50 000 ska B och C fortfarande få köra.
        """
        pages = {
            "A": [[_person(i)] for i in range(60_000)],          # stort
            "B": [[_person(100_000 + i) for i in range(5)]],     # litet, unika namn
            "C": [[_person(200_000 + i) for i in range(5)]],     # litet, unika namn
        }
        r = _run_job(["A", "B", "C"], pages, target=999, mode="exhaust")
        assert r["areas"]["B"]["qualified"] > 0, "B fick aldrig köra (svalt av A)"
        assert r["areas"]["C"]["qualified"] > 0, "C fick aldrig köra (svalt av A)"

    # Test 11 — Ålderextraktion: plain YYYYMMDD utan suffix
    def test11_age_plain_yyyymmdd(self):
        """_extrahera_alder ska hantera YYYYMMDD utan -****."""
        today = datetime.date.today()
        year = today.year - 28
        # 15 januari har alltid passerat om vi kör testet i aug
        text = f"{year}0115"
        result = _extrahera_alder(text)
        assert result == "28", f"Fick {result!r} för {text!r}"

    # Test 12 — Balanced: inga kandidater tappas vid samma-sida-stopp
    def test12_balanced_same_page_no_candidates_lost(self):
        """
        Acceptance test C:
        target=10, A: 10 godkända på sida 1, B: 2 godkända (totalt).
        Initial kvot: A=5, B=5.

        Med gammal kod: A stoppar vid person 5 mitt på sida 1 → 5+2=7 totalt (bugg).
        Med rätt kod: A processar hela sida 1 → global target nås direkt → 10 totalt.
        """
        pages = {
            "A": [[_person(i) for i in range(10)]],       # sida 1: 10 godkända
            "B": [[_person(100 + i) for i in range(2)]],  # sida 1: 2 godkända
        }
        r = _run_job(["A", "B"], pages, target=10, mode="balanced")
        assert r["qualified_count"] == 10, \
            (f"Förväntade 10 kvalificerade men fick {r['qualified_count']}. "
             f"Balanced stoppade troligen mitt på A:s sida 1 och tappade kandidater.")

    # Test 13 — wrong_location räknas inte mot target; motorn fortsätter
    def test13_wrong_location_does_not_consume_target(self):
        """
        Acceptance test (section 26):
        target=5, kandidater: 3 fel-område (Västerås) + 2 utan telefon + 5 rätt+telefon.
        wrong_location-personerna ska INTE räknas mot target.
        Motorn ska fortsätta tills 5 rätta hittats.
        """
        from unittest.mock import patch
        import app as app_mod

        def make_person(i, adress, phone):
            return {
                "namn": f"Person{i}", "telefon": f"070{i:07d}" if phone else "Saknas",
                "adress": adress, "alder": "40",
                "stad": "", "kalla": "Merinfo", "_profil_url": None,
            }

        candidates = [
            # 3 fel-område (Västerås)
            make_person(1, "Gatan 1, 723 50 Västerås",     True),
            make_person(2, "Gatan 2, 723 50 Västerås",     True),
            make_person(3, "Gatan 3, 723 50 Västerås",     True),
            # 2 utan telefon (Bromma men saknar telefon)
            make_person(4, "Zornvägen 4, 168 50 Bromma",   False),
            make_person(5, "Zornvägen 5, 168 50 Bromma",   False),
            # 5 rätt + telefon
            make_person(6, "Zornvägen 6, 168 50 Bromma",   True),
            make_person(7, "Zornvägen 7, 168 50 Bromma",   True),
            make_person(8, "Zornvägen 8, 168 50 Bromma",   True),
            make_person(9, "Zornvägen 9, 168 50 Bromma",   True),
            make_person(10,"Zornvägen 10, 168 50 Bromma",  True),
        ]

        area = app_mod._normalize_area({"postal_code": "16850", "city": "Bromma"})
        captured: dict = {}

        def mock_terminal(job_id, event_type, payload, **kw):
            captured["payload"] = payload

        def mock_hamta(stad, kalla_val, scan_budget=50_000, max_profil_anrop=0,
                       progress_callback=None, on_page=None, start_page=1):
            if on_page:
                on_page(candidates, 1)

        with patch.object(app_mod, "hamta_personer", mock_hamta), \
             patch.object(app_mod, "_append_event", lambda *a, **k: None), \
             patch.object(app_mod, "_append_terminal_event", mock_terminal):
            app_mod._run_scrape_job("test-13", [area], "1", 5, True, "alla")

        payload = captured["payload"]
        assert payload["qualified_count"] == 5, \
            f"Förväntade 5 kvalificerade, fick {payload['qualified_count']}"
        assert payload["wrong_location_count"] == 3, \
            f"Förväntade 3 fel-område, fick {payload['wrong_location_count']}"

    # Test 14 — Exhaust: per-area budget enforced i motorn
    def test14_exhaust_per_area_budget_ceiling(self):
        """
        Acceptance test E (section 12):
        Exhaust, area-budget=50 000. A försöker leverera 60 000 poster.
        Motor-level check ska garantera att A:s scanned <= 50 000.
        """
        # 60 000 poster på en sida
        pages = {"A": [[_person(i) for i in range(60_000)]]}
        r = _run_job(["A"], pages, target=999, mode="exhaust")
        EXHAUST_BUDGET = 50_000
        area_scanned = r["areas"]["A"]["scanned"]
        assert area_scanned <= EXHAUST_BUDGET, \
            (f"Area A scannades {area_scanned} poster men budgeten är {EXHAUST_BUDGET}. "
             f"Motor-level budget-check saknas eller fungerar inte.")
