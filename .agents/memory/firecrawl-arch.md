---
name: Firecrawl scraper architecture
description: Key architectural decisions and quirks for the LARP scraping platform
---

# LARP scraper architecture

**Why:** Replit datacenter IPs are blocked by Cloudflare. Firecrawl provides residential proxies that bypass this.

## Current stack
- `scrapa_alla.py` — Firecrawl-based scraper (BeautifulSoup HTML parsing, no Playwright)
- `app.py` — Flask server with SSE streaming, APScheduler for scheduled jobs
- `kallor.json` — Source config (Merinfo primary, Ratsit secondary with 15s wait)

## Pagination
- Builds next-page URL directly as `base_url + ?page=N` (does NOT hunt for next-page links)
- Confirms page is real by checking regex `[?&]page=N(?:&|$)` against actual HTML

## Deduplication
- Primary key: normalized phone number (strip +46/00/0 prefix → compare digits)
- Secondary key: (name.lower(), address.lower()) tuple

## City extraction
- When searching by postal code, `override_city` is set to "" in `_person_to_result`
- Falls back to `_extrahera_ort_fran_adress(adress)` which regex-extracts city after postal code
- Example: "Storgatan 5, 168 56 Bromma" → "Bromma"

## Housing classification
- "Lägenhet" if "lgh"/"läg"/" apt " in address
- "Okänd" if address is empty or "Saknas"
- "Villa" otherwise (reasonable heuristic — Merinfo explicitly shows "Lgh XXXX" for apartments)

**How to apply:** All scraping work goes through `hamta_personer()` in scrapa_alla.py. The Flask layer in app.py handles filtering, city override, and result mapping via `_person_to_result()`.
