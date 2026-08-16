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
