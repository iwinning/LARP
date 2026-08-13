"""
Säker Playwright-scraper för källor du har rätt att använda.

Programmet kan hämta namn, adress och telefonnummer från en söksida och spara
resultaten som JSON. Det är avsiktligt byggt för företagsdata eller egna/
samtyckta källor, inte för massinsamling av privatpersoners kataloguppgifter.

Installera först:
    pip install -r requirements.txt
    playwright install chromium

Valfritt: skapa en sources.json i projektroten med egna källor:
{
  "min_kalla": {
    "entity_type": "company",
    "url": "https://example.com/search?q={stad}",
    "result_selector": ".result",
    "name_selector": ".name",
    "phone_selector": ".phone",
    "address_selector": ".address",
    "cookie_button": "button:has-text('Acceptera alla')"
  }
}
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus


RESULT_DIR = Path("resultat")
SOURCES_FILE = Path("sources.json")


# Tomt med flit: lägg egna tillåtna källor i sources.json eller via menyn.
KALLOR: dict[str, dict[str, str]] = {}


PERSON_DIRECTORY_PATTERNS = [
    r"eniro\.se/personer",
    r"ratsit\.se/sok/person",
    r"ratsit\.se/person",
    r"mrkoll\.se",
]


def load_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        return sync_playwright, PlaywrightTimeoutError
    except ImportError as error:
        raise RuntimeError(
            "Playwright är inte installerat. Kör:\n"
            "  pip install -r requirements.txt\n"
            "  playwright install chromium"
        ) from error


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def clean_phone(value: str) -> str:
    value = clean_text(value)
    return re.sub(r"[^\d+]", "", value)


def safe_filename(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-zA-Z0-9åäöÅÄÖ_-]+", "_", value)
    return value.strip("_") or "sokning"


def assert_allowed_source(kalla: str, config: dict[str, str]) -> None:
    entity_type = config.get("entity_type", "company").strip().lower()
    url = config.get("url", "")

    if entity_type != "company":
        raise ValueError(
            f"Källan '{kalla}' är markerad som '{entity_type}'. "
            "Det här programmet kör bara företagsdata eller samtyckta egna källor."
        )

    for pattern in PERSON_DIRECTORY_PATTERNS:
        if re.search(pattern, url, flags=re.IGNORECASE):
            raise ValueError(
                f"Källan '{kalla}' ser ut att vara en personkatalog-URL. "
                "Byt till en företagskälla eller en källa där du har samtycke/rätt att samla datan."
            )


def validate_source(kalla: str, config: dict[str, str]) -> None:
    required_keys = [
        "url",
        "result_selector",
        "name_selector",
        "phone_selector",
        "address_selector",
    ]

    missing = [key for key in required_keys if not config.get(key, "").strip()]
    if missing:
        raise ValueError(f"Källan '{kalla}' saknar: {', '.join(missing)}")

    if "{stad}" not in config["url"]:
        raise ValueError(f"Källan '{kalla}' måste ha {{stad}} i URL-mallen.")

    assert_allowed_source(kalla, config)


def load_sources() -> dict[str, dict[str, str]]:
    sources = dict(KALLOR)

    if not SOURCES_FILE.exists():
        return sources

    try:
        with SOURCES_FILE.open("r", encoding="utf-8") as file:
            loaded = json.load(file)

        if not isinstance(loaded, dict):
            raise ValueError("sources.json måste innehålla ett objekt med källnamn.")

        for kalla, config in loaded.items():
            if not isinstance(config, dict):
                raise ValueError(f"Källan '{kalla}' måste vara ett objekt.")
            validate_source(kalla, config)
            sources[kalla] = config

        return sources

    except json.JSONDecodeError as error:
        raise RuntimeError("sources.json innehåller ogiltig JSON.") from error


def ask_for_temporary_source() -> tuple[str, dict[str, str]]:
    print("\nLägg till en tillfällig källa för denna körning.")
    print("Använd bara företagsdata eller en egen/samtyckt källa.")

    kalla = input("Namn på källa: ").strip().lower() or "egen_kalla"
    url = input("URL-mall med {stad}: ").strip()
    result_selector = input("Selektor för varje resultat: ").strip()
    name_selector = input("Selektor för namn/företagsnamn: ").strip()
    phone_selector = input("Selektor för telefon: ").strip()
    address_selector = input("Selektor för adress: ").strip()
    cookie_button = input("Cookie-knappselektor, valfritt: ").strip()

    config = {
        "entity_type": "company",
        "url": url,
        "result_selector": result_selector,
        "name_selector": name_selector,
        "phone_selector": phone_selector,
        "address_selector": address_selector,
        "cookie_button": cookie_button,
    }

    validate_source(kalla, config)
    return kalla, config


def extract_text(element, selector: str) -> str:
    try:
        child = element.query_selector(selector)
        if not child:
            return ""
        return clean_text(child.inner_text())
    except Exception:
        return ""


def click_cookie_button(page, selector: str) -> None:
    if not selector:
        return

    try:
        button = page.locator(selector)
        if button.count() > 0:
            button.first.click(timeout=3000)
            print("Cookie-popup hanterad.")
            time.sleep(1)
    except Exception:
        print("Ingen cookie-popup klickades bort.")


def scrapa(stad: str, kalla: str, config: dict[str, str]) -> list[dict[str, str]]:
    """Scrapa data från vald källa."""
    validate_source(kalla, config)

    sync_playwright, PlaywrightTimeoutError = load_playwright()

    encoded_stad = quote_plus(stad.strip())
    url = config["url"].format(stad=encoded_stad)
    results: list[dict[str, str]] = []

    print(f"\nScrapar '{stad}' från {kalla}...")
    print("Detta kan ta några sekunder.")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                click_cookie_button(page, config.get("cookie_button", ""))
                page.wait_for_selector(config["result_selector"], timeout=15000)

                elements = page.query_selector_all(config["result_selector"])
                print(f"Hittade {len(elements)} resultat.")

                for element in elements:
                    item = {
                        "namn": extract_text(element, config["name_selector"]) or "Saknas",
                        "telefon": clean_phone(extract_text(element, config["phone_selector"])) or "Saknas",
                        "adress": extract_text(element, config["address_selector"]) or "Saknas",
                        "kalla": kalla,
                        "stad": stad,
                        "entity_type": config.get("entity_type", "company"),
                    }

                    if item["namn"] != "Saknas" or item["telefon"] != "Saknas" or item["adress"] != "Saknas":
                        results.append(item)

            except PlaywrightTimeoutError:
                print("Inga resultat hittades eller sidan tog för lång tid att ladda.")
            finally:
                browser.close()

    except Exception as error:
        print(f"Fel vid scraping: {error}")
        return []

    return results


def spara_resultat(results: list[dict[str, str]], stad: str, kalla: str) -> Path:
    """Spara resultaten i en JSON-fil."""
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    filename = RESULT_DIR / f"{safe_filename(stad)}_{safe_filename(kalla)}_{int(time.time())}.json"

    payload = {
        "stad": stad,
        "kalla": kalla,
        "antal": len(results),
        "datum": time.strftime("%Y-%m-%d %H:%M:%S"),
        "resultat": results,
    }

    with filename.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    print(f"Sparade {len(results)} resultat i {filename}")
    return filename


def choose_source(sources: dict[str, dict[str, str]]) -> tuple[str, dict[str, str]] | None:
    if sources:
        print("\nVälj källa:")
        for index, kalla in enumerate(sources, start=1):
            print(f"  {index}. {kalla}")
        print(f"  {len(sources) + 1}. Lägg till tillfällig källa")

        choice = input("Välj: ").strip()
        if choice.isdigit():
            index = int(choice)
            source_names = list(sources)
            if 1 <= index <= len(source_names):
                kalla = source_names[index - 1]
                return kalla, sources[kalla]
            if index == len(sources) + 1:
                return ask_for_temporary_source()
    else:
        print("\nInga källor finns ännu.")
        print("Du kan lägga till en tillfällig källa nu eller skapa sources.json.")
        return ask_for_temporary_source()

    print("Ogiltigt val.")
    return None


def print_results(results: list[dict[str, str]], stad: str) -> None:
    print("\n" + "=" * 50)
    print(f"Hittade {len(results)} resultat i {stad.upper()}")
    print("=" * 50)

    for index, item in enumerate(results, start=1):
        print(f"\n{index}. {item['namn']}")
        print(f"   Telefon: {item['telefon']}")
        print(f"   Adress: {item['adress']}")
        print(f"   Källa: {item['kalla']}")


def main() -> None:
    print("=" * 50)
    print("SÖK OCH SPARA TILLÅTEN KONTAKTDATA")
    print("=" * 50)

    try:
        sources = load_sources()
    except Exception as error:
        print(f"Kunde inte ladda källor: {error}")
        return

    stad = input("\nAnge stad/sökord: ").strip()
    if not stad:
        print("Du måste ange en stad eller ett sökord.")
        return

    chosen = choose_source(sources)
    if not chosen:
        return

    kalla, config = chosen
    results = scrapa(stad, kalla, config)

    if not results:
        print("\nInga resultat hittades.")
        return

    print_results(results, stad)

    spara = input(f"\nSpara {len(results)} resultat? (j/n): ").strip().lower()
    if spara == "j":
        filename = spara_resultat(results, stad, kalla)
        print(f"Klart. Sparat i: {filename}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")
