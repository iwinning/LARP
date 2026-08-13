# ============================================
# SCRAPING PLATTFORM - HITTA PERSONER I SVERIGE
# ============================================
# Använder Firecrawl API (med inbyggt bot-skydd och JS-rendering)
# istället för Playwright direkt.
#
# Användning: python scrapa_alla.py
# API-nyckel: miljövariabeln FIRECRAWL_API_KEY
# ============================================

import os
import re
import json
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from firecrawl import FirecrawlApp

# ============================================
# KONFIGURATION FÖR OLIKA KÄLLOR
# ============================================
# CSS-selektorer och URL:er för varje källa läses från kallor.json.
# Om en sida ändrar sin layout, redigera kallor.json — inte den här filen.

_KALLOR_FIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kallor.json")

_OBLIGATORISKA_NYCKLAR = [
    "namn", "url", "result", "namn_sel",
    "telefon_sel", "adress_sel", "next_page",
]


def _ladda_kallor():
    if not os.path.exists(_KALLOR_FIL):
        print(f"⚠️  Varning: Konfigurationsfilen '{_KALLOR_FIL}' saknas.")
        return {}
    try:
        with open(_KALLOR_FIL, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            print(f"⚠️  Varning: '{_KALLOR_FIL}' är tom eller har fel format.")
            return {}
        godkanda = {}
        for nyckel, kalla in data.items():
            if not isinstance(kalla, dict):
                print(f"⚠️  Post \"{nyckel}\" i kallor.json är inte ett objekt — hoppas över.")
                continue
            saknade = [k for k in _OBLIGATORISKA_NYCKLAR if k not in kalla]
            if saknade:
                namn = kalla.get("namn", f"källa '{nyckel}'")
                print(f"⚠️  {namn} saknar fält: {', '.join(saknade)} — hoppas över.")
            else:
                godkanda[nyckel] = kalla
        return godkanda
    except json.JSONDecodeError as e:
        print(f"⚠️  Kunde inte läsa '{_KALLOR_FIL}': {e}")
        return {}


KALLOR = _ladda_kallor()


# ============================================
# HJÄLPFUNKTION: NÄSTA SIDA
# ============================================

def _next_page_url(soup: "BeautifulSoup", selector_str: str, current_url: str) -> str | None:
    """
    Hitta URL till nästa sida från HTML.

    Hanterar Playwright-stil :has-text('...') pseudo-selektor som
    BeautifulSoup inte förstår — extraherar textkravet och filtrerar manuellt.
    """
    # Extrahera textkrav från :has-text('Nästa') etc.
    has_text_match = re.search(r':has-text\(["\'](.+?)["\']\)', selector_str)
    required_text = has_text_match.group(1).lower() if has_text_match else None

    # Ta bort Playwright-specifika pseudo-selektorer
    clean_sel = re.sub(r':[a-z-]+\([^)]*\)', '', selector_str).strip()

    try:
        candidates = soup.select(clean_sel)
    except Exception:
        return None

    for el in candidates:
        if required_text and required_text not in el.get_text().lower():
            continue
        href = el.get("href", "").strip()
        if href and href != "#":
            return urljoin(current_url, href)
    return None


# ============================================
# FUNKTION: HÄMTA PERSONER
# ============================================

def hamta_personer(stad: str, kalla_val: str, max_antal: int = 5000,
                   progress_callback=None) -> list[dict]:
    """
    Hämta upp till max_antal personer från vald källa via Firecrawl.

    progress_callback(event_type, **kwargs) anropas med:
      - "page_start"  : sida=N, totalt=T
      - "page_done"   : sida=N, hittade=M, totalt=T
      - "blocked"     : anledning=str
      - "no_results"  : sida=N
    """

    def _emit(event_type: str, **kwargs):
        if progress_callback:
            try:
                progress_callback(event_type, **kwargs)
            except Exception:
                pass

    kalla = KALLOR[kalla_val]
    alla_personer: list[dict] = []
    sida = 1

    # Kontrollera API-nyckel
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        msg = (
            "FIRECRAWL_API_KEY saknas. "
            "Lägg till nyckeln som en miljövariabel i Replit Secrets."
        )
        print(f"❌ {msg}")
        _emit("blocked", anledning=msg)
        return []

    fc = FirecrawlApp(api_key=api_key)
    url = kalla["url"].format(stad=stad)

    print(f"\n🔍 Scrapar {stad} från {kalla['namn']} via Firecrawl...")
    print(f"🎯 Mål: {max_antal} personer")
    print("⏳ Varje sida tar några sekunder via API:et...")

    while len(alla_personer) < max_antal:
        print(f"\n📄 Hämtar sida {sida} — {url}")
        _emit("page_start", sida=sida, totalt=len(alla_personer))

        # ── Hämta sidan via Firecrawl ────────────────────────────────────────
        # Vänta 10 sek så att JS-renderade SPA-sidor (Merinfo m.fl.) hinner
        # ladda sina resultat innan vi tar HTML-snapshoten.
        try:
            fc_result = fc.scrape_url(url, formats=["html"], actions=[
                {"type": "wait", "milliseconds": 10000},
            ])
            html = getattr(fc_result, "html", None) or ""
        except Exception as exc:
            print(f"❌ Firecrawl-fel på sida {sida}: {exc}")
            _emit("blocked", anledning=f"Firecrawl-fel: {exc}")
            break

        if not html:
            print("⚠️  Firecrawl returnerade tom HTML.")
            _emit("no_results", sida=sida)
            break

        # ── Parsa HTML med BeautifulSoup ─────────────────────────────────────
        soup = BeautifulSoup(html, "html.parser")
        elements = soup.select(kalla["result"])

        if not elements:
            print(f"⚠️  Inga element med selektor \"{kalla['result']}\" på sida {sida}.")
            if sida == 1:
                print(f"   Tips: Selektorn kan vara föråldrad.")
                print(f"   Uppdatera \"result\" för källa \"{kalla_val}\" i kallor.json.")
            _emit("no_results", sida=sida)
            break

        print(f"🔍 Hittade {len(elements)} poster på sida {sida}")
        saknar_namn = 0

        for idx, element in enumerate(elements, 1):
            try:
                namn_el    = element.select_one(kalla["namn_sel"])
                telefon_el = element.select_one(kalla["telefon_sel"])
                adress_el  = element.select_one(kalla["adress_sel"])

                # separator=" " säkerställer mellanslag vid nästlade <span>-taggar
                n = " ".join(namn_el.get_text(separator=" ", strip=True).split()) if namn_el else ""
                t = telefon_el.get_text(strip=True) if telefon_el else "Saknas"
                a = adress_el.get_text(strip=True)  if adress_el  else "Saknas"

                if not n:
                    saknar_namn += 1
                    n = "Okänd"

                # Rensa telefonnummer
                if t != "Saknas":
                    t = re.sub(r"[\s\-\(\)]", "", t)[:10]

                alla_personer.append({
                    "namn":    n,
                    "telefon": t,
                    "adress":  a,
                    "stad":    stad,
                    "kalla":   kalla["namn"],
                })

            except Exception as exc:
                print(f"   ⚠️  Kunde inte läsa element {idx} på sida {sida}: {exc}")
                continue

        # Varna om majoriteten saknar namn — tyder på föråldrad selektor
        if elements and saknar_namn / len(elements) >= 0.5:
            print(
                f"⚠️  {saknar_namn}/{len(elements)} poster saknar namn — "
                f"selektorn \"{kalla['namn_sel']}\" kan vara föråldrad."
            )
            print(f"   Tips: Uppdatera \"namn_sel\" för källa \"{kalla_val}\" i kallor.json.")

        print(f"✅ Totalt: {len(alla_personer)} personer hittills")
        _emit("page_done", sida=sida, hittade=len(elements), totalt=len(alla_personer))

        if len(alla_personer) >= max_antal:
            print(f"🎯 Nått målet på {max_antal} personer!")
            break

        # ── Nästa sida ────────────────────────────────────────────────────────
        next_url = _next_page_url(soup, kalla["next_page"], url)
        if next_url and next_url != url:
            url = next_url
            sida += 1
            time.sleep(1)  # Kort paus för att inte överbelasta API:et
        else:
            print("📭 Inga fler sidor!")
            break

    if not alla_personer:
        print(f"\n❌ Hittade 0 personer från {kalla['namn']} för \"{stad}\".")
        print("   Möjliga orsaker:")
        print("   1. CSS-selektorerna i kallor.json stämmer inte med sidans nuvarande HTML")
        print("   2. Söktermen gav inga träffar på sidan")
        print("   3. Sidan kräver inloggning för att visa resultat")

    return alla_personer


# ============================================
# FUNKTION: SPARA PERSONER
# ============================================

def spara_personer(personer: list[dict], stad: str):
    """Spara alla personer i JSON och CSV."""

    if not personer:
        print("❌ Inga personer att spara!")
        return

    os.makedirs("resultat", exist_ok=True)

    json_fil = f"resultat/{stad}_{len(personer)}_personer_{int(time.time())}.json"
    with open(json_fil, "w", encoding="utf-8") as f:
        json.dump(personer, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON sparad: {json_fil}")

    csv_fil = f"resultat/{stad}_{len(personer)}_personer_{int(time.time())}.csv"
    with open(csv_fil, "w", encoding="utf-8") as f:
        f.write("Namn,Telefon,Adress\n")
        for p in personer:
            namn    = p.get("namn", "").replace(",", " ")
            telefon = p.get("telefon", "")
            adress  = p.get("adress", "").replace(",", " ")
            f.write(f"{namn},{telefon},{adress}\n")
    print(f"💾 CSV sparad: {csv_fil}")

    return json_fil, csv_fil


# ============================================
# HUVUDPROGRAM (CLI)
# ============================================

def main():
    print("=" * 60)
    print("🔍 HITTA PERSONER I SVERIGE — via Firecrawl")
    print("=" * 60)

    if not KALLOR:
        print("❌ Inga giltiga källor i kallor.json — avbryter.")
        return

    stad = input("\n🏙️  Ange stad: ").strip()
    if not stad:
        print("❌ Du måste ange en stad!")
        return

    print("\n📡 Välj källa:")
    giltiga = list(KALLOR.keys())
    for nyckel, kalla in KALLOR.items():
        print(f"   {nyckel}. {kalla['namn']}")
    val = input(f"\n👉 Välj ({giltiga[0]}–{giltiga[-1]}): ").strip()
    if val not in KALLOR:
        print(f"❌ Ogiltigt val! Tillgängliga: {', '.join(giltiga)}")
        return

    print("\n📊 Hur många personer vill du hämta?")
    print("   1. 100 personer (snabb test)")
    print("   2. 1 000 personer")
    print("   3. 5 000 personer (rekommenderas)")
    print("   4. 10 000 personer (kan ta tid)")
    print("   5. Så många som möjligt")
    antal_val = input("\n👉 Välj (1–5): ").strip()

    antal_map = {"1": 100, "2": 1000, "3": 5000, "4": 10000, "5": 9_999_999}
    max_antal = antal_map.get(antal_val, 5000)

    start_tid = time.time()
    personer = hamta_personer(stad, val, max_antal)
    tid = time.time() - start_tid

    print("\n" + "=" * 60)
    print(f"📊 RESULTAT: {len(personer)} personer i {stad.upper()}")
    print(f"⏱️  Tog {tid:.1f} sekunder")
    print("=" * 60)

    if personer:
        print("\n👤 Första 10:")
        for i, p in enumerate(personer[:10], 1):
            print(f"  {i}. {p['namn']} | 📱 {p['telefon']} | 📍 {p['adress']}")

    if personer:
        spara = input(f"\n💾 Spara {len(personer)} personer? (j/n): ").strip().lower()
        if spara == "j":
            spara_personer(personer, stad)
            print("\n✅ Allt sparat!")

    print("\n✅ KLART!")


if __name__ == "__main__":
    main()
