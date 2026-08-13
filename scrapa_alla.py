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
from playwright.sync_api import sync_playwright

# ============================================
# KONFIGURATION FÖR OLIKA KÄLLOR
# ============================================
# Här anger vi vilka CSS-selektorer som används på varje sida
# Om en sida ändrar sig måste dessa uppdateras

KALLOR = {
    "1": {
        "namn": "Eniro",
        "url": "https://www.eniro.se/personer?q={stad}",
        "result": ".result-item",        # Varje person
        "namn_sel": ".name",              # Personens namn
        "telefon_sel": ".phone",          # Personens telefon
        "adress_sel": ".address",         # Personens adress
        "cookies": "button:has-text('Acceptera alla')",
        "next_page": "a.next:has-text('Nästa')"
    },
    "2": {
        "namn": "Hitta.se",
        "url": "https://www.hitta.se/sök?q={stad}",
        "result": ".result-card",
        "namn_sel": ".result-card__title",
        "telefon_sel": ".result-card__phone",
        "adress_sel": ".result-card__address",
        "cookies": "button:has-text('Acceptera alla')",
        "next_page": "a.next"
    },
    "3": {
        "namn": "Ratsit",
        "url": "https://www.ratsit.se/sok/person?q={stad}",
        "result": ".person-row",
        "namn_sel": ".name-column a",
        "telefon_sel": ".phone-column",
        "adress_sel": ".address-column",
        "cookies": "button:has-text('Acceptera cookies')",
        "next_page": "a.next"
    }
}

# ============================================
# FUNKTION: HÄMTA PERSONER
# ============================================
# Går till vald sida, hämtar alla personer och
# går vidare till nästa sida tills vi har tillräckligt

def hamta_personer(stad, kalla_val, max_antal=5000):
    """Hämta så många personer som möjligt från vald källa"""
    
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
            
            # Hantera cookie-popup (klicka bort den)
            try:
                btn = page.locator(kalla["cookies"])
                if btn.count() > 0:
                    btn.click()
                    time.sleep(1)
            except:
                pass
            
            # Fortsätt tills vi har tillräckligt många personer
            while len(alla_personer) < max_antal:
                print(f"\n📄 Hämtar sida {sida}...")
                
                # Vänta på att resultaten ska ladda
                try:
                    page.wait_for_selector(kalla["result"], timeout=10000)
                except:
                    print("⚠️ Inga fler resultat!")
                    break
                
                # Hitta alla personer på sidan
                elements = page.query_selector_all(kalla["result"])
                print(f"🔍 Hittade {len(elements)} personer på sida {sida}")
                
                # Gå igenom varje person och extrahera data
                for element in elements:
                    try:
                        # Hitta namn, telefon och adress för personen
                        namn = element.query_selector(kalla["namn_sel"])
                        telefon = element.query_selector(kalla["telefon_sel"])
                        adress = element.query_selector(kalla["adress_sel"])
                        
                        # Rensa texten från onödiga mellanslag
                        n = namn.inner_text().strip() if namn else "Okänd"
                        t = telefon.inner_text().strip() if telefon else "Saknas"
                        a = adress.inner_text().strip() if adress else "Saknas"
                        
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
                        
                    except:
                        # Hoppa över personer som inte kunde läsas
                        continue
                
                print(f"✅ Totalt: {len(alla_personer)} personer hittills")
                
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
                except:
                    print("📭 Inga fler sidor!")
                    break
            
            # Stäng webbläsaren
            browser.close()
            
    except Exception as e:
        print(f"❌ Fel: {e}")
    
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
    
    # 2. Fråga efter källa
    print("\n📡 Välj källa:")
    print("   1. Eniro (rekommenderas för många resultat)")
    print("   2. Hitta.se")
    print("   3. Ratsit")
    val = input("\n👉 Välj (1-3): ").strip()
    
    if val not in KALLOR:
        print("❌ Ogiltigt val!")
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
