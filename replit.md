# Scraping Plattform – Hitta personer i Sverige

A Python CLI tool that uses Playwright to scrape people's data (name, phone, address) from Swedish public directories.

## Supported sources
- **Eniro.se**
- **Hitta.se**
- **Ratsit.se**

## How to run

```bash
python scrapa_alla.py
```

The script prompts you for:
1. City name (e.g. `stockholm`)
2. Source (Eniro / Hitta.se / Ratsit)
3. How many people to fetch (100 – all)

Results are saved to `resultat/` as both JSON and CSV.

## Stack
- Python 3.12
- Playwright (headless Chromium)

## Files
- `scrapa_alla.py` – main interactive CLI scraper (start here)
- `run.sh` – wrapper that sets LD_LIBRARY_PATH for Chromium's Nix-store libraries, then runs scrapa_alla.py
- `main.py` – alternative CLI with custom CSS selectors (incomplete: `src/scraper.py` is missing)
- `src/data_handler.py` – JSON save/load utilities

## User preferences
- Keep Swedish language in user-facing text of the scraper scripts.
