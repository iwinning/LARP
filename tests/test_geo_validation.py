"""
Geovalideringstester för LARP v0.3.1 (DEL 14).
Täcker _extract_postal_code, _normalize_area och _validate_geography.
Kör: python -m pytest tests/test_geo_validation.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import _extract_postal_code, _normalize_area, _validate_geography


class TestGeoValidation:
    """Sju geovalideringstester (GEO-1 – GEO-7)."""

    # GEO-1 — Korrekt adress godkänns
    def test_geo1_valid_address_accepted(self):
        """Rätt postnummer + rätt stad → accept."""
        area   = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        result = {"address": "Zornvägen 38, 168 50 Bromma", "city": "Bromma",
                  "postal_code": "16850"}
        valid, reason = _validate_geography(result, area)
        assert valid is True
        assert reason == ""

    # GEO-2 — Fel postnummer OCH fel stad → wrong_location
    def test_geo2_wrong_location_both_mismatch(self):
        """Postnummer pekar på Västerås istället för Bromma → wrong_location."""
        area   = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        result = {"address": "Infanterigatan 168, 723 50 Västerås",
                  "city": "Västerås", "postal_code": "72350"}
        valid, reason = _validate_geography(result, area)
        assert valid is False
        assert reason == "wrong_location"

    # GEO-3 — Rätt stad men fel postnummer → wrong_postal_code
    def test_geo3_wrong_postal_code_correct_city(self):
        """Postnummer matchar inte men staden är rätt → wrong_postal_code."""
        area   = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        result = {"address": "Ståltrådsvägen 50, 168 68 Bromma",
                  "city": "Bromma", "postal_code": "16868"}
        valid, reason = _validate_geography(result, area)
        assert valid is False
        assert reason == "wrong_postal_code"

    # GEO-4 — Normalisering av postnummer med/utan mellanslag
    def test_geo4_postal_normalization_strips_spaces(self):
        """'168 50 Bromma' och '16850 Bromma' ska ge samma postnummer."""
        a1 = _normalize_area("168 50 Bromma")
        a2 = _normalize_area("16850 Bromma")
        assert a1["postal_code"] == "16850"
        assert a2["postal_code"] == "16850"
        assert a1["_search"] == a2["_search"]

    # GEO-5 — Stadsnamn case-insensitivt
    def test_geo5_city_comparison_case_insensitive(self):
        """Bromma, BROMMA och bromma ska alla godkännas."""
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        for variant in ("Bromma", "BROMMA", "bromma"):
            result = {"address": "Storgatan 5, 168 50 Bromma",
                      "city": variant, "postal_code": "16850"}
            valid, reason = _validate_geography(result, area)
            assert valid is True, f"Förväntade godkänt för city={variant!r}"

    # GEO-6 — Fel-område-person med telefon räknas INTE som godkänd (enhetstest)
    def test_geo6_wrong_location_not_qualified(self):
        """En person med rätt telefon men fel postnummer ska ge wrong_location-resultat."""
        from unittest.mock import patch
        import app as app_mod

        # Bygg en person med Västerås-adress men begär Bromma
        wrong_loc_person = {
            "namn": "Fel Person", "telefon": "0700000001",
            "adress": "Infanterigatan 50, 723 50 Västerås",
            "alder": "40", "stad": "Västerås", "kalla": "Merinfo",
            "_profil_url": None,
        }
        correct_person = {
            "namn": "Rätt Person", "telefon": "0700000002",
            "adress": "Zornvägen 5, 168 50 Bromma",
            "alder": "40", "stad": "Bromma", "kalla": "Merinfo",
            "_profil_url": None,
        }
        pages = {"168 50 Bromma": [[wrong_loc_person, correct_person]]}
        area  = app_mod._normalize_area({"postal_code": "16850", "city": "Bromma"})

        captured: dict = {}

        def mock_terminal(job_id, event_type, payload, **kw):
            captured["payload"] = payload

        def mock_hamta(stad, kalla_val, scan_budget=50_000, max_profil_anrop=0,
                       progress_callback=None, on_page=None, start_page=1):
            if on_page:
                on_page(pages.get(stad, [[]]) [0], 1)

        with patch.object(app_mod, "hamta_personer", mock_hamta), \
             patch.object(app_mod, "_append_event", lambda *a, **k: None), \
             patch.object(app_mod, "_append_terminal_event", mock_terminal):
            app_mod._run_scrape_job(
                "test-geo6", [area], "1", 5, False, "alla")

        payload = captured["payload"]
        assert payload["qualified_count"] == 1,  \
            f"Förväntade 1 godkänd men fick {payload['qualified_count']}"
        assert payload["wrong_location_count"] == 1, \
            f"Förväntade 1 fel-område men fick {payload['wrong_location_count']}"

    # GEO-7 — _extract_postal_code fungerar på Merinfo-adressformat
    def test_geo7_extract_postal_from_merinfo_format(self):
        """Postnummer extraheras korrekt från Merinfo-adresssträngar."""
        assert _extract_postal_code("Zornvägen 38, 168 50 Bromma")           == "16850"
        assert _extract_postal_code("Infanterigatan 168, 723 50 Västerås")   == "72350"
        assert _extract_postal_code("Ståltrådsvägen 50, 168 68 Bromma")      == "16868"
        assert _extract_postal_code("Ingen adress alls")                      == ""
        assert _extract_postal_code("Storgatan 5, 113 46 Stockholm")          == "11346"
