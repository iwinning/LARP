# ============================================
# SCRAPING PLATTFORM - HITTA PERSONER I SVERIGE
# ============================================
# Detta program kan hämta personuppgifter från:
# - Eniro.se
# - Hitta.se
# - Ratsit.se
#
# Användning: python scrapa_alla.py
# ============================================

import time
import json
import os
import re
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

# ============================================
# KONFIGURATION FÖR OLIKA KÄLLOR
# ============================================
# CSS-selektorer och URL:er för varje källa läses från kallor.json.
# Om en sida ändrar sin layout, redigera kallor.json — inte den här filen.

_KALLOR_FIL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kallor.json")

_OBLIGATORISKA_NYCKLAR = ["namn", "url", "result", "namn_sel", "telefon_sel", "adress_sel", "cookies", "next_page"]

def _ladda_kallor():
    if not os.path.exists(_KALLOR_FIL):
        print(f"⚠️  Varning: Konfigurationsfilen '{_KALLOR_FIL}' saknas.")
        print("   Skapa filen kallor.json med dina källors selektorer och försök igen.")
        return {}
    try:
        with open(_KALLOR_FIL, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            print(f"⚠️  Varning: '{_KALLOR_FIL}' är tom eller har fel format (förväntar ett JSON-objekt).")
            return {}
        # Validera att varje källa är ett objekt och har alla obligatoriska nycklar
        godkanda = {}
        for nyckel, kalla in data.items():
            if not isinstance(kalla, dict):
                print(f"⚠️  Varning: Post \"{nyckel}\" i kallor.json är inte ett objekt (hittade: {type(kalla).__name__}).")
                print(f"   Källa \"{nyckel}\" hoppas över — varje källa måste vara ett JSON-objekt med nycklar.")
                continue
            saknade = [k for k in _OBLIGATORISKA_NYCKLAR if k not in kalla]
            if saknade:
                namn = kalla.get("namn", f"källa '{nyckel}'")
                print(f"⚠️  Varning: {namn} (nyckel: \"{nyckel}\") saknar obligatoriska fält: {', '.join(saknade)}")
                print(f"   Källa \"{nyckel}\" hoppas över tills alla fält finns i kallor.json.")
            else:
                godkanda[nyckel] = kalla
        return godkanda
    except json.JSONDecodeError as e:
        print(f"⚠️  Varning: Kunde inte läsa '{_KALLOR_FIL}': {e}")
        print("   Kontrollera att filen är giltig JSON och försök igen.")
        return {}

KALLOR = _ladda_kallor()

# ============================================
# FUNKTION: HÄMTA PERSONER
# ============================================
# Går till vald sida, hämtar alla personer och
# går vidare till nästa sida tills vi har tillräckligt

def _kolla_bot_blockering(page, kalla_namn):
    """
    Kontrollera om sidan blockerar oss (CAPTCHA, inloggning, fel-URL).
    Returnerar (blockerad: bool, anledning: str).

    OBS: kontrollerar aldrig query-parametrar i URL:en (t.ex. ?q=Botkyrka)
    för att undvika falska positiver på stadsnamn som innehåller vanliga ord.
    """
    parsed = urlparse(page.url)
    # Kontrollera bara protokoll + domän + sökväg, INTE query-strängen
    url_path = (parsed.scheme + "://" + parsed.netloc + parsed.path).lower()
    title = page.title().lower()

    # --- Mönster för CAPTCHA / bot-skydd ---
    # Används mot: sidtitel OCH URL-sökväg (ej query-sträng)
    captcha_url_titlar = [
        "captcha",
        "challenge",
        "are-you-human",
        "robot-check",
        "bot-check",
    ]
    # Används bara mot sidtiteln (fritext är säkrare mot falska positiver)
    captcha_titlar = [
        "are you a robot",
        "are you human",
        "robot check",
        "bot check",
        "security check",
        "please verify",
        "verify you are human",
        "bekräfta att du är människa",
    ]

    # --- Mönster för inloggning / åtkomst nekad ---
    login_url_titlar = [
        "/login",
        "/signin",
        "/logga-in",
        "/access-denied",
        "/403",
        "/unauthorized",
    ]
    login_titlar = [
        "access denied",
        "unauthorized",
        "403 forbidden",
        "logga in för att",
        "sign in to",
    ]

    # --- Mönster för felsidor ---
    fel_titlar = [
        "404",
        "page not found",
        "hittades inte",
        "fel sida",
    ]

    for pattern in captcha_url_titlar:
        if pattern in url_path or pattern in title:
            return True, f"CAPTCHA/robot-kontroll detekterad (titel: '{page.title()}', URL: {page.url})"

    for pattern in captcha_titlar:
        if pattern in title:
            return True, f"CAPTCHA/robot-kontroll detekterad (titel: '{page.title()}', URL: {page.url})"

    for pattern in login_url_titlar:
        if pattern in url_path or pattern in title:
            return True, f"Inloggning/åtkomst nekad (titel: '{page.title()}', URL: {page.url})"

    for pattern in login_titlar:
        if pattern in title:
            return True, f"Inloggning/åtkomst nekad (titel: '{page.title()}', URL: {page.url})"

    for pattern in fel_titlar:
        if pattern in title:
            return True, f"Felsida detekterad (titel: '{page.title()}', URL: {page.url})"

    # Kolla sidans brödtext efter specifika blockerings-fraser (flerordiga = färre falska positiver)
    # Används INTE mot URL:en för att undvika träffar på stadsnamn i query-strängen.
    body_block_fraser = [
        "please complete the captcha",
        "complete a captcha",
        "prove you are human",
        "are you a robot",
        "verify you are human",
        "access to this page has been denied",
        "your ip has been blocked",
        "du är inte behörig",
        "du måste logga in",
    ]
    try:
        body_text = page.inner_text("body")[:3000].lower()
        for fras in body_block_fraser:
            if fras in body_text:
                return True, f"Blockeringstext hittad på sidan: \"{fras}\""
    except Exception:
        pass

    return False, ""


def hamta_personer(stad, kalla_val, max_antal=5000, progress_callback=None):
    """Hämta så många personer som möjligt från vald källa.

    progress_callback(event_type, **kwargs) är valfri och anropas med:
      - event_type="page_start"  : sida=N
      - event_type="page_done"   : sida=N, hittade=M, totalt=T
      - event_type="blocked"     : anledning=str
      - event_type="no_results"  : sida=N
    """

    def _emit(event_type, **kwargs):
        if progress_callback:
            try:
                progress_callback(event_type, **kwargs)
            except Exception:
                pass

    # Hämta inställningar för vald källa
    kalla = KALLOR[kalla_val]
    alla_personer = []  # Lista där vi sparar alla personer
    sida = 1            # Vilken sida vi är på
    
    print(f"\n🔍 Scrapar {stad} från {kalla['namn']}...")
    print(f"🎯 Mål: {max_antal} personer")
    print("⏳ Detta kan ta några minuter...")
    
    try:
        # Starta Playwright (webbläsarautomatisering)
        with sync_playwright() as p:
            # Starta webbläsaren i bakgrunden (headless=True)
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Gå till söksidan
            url = kalla["url"].format(stad=stad)
            page.goto(url, timeout=30000)
            time.sleep(2)

            # Kontrollera direkt om sidan blockerar oss
            blockerad, anledning = _kolla_bot_blockering(page, kalla["namn"])
            if blockerad:
                print(f"\n🚫 {kalla['namn']} blockerar scraping!")
                print(f"   Anledning: {anledning}")
                print(f"   Tips: Prova igen senare eller välj en annan källa.")
                _emit("blocked", anledning=anledning)
                browser.close()
                return []
            
            # Hantera cookie-popup (klicka bort den)
            try:
                btn = page.locator(kalla["cookies"])
                if btn.count() > 0:
                    btn.click()
                    time.sleep(1)
            except Exception:
                pass
            
            # Fortsätt tills vi har tillräckligt många personer
            while len(alla_personer) < max_antal:
                print(f"\n📄 Hämtar sida {sida}...")
                _emit("page_start", sida=sida, totalt=len(alla_personer))

                # Kontrollera bot-blockering igen (kan ske efter omdirigeringar)
                blockerad, anledning = _kolla_bot_blockering(page, kalla["namn"])
                if blockerad:
                    print(f"\n🚫 {kalla['namn']} blockerade oss på sida {sida}!")
                    print(f"   Anledning: {anledning}")
                    print(f"   Tips: Prova igen senare eller välj en annan källa.")
                    _emit("blocked", anledning=anledning)
                    break
                
                # Vänta på att resultaten ska ladda
                try:
                    page.wait_for_selector(kalla["result"], timeout=10000)
                except Exception as e:
                    # Kontrollera om det beror på blockering eller föråldrad selektor
                    blockerad, anledning = _kolla_bot_blockering(page, kalla["namn"])
                    if blockerad:
                        print(f"\n🚫 {kalla['namn']} blockerade oss på sida {sida}!")
                        print(f"   Anledning: {anledning}")
                        print(f"   Tips: Prova igen senare eller välj en annan källa.")
                        _emit("blocked", anledning=anledning)
                    else:
                        print(f"\n⚠️  Inga resultat på sida {sida} från {kalla['namn']}.")
                        print(f"   Selektor som misslyckades: \"{kalla['result']}\"")
                        print(f"   Nuvarande URL: {page.url}")
                        if sida == 1:
                            print(f"   ⚠️  Sidan kan ha ändrat sin layout — selektorn \"{kalla['result']}\" kanske är föråldrad.")
                            print(f"   Tips: Uppdatera \"result\" för källa \"{kalla_val}\" i kallor.json.")
                        else:
                            print(f"   (Inga fler sidor med resultat)")
                        _emit("no_results", sida=sida)
                    break
                
                # Hitta alla personer på sidan
                elements = page.query_selector_all(kalla["result"])

                if len(elements) == 0:
                    print(f"\n⚠️  Resultat-selektorn hittade 0 element på sida {sida}.")
                    print(f"   Selektor: \"{kalla['result']}\" (källa: {kalla['namn']}, URL: {page.url})")
                    if sida == 1:
                        print(f"   ⚠️  Layouten kan ha ändrats — selektorn verkar föråldrad.")
                        print(f"   Tips: Uppdatera \"result\" för källa \"{kalla_val}\" i kallor.json.")
                    _emit("no_results", sida=sida)
                    break

                print(f"🔍 Hittade {len(elements)} personer på sida {sida}")
                
                # Räkna hur många poster som saknade namn (kan tyda på fel selektor)
                saknar_namn = 0

                # Gå igenom varje person och extrahera data
                for idx, element in enumerate(elements, 1):
                    try:
                        # Hitta namn, telefon och adress för personen
                        namn = element.query_selector(kalla["namn_sel"])
                        telefon = element.query_selector(kalla["telefon_sel"])
                        adress = element.query_selector(kalla["adress_sel"])
                        
                        # Rensa texten från onödiga mellanslag
                        n = namn.inner_text().strip() if namn else ""
                        t = telefon.inner_text().strip() if telefon else "Saknas"
                        a = adress.inner_text().strip() if adress else "Saknas"

                        if not n:
                            saknar_namn += 1
                            n = "Okänd"
                        
                        # Rensa telefonnumret (ta bort mellanslag, bindestreck etc)
                        if t != "Saknas":
                            t = t.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
                            t = t[:10]  # Ta bara de första 10 siffrorna
                        
                        # Spara personen i listan
                        alla_personer.append({
                            "namn": n,
                            "telefon": t,
                            "adress": a,
                            "stad": stad,
                            "kalla": kalla["namn"]
                        })
                        
                    except Exception as e:
                        # Logga vilket element som misslyckades istället för att svälja felet tyst
                        print(f"   ⚠️  Kunde inte läsa element {idx} på sida {sida}: {e}")
                        continue

                # Varna om många poster saknar namn — tyder på föråldrad namnsselektor
                if saknar_namn > 0 and len(elements) > 0:
                    andel = saknar_namn / len(elements)
                    if andel >= 0.5:
                        print(f"\n⚠️  {saknar_namn}/{len(elements)} poster på sida {sida} saknar namn.")
                        print(f"   Namn-selektor: \"{kalla['namn_sel']}\" (källa: {kalla['namn']})")
                        print(f"   Tips: Selektorn kan vara föråldrad — uppdatera \"namn_sel\" för källa \"{kalla_val}\" i kallor.json.")
                
                print(f"✅ Totalt: {len(alla_personer)} personer hittills")
                _emit("page_done", sida=sida, hittade=len(elements), totalt=len(alla_personer))
                
                # Kolla om vi har tillräckligt
                if len(alla_personer) >= max_antal:
                    print(f"🎯 Nått målet på {max_antal} personer!")
                    break
                
                # Försök gå till nästa sida
                try:
                    next_btn = page.locator(kalla["next_page"])
                    if next_btn.count() > 0 and next_btn.is_visible():
                        next_btn.click()
                        time.sleep(2)
                        sida += 1
                    else:
                        print("📭 Inga fler sidor!")
                        break
                except Exception:
                    print("📭 Inga fler sidor!")
                    break
            
            # Stäng webbläsaren
            browser.close()
            
    except Exception as e:
        print(f"❌ Oväntat fel: {e}")
    
    if not alla_personer:
        print(f"\n❌ Hittade 0 personer från {kalla['namn']} för '{stad}'.")
        print(f"   Möjliga orsaker:")
        print(f"   1. Sidan blockerar automatisk scraping (CAPTCHA / bot-skydd)")
        print(f"   2. CSS-selektorerna i KALLOR är föråldrade (sidan har ändrat sin layout)")
        print(f"   3. Sökningen gav inga träffar för '{stad}'")
        print(f"   Tips: Kolla URL och selektorer för källa \"{kalla_val}\" i kallor.json.")

    # Returnera alla personer vi hittade
    return alla_personer

# ============================================
# FUNKTION: SPARA PERSONER
# ============================================
# Sparar alla personer i två format:
# - JSON (för programmering)
# - CSV (för Excel)

def spara_personer(personer, stad):
    """Spara alla personer i JSON och CSV"""
    
    if not personer:
        print("❌ Inga personer att spara!")
        return
    
    # Skapa mappen "resultat" om den inte finns
    os.makedirs("resultat", exist_ok=True)
    
    # Spara som JSON
    json_fil = f"resultat/{stad}_{len(personer)}_personer_{int(time.time())}.json"
    with open(json_fil, "w", encoding="utf-8") as f:
        json.dump(personer, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON sparad: {json_fil}")
    
    # Spara som CSV (kan öppnas i Excel)
    csv_fil = f"resultat/{stad}_{len(personer)}_personer_{int(time.time())}.csv"
    with open(csv_fil, "w", encoding="utf-8") as f:
        f.write("Namn,Telefon,Adress\n")
        for p in personer:
            f.write(f"{p['namn']},{p['telefon']},{p['adress']}\n")
    print(f"💾 CSV sparad: {csv_fil}")
    
    return json_fil, csv_fil

# ============================================
# HUVUDPROGRAM
# ============================================
# Detta körs när du startar programmet

def main():
    print("=" * 60)
    print("🔍 HITTA PERSONER I SVERIGE - STORT URVAL")
    print("=" * 60)
    
    # 1. Fråga efter stad
    stad = input("\n🏙️ Ange stad: ").strip()
    if not stad:
        print("❌ Du måste ange en stad!")
        return
    
    # 2. Fråga efter källa (byggs dynamiskt från kallor.json)
    if not KALLOR:
        print("❌ Inga giltiga källor hittades i kallor.json — kan inte fortsätta.")
        return

    print("\n📡 Välj källa:")
    for nyckel, kalla in KALLOR.items():
        print(f"   {nyckel}. {kalla['namn']}")
    giltiga = list(KALLOR.keys())
    val = input(f"\n👉 Välj ({'-'.join([giltiga[0], giltiga[-1]] if len(giltiga) > 1 else [giltiga[0]])}): ").strip()

    if val not in KALLOR:
        print(f"❌ Ogiltigt val! Tillgängliga alternativ: {', '.join(giltiga)}")
        return
    
    # 3. Fråga hur många personer
    print("\n📊 Hur många personer vill du hämta?")
    print("   1. 100 personer (snabb test)")
    print("   2. 1 000 personer")
    print("   3. 5 000 personer (rekommenderas)")
    print("   4. 10 000 personer (kan ta tid)")
    print("   5. Så många som möjligt (alla)")
    
    antal_val = input("\n👉 Välj (1-5): ").strip()
    
    antal_map = {
        "1": 100,
        "2": 1000,
        "3": 5000,
        "4": 10000,
        "5": 9999999
    }
    max_antal = antal_map.get(antal_val, 5000)
    
    if max_antal == 9999999:
        print("\n🚀 Hämtar ALLA personer! Detta kan ta lång tid...")
    else:
        print(f"\n🚀 Hämtar {max_antal} personer...")
    
    # 4. Starta scraping
    start_tid = time.time()
    personer = hamta_personer(stad, val, max_antal)
    tid = time.time() - start_tid
    
    # 5. Visa resultat
    print("\n" + "=" * 60)
    print(f"📊 RESULTAT")
    print("=" * 60)
    print(f"👤 Hittade {len(personer)} personer i {stad.upper()}")
    print(f"⏱️ Tog {tid:.1f} sekunder")
    
    if personer:
        print("\n👤 Första 10 personerna:")
        print("-" * 40)
        for i, p in enumerate(personer[:10], 1):
            print(f"{i}. {p['namn']} | 📱 {p['telefon']}")
    
    # 6. Fråga om spara
    if personer:
        spara = input(f"\n💾 Spara {len(personer)} personer? (j/n): ").strip().lower()
        if spara == "j":
            spara_personer(personer, stad)
            print("\n✅ Allt sparat!")
    
    print("\n✅ KLART!")

# ============================================
# STARTA PROGRAMMET
# ============================================
if __name__ == "__main__":
    main()
