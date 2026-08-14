# LARP — Svensk persondata-plattform

En Flask-baserad webb-app för att hämta personuppgifter (namn, telefon, adress, ålder) från svenska offentliga folkbokföringskällor i bulk.

## Nuvarande stack

- **Python 3.12** + **Flask** + **Gunicorn**
- **Firecrawl** (residential proxies, kringgår Cloudflare-blockering på Merinfo/Ratsit)
- **APScheduler** för schemalagda nattliga körningar
- **BeautifulSoup4** för HTML-parsning

## Arkitektur

```
app.py          — Flask-server, SSE-streaming, filter, CSV/PDF-export, historik, schema
scrapa_alla.py  — Firecrawl-baserad scraper-motor med paginering och dedup
kallor.json     — Källkonfiguration (Merinfo, Ratsit)
templates/      — Jinja2-templates (index.html)
resultat/       — Sparade körningar som JSON (gitignorerade)
run.sh          — Gunicorn-startskript
```

## Hur det fungerar

1. Användaren anger orter/postnummer i webbgränssnittet
2. Flask startar ett bakgrundsjobb (Python-tråd) som anropar `hamta_personer()` i scrapa_alla.py
3. Firecrawl hämtar Merinfo-sidor via residential proxy
4. Resultaten streamas till klienten via Server-Sent Events (SSE)
5. Användaren kan filtrera och ladda ned som CSV eller PDF

## Sökmodi

- **Standard** — Ange orter/postnummer, välj källa, sätt maxantal
- **Anpassad URL** — Klistra in en Merinfo-sök-URL direkt
- **Schema** — Schemalägg nattlig körning

## Filter

- Bara med telefonnummer
- Bostadstyp (Villa / Lägenhet / Okänd)
- Postal kod — filtrerar till exakt postnummerområde

## Datamodell (person)

```json
{
  "name":         "Förnamn Efternamn",
  "phone":        "0701234567",
  "address":      "Storgatan 5, 168 56 Bromma",
  "age":          "45",
  "city":         "Bromma",
  "housing_type": "Villa | Lägenhet | Okänd",
  "source":       "Merinfo"
}
```

## Viktiga detaljer

- Resultat-JSON-filer läggs i `resultat/` (gitignorerad — innehåller persondata)
- Firecrawl API-nyckel sätts som Replit Secret: `FIRECRAWL_API_KEY`
- Dedupliciering: primärt på normaliserat telefonnummer, sekundärt på namn+adress
- City extraheras ur adresssträngen vid postnummersökning

## User preferences

- Håll svenska i alla användarvända texter i scraper-skripten.
