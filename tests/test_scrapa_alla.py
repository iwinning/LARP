"""
Tester för scrapa_alla.py — _kolla_bot_blockering() och hamta_personer().

Alla tester körs utan en riktig webbläsare. Playwright-objekt ersätts med
mock-objekt som simulerar olika sidtillstånd.
"""

import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Se till att projektets rotkatalog finns i sökvägen
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Importera de funktioner vi testar
# ---------------------------------------------------------------------------
from scrapa_alla import _kolla_bot_blockering, hamta_personer


# ===========================================================================
# HJÄLPFUNKTIONER — skapa falska Playwright-sidobjekt
# ===========================================================================

def _fake_page(url="https://example.se/resultat", title="Resultat", body=""):
    """Returnerar ett MagicMock som liknar ett Playwright Page-objekt."""
    page = MagicMock()
    type(page).url = PropertyMock(return_value=url)
    page.title.return_value = title
    page.inner_text.return_value = body
    return page


def _fake_element(namn="Anna Svensson", telefon="070-123 45 67", adress="Storgatan 1"):
    """Returnerar ett MagicMock som liknar ett Playwright ElementHandle."""
    elem = MagicMock()

    def query_selector(selector):
        mapping = {
            ".name":    _text_node(namn),
            ".phone":   _text_node(telefon),
            ".address": _text_node(adress),
        }
        return mapping.get(selector, None)

    elem.query_selector.side_effect = query_selector
    return elem


def _text_node(text):
    """Minimalt nod-mock med inner_text()."""
    node = MagicMock()
    node.inner_text.return_value = text
    return node


# ===========================================================================
# TESTER FÖR _kolla_bot_blockering()
# ===========================================================================

class TestKollaBotBlockering:

    def test_ren_sida_returnerar_inte_blockerad(self):
        """En vanlig resultatsida ska inte flaggas som blockerad."""
        page = _fake_page(
            url="https://www.eniro.se/resultat?q=Stockholm",
            title="Sökresultat",
            body="Här är dina sökresultat",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Eniro")
        assert not blockerad
        assert anledning == ""

    def test_captcha_i_titeln_ger_blockering(self):
        """En sida med 'captcha' i titeln ska detekteras som blockerad."""
        page = _fake_page(
            url="https://www.eniro.se/resultat",
            title="Captcha — Verifiera att du är människa",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Eniro")
        assert blockerad
        assert "CAPTCHA" in anledning

    def test_captcha_i_url_ger_blockering(self):
        """En URL som innehåller 'captcha' ska flaggas."""
        page = _fake_page(
            url="https://www.eniro.se/captcha?redirect=/resultat",
            title="Vänta lite",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Eniro")
        assert blockerad
        assert "CAPTCHA" in anledning

    def test_are_you_a_robot_i_titeln(self):
        """Titeln 'Are you a robot?' ska ge blockering."""
        page = _fake_page(
            url="https://www.hitta.se/sok",
            title="Are you a robot?",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Hitta.se")
        assert blockerad

    def test_login_url_ger_blockering(self):
        """En omdirigering till /login ska detekteras."""
        page = _fake_page(
            url="https://www.ratsit.se/login?next=/sok",
            title="Logga in",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Ratsit")
        assert blockerad
        assert "Inloggning" in anledning

    def test_access_denied_i_titeln(self):
        """Titeln 'Access Denied' ska ge blockering."""
        page = _fake_page(
            url="https://www.ratsit.se/sok",
            title="Access Denied",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Ratsit")
        assert blockerad

    def test_403_i_titeln(self):
        """Titeln '403 Forbidden' ska ge blockering."""
        page = _fake_page(
            url="https://www.eniro.se/sok",
            title="403 Forbidden",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Eniro")
        assert blockerad

    def test_blockeringstext_i_body(self):
        """'please complete the captcha' i brödtexten ska ge blockering."""
        page = _fake_page(
            url="https://www.eniro.se/resultat",
            title="Vänta",
            body="Vi behöver verifiera dig. Please complete the captcha för att fortsätta.",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Eniro")
        assert blockerad
        assert "please complete the captcha" in anledning.lower()

    def test_query_parameter_med_vanligt_ord_ger_inte_falskt_positivt(self):
        """
        Stadnamn eller söksträng i query-parametrar ska INTE trigga blockering.
        Tidigare bugg: '?q=robot-check-stad' matchade bot-mönster.
        """
        page = _fake_page(
            url="https://www.eniro.se/resultat?q=robot-check-stad",
            title="Sökresultat",
            body="",
        )
        blockerad, _ = _kolla_bot_blockering(page, "Eniro")
        assert not blockerad

    def test_challenge_i_url_path_ger_blockering(self):
        """'challenge' i URL-sökvägen (inte query) ska flaggas."""
        page = _fake_page(
            url="https://www.eniro.se/challenge/verify",
            title="Verifiera dig",
        )
        blockerad, anledning = _kolla_bot_blockering(page, "Eniro")
        assert blockerad


# ===========================================================================
# TESTER FÖR hamta_personer() — mockar hela Playwright-stacken
# ===========================================================================

def _bygg_playwright_mock(page_mock):
    """
    Returnerar en mock av sync_playwright() som kan användas som
    kontexthanterare och levererar page_mock som aktiv sida.
    """
    browser = MagicMock()
    browser.new_page.return_value = page_mock

    p = MagicMock()
    p.chromium.launch.return_value = browser

    pw_ctx = MagicMock()
    pw_ctx.__enter__ = MagicMock(return_value=p)
    pw_ctx.__exit__ = MagicMock(return_value=False)

    return pw_ctx


class TestHamtaPersoner:
    """
    Testar hamta_personer() utan att starta en riktig webbläsare.

    Källkonfigurationen "1" (Eniro) används genomgående så att KALLOR["1"]
    alltid är tillgänglig.
    """

    # ------------------------------------------------------------------
    # Scenario 1: Lyckat uttag — selektorer matchar, en sida med resultat
    # ------------------------------------------------------------------
    def test_lyckad_extraktion(self):
        """Returnerar en lista med de extraherade personerna."""
        elements = [
            _fake_element("Anna Svensson", "070-111 22 33", "Storgatan 1"),
            _fake_element("Bo Karlsson",   "073-444 55 66", "Lillgatan 2"),
        ]

        page = _fake_page(
            url="https://www.eniro.se/resultat",
            title="Sökresultat",
        )
        # Selektorn väntar lyckas
        page.wait_for_selector.return_value = None
        # Första anropet returnerar element; andra (nästa sida) returnerar []
        page.query_selector_all.side_effect = [elements, []]
        # Ingen nästa-sida-knapp
        next_btn = MagicMock()
        next_btn.count.return_value = 0
        page.locator.return_value = next_btn
        # body-text utan blockeringsfraser
        page.inner_text.return_value = "normalt innehåll"

        pw_ctx = _bygg_playwright_mock(page)

        with patch("scrapa_alla.sync_playwright", return_value=pw_ctx):
            resultat = hamta_personer("Stockholm", "1", max_antal=100)

        assert len(resultat) == 2
        assert resultat[0]["namn"] == "Anna Svensson"
        assert resultat[1]["namn"] == "Bo Karlsson"
        assert resultat[0]["kalla"] == "Eniro"

    # ------------------------------------------------------------------
    # Scenario 2: Selektorn hittar 0 element — varning ska skrivas ut
    # ------------------------------------------------------------------
    def test_noll_element_skriver_varning(self, capsys):
        """
        När result-selektorn returnerar 0 element ska en tydlig varning
        skrivas ut och en tom lista returneras.
        """
        page = _fake_page(
            url="https://www.eniro.se/resultat",
            title="Sökresultat",
        )
        page.wait_for_selector.return_value = None
        page.query_selector_all.return_value = []
        page.inner_text.return_value = ""

        pw_ctx = _bygg_playwright_mock(page)

        with patch("scrapa_alla.sync_playwright", return_value=pw_ctx):
            resultat = hamta_personer("Göteborg", "1", max_antal=100)

        ut = capsys.readouterr().out
        assert resultat == []
        assert "0 element" in ut or "inga resultat" in ut.lower() or "0 personer" in ut.lower()

    # ------------------------------------------------------------------
    # Scenario 3: wait_for_selector kastar undantag, ingen blockering
    #             → föråldrad selektor-varning
    # ------------------------------------------------------------------
    def test_foraeldrad_selektor_skriver_varning(self, capsys):
        """
        Om wait_for_selector misslyckas och sidan inte är blockerad ska
        ett meddelande om föråldrad selektor skrivas ut.
        """
        page = _fake_page(
            url="https://www.eniro.se/resultat",
            title="Sökresultat",
        )
        page.wait_for_selector.side_effect = Exception("Timeout waiting for selector")
        page.inner_text.return_value = ""

        pw_ctx = _bygg_playwright_mock(page)

        with patch("scrapa_alla.sync_playwright", return_value=pw_ctx):
            resultat = hamta_personer("Malmö", "1", max_antal=100)

        ut = capsys.readouterr().out
        assert resultat == []
        # Ska nämna selektorn eller att layouten kan ha ändrats
        assert (
            "selektor" in ut.lower()
            or "layout" in ut.lower()
            or "kallor.json" in ut.lower()
        )

    # ------------------------------------------------------------------
    # Scenario 4: CAPTCHA detekteras direkt — returnerar [] med förklaring
    # ------------------------------------------------------------------
    def test_captcha_pa_forsta_sidan_returnerar_tomt(self, capsys):
        """
        Om sidan visar en CAPTCHA-sida direkt ska scraping avbrytas,
        [] returneras och ett tydligt meddelande skrivas ut.
        """
        page = _fake_page(
            url="https://www.eniro.se/captcha",
            title="Captcha — verifiera dig",
        )
        page.inner_text.return_value = ""

        pw_ctx = _bygg_playwright_mock(page)

        with patch("scrapa_alla.sync_playwright", return_value=pw_ctx):
            resultat = hamta_personer("Uppsala", "1", max_antal=100)

        ut = capsys.readouterr().out
        assert resultat == []
        assert "blockerar" in ut.lower() or "captcha" in ut.lower() or "blockerad" in ut.lower()

    # ------------------------------------------------------------------
    # Scenario 5: Inloggningsomdirigering — returnerar [] med förklaring
    # ------------------------------------------------------------------
    def test_login_redirect_returnerar_tomt(self, capsys):
        """
        En omdirigering till /login ska detekteras, [] returneras och
        ett förklarande meddelande skrivas ut.
        """
        page = _fake_page(
            url="https://www.ratsit.se/login?next=/sok",
            title="Logga in för att se resultat",
        )
        page.inner_text.return_value = ""

        pw_ctx = _bygg_playwright_mock(page)

        with patch("scrapa_alla.sync_playwright", return_value=pw_ctx):
            resultat = hamta_personer("Lund", "1", max_antal=100)

        ut = capsys.readouterr().out
        assert resultat == []
        assert "blockerar" in ut.lower() or "inloggning" in ut.lower() or "blockerad" in ut.lower()

    # ------------------------------------------------------------------
    # Scenario 6: Flera sidor — bläddring fungerar
    # ------------------------------------------------------------------
    def test_flera_sidor_ackumulerar_resultat(self):
        """
        Om det finns en nästa-sida-knapp ska scraping fortsätta tills
        det inte finns fler sidor.
        """
        sida1 = [_fake_element(f"Person {i}") for i in range(3)]
        sida2 = [_fake_element(f"Person {i+3}") for i in range(2)]

        page = _fake_page(
            url="https://www.eniro.se/resultat",
            title="Sökresultat",
        )
        page.wait_for_selector.return_value = None
        page.query_selector_all.side_effect = [sida1, sida2]
        page.inner_text.return_value = ""

        # Cookie-knapp: ej synlig (count=0)
        cookie_btn = MagicMock()
        cookie_btn.count.return_value = 0

        # Nästa-knapp: synlig första gången, ej synlig andra gången
        next_btn = MagicMock()
        next_btn.count.side_effect = [1, 0]
        next_btn.is_visible.return_value = True

        def locator_factory(selector):
            from scrapa_alla import KALLOR
            if selector == KALLOR["1"]["cookies"]:
                return cookie_btn
            return next_btn

        page.locator.side_effect = locator_factory

        pw_ctx = _bygg_playwright_mock(page)

        with patch("scrapa_alla.sync_playwright", return_value=pw_ctx):
            resultat = hamta_personer("Örebro", "1", max_antal=100)

        assert len(resultat) == 5
