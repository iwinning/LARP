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
from urllib.parse import urljoin, urlparse, urlunparse, urlencode, parse_qs

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
                   max_profil_anrop: int = 0,
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
    sedda_nycklar: set[tuple] = set()   # dedup: (namn, adress)
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
    base_url = kalla["url"].format(stad=stad)   # Sida 1 URL (utan page=)
    url = base_url

    wait_ms = kalla.get("wait_ms", 10000)
    wait_sek = round(wait_ms / 1000)
    print(f"\n🔍 Scrapar {stad} från {kalla['namn']} via Firecrawl...")
    print(f"🎯 Mål: {max_antal} personer")
    print(f"⏳ ~{wait_sek} s per sida ({kalla['namn']})")

    while len(alla_personer) < max_antal:
        print(f"\n📄 Hämtar sida {sida} — {url}")
        _emit("page_start", sida=sida, totalt=len(alla_personer))

        # ── Hämta sidan via Firecrawl ────────────────────────────────────────
        # Vänta tills JS-renderade sidor hinner ladda sina resultat.
        # Källan kan åsidosätta väntetiden via "wait_ms" i kallor.json
        # (t.ex. Ratsit som kräver 15 sek för att passera Cloudflare).
        try:
            fc_result = fc.scrape_url(url, formats=["html"], actions=[
                {"type": "wait", "milliseconds": wait_ms},
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

                # Rensa telefonnummer — ta bort mellanslag/bindestreck/parentes
                # men trunkera INTE (kapar annars +46-nummer och långa landlinjenummer)
                if t != "Saknas":
                    t = re.sub(r"[\s\-\(\)]", "", t)
                    # Normalisera 0046... → +46...
                    if t.startswith("0046"):
                        t = "+46" + t[4:]

                # Hämta profilsidans URL om källan har profil_url_sel konfigurerat
                _profil_url = None
                profil_sel = kalla.get("profil_url_sel")
                if profil_sel:
                    _a_el = element.select_one(profil_sel)
                    if _a_el:
                        _href = (_a_el.get("href") or "").strip()
                        if _href and not _href.startswith("tel:") and not _href.startswith("#"):
                            _profil_url = urljoin(url, _href)

                # ── Ålder ────────────────────────────────────────────────
                alder = ""
                alder_sel = kalla.get("alder_sel")
                if alder_sel:
                    alder_el = element.select_one(alder_sel)
                    if alder_el:
                        m = re.search(r"\d+", alder_el.get_text(strip=True))
                        if m:
                            alder = m.group(0)
                if not alder:
                    # Fallback: regex på hela kortets text
                    card_text = element.get_text(" ", strip=True)
                    m = re.search(r"\b(\d{1,3})\s*år\b", card_text, re.IGNORECASE)
                    if m:
                        alder = m.group(1)

                # ── Dubblettfilter ───────────────────────────────────────
                nyckel = (n.lower(), a.lower())
                if nyckel in sedda_nycklar:
                    continue
                sedda_nycklar.add(nyckel)

                alla_personer.append({
                    "namn":        n,
                    "telefon":     t,
                    "adress":      a,
                    "alder":       alder,
                    "stad":        stad,
                    "kalla":       kalla["namn"],
                    "_profil_url": _profil_url,
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
        # Bygg nästa URL direkt (sida+1) — länk-hunting i HTML plockar
        # fel länk (föregående sida) när pagination har både prev/next-länkar.
        next_sida = sida + 1
        parsed = urlparse(base_url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params["page"] = [str(next_sida)]
        next_url = urlunparse(parsed._replace(query=urlencode(params, doseq=True)))

        # Kolla om HTML:en faktiskt innehåller en länk till just sida next_sida.
        # Använd exakt regex-match ([?&]page=N[&$]) så att page=2 inte
        # råkar matcha page=20 eller page=21.
        next_page_re = re.compile(rf"[?&]page={next_sida}(?:&|$)")
        has_next = any(
            next_page_re.search(el.get("href") or "")
            for el in soup.select("a[href]")
        )
        if not has_next:
            print("📭 Inga fler sidor!")
            break

        url = next_url
        sida += 1
        time.sleep(1)  # Kort paus för att inte överbelasta API:et

    if not alla_personer:
        print(f"\n❌ Hittade 0 personer från {kalla['namn']} för \"{stad}\".")
        print("   Möjliga orsaker:")
        print("   1. CSS-selektorerna i kallor.json stämmer inte med sidans nuvarande HTML")
        print("   2. Söktermen gav inga träffar på sidan")
        print("   3. Sidan kräver inloggning för att visa resultat")

    # ── Hämta telefonnummer från profilsidor ──────────────────────────────────
    kandidater = [
        p for p in alla_personer
        if p.get("telefon") == "Saknas" and p.get("_profil_url")
    ]
    if kandidater and max_profil_anrop > 0:
        att_hamta = kandidater[:max_profil_anrop]
        print(f"\n📱 Hämtar telefonnummer från {len(att_hamta)} profilsidor "
              f"({len(kandidater)} saknar nummer)…")
        _emit("profil_start", totalt=len(att_hamta), saknar=len(kandidater))

        hittade_telefon = 0
        for i, person in enumerate(att_hamta, 1):
            profil_url_p = person["_profil_url"]
            print(f"  [{i}/{len(att_hamta)}] {person['namn']} — {profil_url_p}")
            telefon = _hamta_telefon_fran_profil(fc, profil_url_p)
            if telefon:
                person["telefon"] = telefon
                hittade_telefon += 1
                print(f"    ✅ {telefon}")
            else:
                print(f"    ❌ Inget nummer")
            _emit("profil_done", klar=i, totalt=len(att_hamta),
                  hittade=hittade_telefon)
            time.sleep(0.5)

        print(f"✅ Profilscraping klar: {hittade_telefon}/{len(att_hamta)} "
              f"nummer hittade")
        _emit("profil_klar", hittade=hittade_telefon, totalt=len(att_hamta))

    # Rensa interna fält innan retur
    for p in alla_personer:
        p.pop("_profil_url", None)

    return alla_personer
def _extrahera_person_nara_tel(tel_el) -> tuple[str, str]:
    """
    Gå uppåt i DOM-trädet från ett tel:-element för att hitta
    personens namn (närmaste rubrik) och adress (address-tagg).
    """
    namn = ""
    adress = ""
    el = tel_el
    for _ in range(12):
        try:
            el = el.parent
        except Exception:
            break
        if not hasattr(el, "find"):
            break
        # Sök namn i rubrik
        if not namn:
            for tag in ["h1", "h2", "h3", "h4"]:
                h = el.find(tag)
                if h:
                    txt = " ".join(h.get_text(separator=" ", strip=True).split())
                    if txt and len(txt) > 2:
                        namn = txt
                        break
        # Sök adress
        if not adress:
            addr = el.find("address")
            if addr:
                adress = addr.get_text(strip=True)
        if namn and adress:
            break
    return namn, adress


def _hitta_nasta_url(soup, current_url: str) -> str | None:
    """
    Försök automatiskt hitta URL till nästa sida via:
    1. rel="next" länk / meta-tagg
    2. Text-länk med "Nästa" / "»" etc.
    3. Inkrementera sidparameter (sida=, page=, p=, pg=, sid=)
    """
    # 1. rel="next"
    for el in soup.find_all(["a", "link"], rel=True):
        rel = el.get("rel", [])
        if isinstance(rel, str):
            rel = [rel]
        if "next" in rel:
            href = el.get("href", "").strip()
            if href:
                return urljoin(current_url, href)

    # 2. Text-länk "Nästa" / "»"
    next_keywords = ["nästa", "next", "»", "›", "forward"]
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        if any(kw in text for kw in next_keywords):
            href = a.get("href", "").strip()
            if href and href != "#" and href != current_url:
                candidate = urljoin(current_url, href)
                if candidate != current_url:
                    return candidate

    # 3. Inkrementera sidparameter i URL
    parsed = urlparse(current_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for param in ["sida", "page", "p", "pg", "sid", "offset"]:
        if param in params:
            try:
                cur = int(params[param][0])
                params[param] = [str(cur + 1)]
                new_query = urlencode(params, doseq=True)
                new_url = urlunparse(parsed._replace(query=new_query))
                return new_url if new_url != current_url else None
            except (ValueError, IndexError):
                pass

    return None


def _hamta_telefon_fran_profil(fc, profil_url: str) -> str | None:
    """
    Besök en persons profilsida och hämta telefonnumret därifrån.
    Returnerar normaliserat telefonnummer eller None om inget hittas.
    """
    try:
        res = fc.scrape_url(profil_url, formats=["html"], actions=[
            {"type": "wait", "milliseconds": 8000},
        ])
        html = getattr(res, "html", None) or ""
    except Exception as exc:
        print(f"    ⚠️  Profilsida misslyckades ({profil_url}): {exc}")
        return None

    if not html:
        return None

    # Om sidan explicit anger att telefon saknas, ge upp direkt
    if "Lägg till telefonnummer" in html or "Saknar telefon" in html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # 1. tel:-länk UTANFÖR annonscontainers
    for tel in soup.find_all("a", href=re.compile(r'^tel:')):
        # Hoppa över om elementet sitter i en annons
        parents = [str(p.get("class", "")) + str(p.get("id", ""))
                   for p in tel.parents if hasattr(p, "get")]
        if any(re.search(r'[Aa]d|[Aa]nnons|sponsor|[Pp]lace|banner|promo',
                         txt) for txt in parents):
            continue
        raw = tel.get("href", "").replace("tel:+46", "0").replace("tel:", "")
        num = re.sub(r"[\s\-\(\)]", "", tel.get_text(strip=True) or raw)
        if 9 <= len(num) <= 10:
            return num

    # 2. Telefonnummer som ren text — välj det som förekommer mest (= personens)
    #    Annonsnummer varierar; personens nummer upprepas i schema, metadata etc.
    phone_re = re.compile(r'(?<!\d)(0\d[\s\-]?\d{2,3}[\s\-]?\d{2,3}[\s\-]?\d{2,3})(?!\d)')
    kandidater: dict[str, int] = {}
    for m in phone_re.finditer(html):
        num = re.sub(r"[\s\-]", "", m.group())
        if 9 <= len(num) <= 10:
            kandidater[num] = kandidater.get(num, 0) + 1

    if kandidater:
        vanligast = max(kandidater, key=lambda k: kandidater[k])
        # Kräv att det förekommer minst 2 gånger — annars är det troligen en annons
        if kandidater[vanligast] >= 2:
            return vanligast

    return None


def hamta_fran_url(start_url: str, max_antal: int = 5000,
                   max_profil_anrop: int = 50,
                   wait_ms: int = 10000,
                   progress_callback=None) -> list[dict]:
    """
    Generisk URL-scraper.  Klistra in valfri sökresultatsida från en
    svensk persondatabas — scrapers bläddrar automatiskt igenom alla
    sidor och hämtar namn, telefon och adress.

    progress_callback(event_type, **kwargs) anropas med samma events
    som hamta_personer: page_start, page_done, blocked, no_results.
    """

    def _emit(event_type: str, **kwargs):
        if progress_callback:
            try:
                progress_callback(event_type, **kwargs)
            except Exception:
                pass

    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        msg = "FIRECRAWL_API_KEY saknas. Lägg till nyckeln i Replit Secrets."
        print(f"❌ {msg}")
        _emit("blocked", anledning=msg)
        return []

    fc = FirecrawlApp(api_key=api_key)
    alla_personer: list[dict] = []
    sett_telefon: set[str] = set()
    url = start_url
    sida = 1
    hostname = urlparse(start_url).hostname or start_url

    wait_sek = round(wait_ms / 1000)
    print(f"\n🔗 URL-läge: {start_url}")
    print(f"🎯 Mål: {max_antal} personer")
    print(f"⏳ ~{wait_sek} s per sida")

    while len(alla_personer) < max_antal:
        print(f"\n📄 Hämtar sida {sida} — {url}")
        _emit("page_start", sida=sida, totalt=len(alla_personer), wait_sek=wait_sek)

        # ── Hämta via Firecrawl ──────────────────────────────────────────────
        try:
            fc_result = fc.scrape_url(url, formats=["html"], actions=[
                {"type": "wait", "milliseconds": wait_ms},
            ])
            html = getattr(fc_result, "html", None) or ""
        except Exception as exc:
            print(f"❌ Firecrawl-fel: {exc}")
            _emit("blocked", anledning=f"Firecrawl-fel: {exc}")
            break

        if not html:
            _emit("no_results", sida=sida)
            break

        soup = BeautifulSoup(html, "html.parser")
        sida_personer: list[dict] = []
        sett_namn: set[str] = set()

        # ── Strategi 1: tel:-länkar (Merinfo m.fl.) ──────────────────────────
        tel_links = soup.select("a[href^='tel:']")
        print(f"  📞 tel:-länkar hittade: {len(tel_links)}")

        for tel_el in tel_links:
            raw = tel_el.get("href", "").replace("tel:+46", "0").replace("tel:", "")
            telefon = tel_el.get_text(strip=True) or raw
            telefon = re.sub(r"[\s\-\(\)]", "", telefon)
            if not telefon or telefon in sett_telefon:
                continue
            namn, adress = _extrahera_person_nara_tel(tel_el)
            sida_personer.append({
                "namn":    namn or "Okänd",
                "telefon": telefon,
                "adress":  adress or "Saknas",
                "stad":    "",
                "kalla":   hostname,
            })
            sett_telefon.add(telefon)
            if namn:
                sett_namn.add(namn)

        # ── Strategi 2: rubrik+adress (Hitta.se m.fl. utan tel:-länk) ────────
        # Hitta personkort via h2/h3 som liknar ett personnamn,
        # extrahera adress via postnummermönster i närheten.
        if not sida_personer:
            postnr_re = re.compile(r'\d{3}\s?\d{2}')
            namn_re   = re.compile(r'^[A-ZÅÄÖ][a-zåäö]+(?:\s[A-ZÅÄÖ][a-zåäö]+)+')

            for h in soup.find_all(["h2", "h3"]):
                rubrik_text = " ".join(
                    h.get_text(separator=" ", strip=True).split()
                )
                # Ta bort ev. åldersiffra i slutet (Hitta.se: "Sead Fazlic 59")
                rubrik_text = re.sub(r'\s+\d{1,3}\s*$', '', rubrik_text).strip()
                # Filtrera bort rubriker som inte ser ut som personnamn
                if not namn_re.match(rubrik_text):
                    continue
                # Inte för lång (slogans, rubriker etc.)
                if len(rubrik_text) > 60:
                    continue

                namn = rubrik_text
                if namn in sett_namn:
                    continue

                # Hitta profillänk i/kring rubriken (generisk approach)
                _profil_url = None
                _a_el = h.find("a", href=True)
                if not _a_el and getattr(h.parent, "name", "") == "a":
                    _a_el = h.parent
                if _a_el:
                    _href = (_a_el.get("href") or "").strip()
                    if _href and not _href.startswith("tel:") and not _href.startswith("#"):
                        _profil_url = urljoin(url, _href)

                # Gå uppåt för att hitta adress och ev. telefon i kortet
                adress  = "Saknas"
                telefon = "Saknas"
                el = h
                for _ in range(8):
                    try:
                        el = el.parent
                    except Exception:
                        break
                    if not hasattr(el, "find"):
                        break
                    # Telefon via tel:-länk
                    if telefon == "Saknas":
                        t_el = el.find("a", href=re.compile(r'^tel:'))
                        if t_el:
                            raw = t_el.get("href","").replace("tel:+46","0").replace("tel:","")
                            telefon = re.sub(r"[\s\-\(\)]", "", t_el.get_text(strip=True) or raw)
                    # Adress via address-tagg
                    if adress == "Saknas":
                        addr_el = el.find("address")
                        if addr_el:
                            adress = addr_el.get_text(strip=True)
                    # Adress via postnummermönster i <p>-text
                    if adress == "Saknas":
                        for p in el.find_all("p"):
                            p_text = p.get_text(separator="\n", strip=True)
                            if postnr_re.search(p_text):
                                # Ta bort kön/ålder-ord, håll adressrader
                                rader = [
                                    rad for rad in p_text.splitlines()
                                    if rad.strip()
                                    and not re.match(
                                        r'^(Man|Kvinna|Övrig|\d{1,3}\s*år?)\s*$',
                                        rad.strip(), re.I
                                    )
                                ]
                                adress = ", ".join(rader)
                                break
                    if adress != "Saknas":
                        break

                sida_personer.append({
                    "namn":        namn,
                    "telefon":     telefon,
                    "adress":      adress,
                    "stad":        "",
                    "kalla":       hostname,
                    "_profil_url": _profil_url,
                })
                sett_namn.add(namn)

            if sida_personer:
                print(f"  👤 Rubrik-strategi: {len(sida_personer)} personer")

        # ── Strategi 3: telefon-regex i ren text ─────────────────────────────
        if not sida_personer:
            phone_re = re.compile(
                r'(?<!\d)0(?:\d[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2,3})(?!\d)'
            )
            for m in phone_re.finditer(html):
                telefon = re.sub(r"[\s\-]", "", m.group())
                if telefon in sett_telefon:
                    continue
                sida_personer.append({
                    "namn":    "Okänd",
                    "telefon": telefon,
                    "adress":  "Saknas",
                    "stad":    "",
                    "kalla":   hostname,
                })
                sett_telefon.add(telefon)
            if sida_personer:
                print(f"  📞 Regex-fallback: {len(sida_personer)} nummer")

        if not sida_personer:
            print(f"  ⚠️  Inga personer hittade på sida {sida}.")
            _emit("no_results", sida=sida)
            break

        # Lägg till (upp till max)
        ledigt = max_antal - len(alla_personer)
        alla_personer.extend(sida_personer[:ledigt])
        _emit("page_done", sida=sida, hittade=len(sida_personer),
              totalt=len(alla_personer))
        print(f"✅ Totalt: {len(alla_personer)} personer")

        if len(alla_personer) >= max_antal:
            print(f"🎯 Nått målet!")
            break

        # ── Nästa sida ────────────────────────────────────────────────────────
        next_url = _hitta_nasta_url(soup, url)
        if next_url and next_url != url:
            url = next_url
            sida += 1
            time.sleep(1)
        else:
            print("📭 Inga fler sidor!")
            break

    if not alla_personer:
        print(f"\n❌ Hittade 0 personer från {hostname}.")
        print("   Tänkbara orsaker:")
        print("   1. Sidan kräver inloggning / visar CAPTCHA")
        print("   2. Resultaten laddas av JavaScript som Firecrawl inte kom åt")
        print("   3. URL:en pekar inte på en resultatsida med telefonnummer")

    # ── Hämta telefonnummer från profilsidor ──────────────────────────────────
    kandidater = [
        p for p in alla_personer
        if p.get("telefon") == "Saknas" and p.get("_profil_url")
    ]
    if kandidater and max_profil_anrop > 0:
        att_hamta = kandidater[:max_profil_anrop]
        print(f"\n📱 Hämtar telefonnummer från {len(att_hamta)} profilsidor "
              f"({len(kandidater)} saknar nummer)…")
        _emit("profil_start", totalt=len(att_hamta),
              saknar=len(kandidater))

        hittade_telefon = 0
        for i, person in enumerate(att_hamta, 1):
            profil_url_p = person["_profil_url"]
            print(f"  [{i}/{len(att_hamta)}] {person['namn']} — {profil_url_p}")
            telefon = _hamta_telefon_fran_profil(fc, profil_url_p)
            if telefon:
                person["telefon"] = telefon
                hittade_telefon += 1
                print(f"    ✅ {telefon}")
            else:
                print(f"    ❌ Inget nummer")
            _emit("profil_done", klar=i, totalt=len(att_hamta),
                  hittade=hittade_telefon)
            time.sleep(0.5)

        print(f"✅ Profilscraping klar: {hittade_telefon}/{len(att_hamta)} "
              f"nummer hittade")
        _emit("profil_klar", hittade=hittade_telefon, totalt=len(att_hamta))

    # Rensa interna fält innan retur
    for p in alla_personer:
        p.pop("_profil_url", None)

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
