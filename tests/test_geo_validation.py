"""
Geovalideringstester för LARP v0.3.1 (DEL 14).
Täcker _extract_postal_code, _normalize_area, _validate_geography,
_person_to_result och postnummervalidering.
Kör: python -m pytest tests/test_geo_validation.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (
    _extract_postal_code, _normalize_area, _validate_geography,
    _person_to_result,
)


class TestGeoValidation:
    """Geovalideringstester GEO-1 – GEO-14."""

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

    # GEO-6 — Fel-område-person med telefon räknas INTE som godkänd
    def test_geo6_wrong_location_not_qualified(self):
        """En person med rätt telefon men fel postnummer ska ge wrong_location-resultat."""
        from unittest.mock import patch
        import app as app_mod

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

    # GEO-8 — Integrationstest: söksträngen som stad förorenar INTE city
    def test_geo8_search_string_as_stad_does_not_contaminate_city(self):
        """
        Acceptance test A: råperson med söksträng '168 50 Bromma' som stad-fält
        → _person_to_result extraherar 'Bromma' ur adressen, inte söksträng som city.
        → geo-validering mot 16850/Bromma accepterar personen.
        """
        # Detta är exakt det format Merinfo-scrapern producerar: stad = söksträng
        raw_person = {
            "namn": "Test Person",
            "telefon": "0701234567",
            "adress": "Zornvägen 38, 168 50 Bromma",
            "alder": "45",
            "stad": "168 50 Bromma",      # söksträng, INTE verifierad stad
            "search_location": "168 50 Bromma",
            "kalla": "Merinfo",
            "_profil_url": None,
        }
        result = _person_to_result(raw_person)

        # Stad ska extraheras ur adressen, inte från söksträng-fältet
        assert result["postal_code"] == "16850", \
            f"Fel postnummer: {result['postal_code']!r}"
        assert result["city"] == "Bromma", \
            f"City bör vara 'Bromma' (från adress), inte {result['city']!r}"

        # Geo-validering ska godkänna personen
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        valid, reason = _validate_geography(result, area)
        assert valid is True, \
            f"Korrekt person felaktigt avvisad: reason={reason!r}"

    # GEO-9 — Acceptance test B: falsk Merinfo-träff med Västerås-adress → reject
    def test_geo9_false_merinfo_match_vastera_rejected(self):
        """Acceptance test B: Västerås-adress mot Bromma-förfrågan → wrong_location."""
        raw_person = {
            "namn": "Västerås Person",
            "telefon": "0709999999",
            "adress": "Infanterigatan 168, 723 50 Västerås",
            "alder": "50",
            "stad": "168 50 Bromma",    # söksträng
            "kalla": "Merinfo",
            "_profil_url": None,
        }
        result = _person_to_result(raw_person)
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        valid, reason = _validate_geography(result, area)
        assert valid is False
        assert reason == "wrong_location"

    # GEO-10 — Verkliga Merinfo-mönster: korrekt adress godkänns
    def test_geo10_real_merinfo_correct_address(self):
        """'Exempelvägen 10, 168 50 Bromma' ska godkännas mot 16850/Bromma."""
        raw = {"adress": "Exempelvägen 10, 168 50 Bromma", "stad": "", "kalla": "Merinfo",
               "namn": "A", "telefon": "070", "alder": "", "_profil_url": None}
        r = _person_to_result(raw)
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        valid, reason = _validate_geography(r, area)
        assert valid is True, f"Borde vara giltig, fick reason={reason!r}"

    # GEO-11 — Fel postnummer i Bromma → wrong_postal_code
    def test_geo11_wrong_postal_same_city(self):
        """'Ståltrådsvägen 50, 168 68 Bromma' → wrong_postal_code (stad OK, postnr fel)."""
        raw = {"adress": "Ståltrådsvägen 50, 168 68 Bromma", "stad": "", "kalla": "Merinfo",
               "namn": "A", "telefon": "070", "alder": "", "_profil_url": None}
        r = _person_to_result(raw)
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        valid, reason = _validate_geography(r, area)
        assert valid is False
        assert reason == "wrong_postal_code"

    # GEO-12 — Fel postnummer OCH fel stad (Helsingborg) → wrong_location
    def test_geo12_text_query_false_match_helsingborg(self):
        """'Flohemsvägen 168, 254 50 Helsingborg' → wrong_location mot Bromma."""
        raw = {"adress": "Flohemsvägen 168, 254 50 Helsingborg", "stad": "", "kalla": "Merinfo",
               "namn": "A", "telefon": "070", "alder": "", "_profil_url": None}
        r = _person_to_result(raw)
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        valid, reason = _validate_geography(r, area)
        assert valid is False
        assert reason == "wrong_location"

    # GEO-13 — Täby-adress → wrong_location mot Bromma
    def test_geo13_taby_rejected_for_bromma(self):
        """'Täbyvägen 168, 187 50 Täby' → wrong_location mot 16850/Bromma."""
        raw = {"adress": "Täbyvägen 168, 187 50 Täby", "stad": "", "kalla": "Merinfo",
               "namn": "A", "telefon": "070", "alder": "", "_profil_url": None}
        r = _person_to_result(raw)
        area = _normalize_area({"postal_code": "16850", "city": "Bromma"})
        valid, reason = _validate_geography(r, area)
        assert valid is False
        assert reason == "wrong_location"

    # GEO-14 — Postnummervalidering (section 27)
    def test_geo14_postal_code_normalization_and_validation(self):
        """_normalize_area normaliserar giltiga postnummer; ogiltiga ger icke-5-siffriga koder."""
        import re

        # Giltiga: normaliseras till 5 siffror
        for raw, expected_pc in [
            ("16850 Bromma",  "16850"),
            ("168 50 Bromma", "16850"),
            ({"postal_code": "16850", "city": "Bromma"}, "16850"),
            ({"postal_code": "168 50", "city": "Bromma"}, "16850"),
        ]:
            area = _normalize_area(raw)
            assert re.fullmatch(r'\d{5}', area["postal_code"]), \
                f"Förväntat 5-siffrigt postnummer för {raw!r}, fick {area['postal_code']!r}"
            assert area["postal_code"] == expected_pc, \
                f"Förväntat {expected_pc!r}, fick {area['postal_code']!r}"

        # Ogiltiga: postnummer ska INTE bli 5 siffror efter normalisering
        invalid_inputs = [
            {"postal_code": "1685",   "city": "Bromma"},   # 4 siffror
            {"postal_code": "123456", "city": "Bromma"},   # 6 siffror
            {"postal_code": "16A50",  "city": "Bromma"},   # bokstav i postnummer
            {"postal_code": "abcde",  "city": "Bromma"},   # bokstäver
        ]
        for raw in invalid_inputs:
            area = _normalize_area(raw)
            assert not re.fullmatch(r'\d{5}', area["postal_code"]), \
                f"Ogiltigt postnummer {raw['postal_code']!r} borde INTE normaliseras till 5 siffror"
