from dataclasses import dataclass


@dataclass
class ScraperConfig:
    url: str
    result_selector: str
    name_selector: str
    address_selector: str
    phone_selector: str
    timeout_ms: int = 15000
    headless: bool = True


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _safe_inner_text(element, selector: str) -> str:
    try:
        child = element.query_selector(selector)
        if child is None:
            return ""
        return _clean_text(child.inner_text())
    except Exception:
        return ""


def validate_config(config: ScraperConfig) -> None:
    if not config.url.startswith(("http://", "https://")):
        raise ValueError("URL måste börja med http:// eller https://")

    required_selectors = {
        "result_selector": config.result_selector,
        "name_selector": config.name_selector,
        "address_selector": config.address_selector,
        "phone_selector": config.phone_selector,
    }

    missing = [name for name, value in required_selectors.items() if not value.strip()]
    if missing:
        raise ValueError(f"Saknade selektorer: {', '.join(missing)}")


def scrape_search_page(config: ScraperConfig) -> list[dict[str, str]]:
    """
    Går till en söksida, hämtar alla resultat som matchar result_selector
    och extraherar namn, adress och telefonnummer.

    Obs: använd bara programmet på sidor där du har rätt att skrapa data
    och följ webbplatsens villkor, robots.txt och tillämpliga dataskyddsregler.
    """
    validate_config(config)

    results: list[dict[str, str]] = []

    try:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError(
                "Playwright är inte installerat. Kör först: "
                "pip install -r requirements.txt och sedan playwright install chromium"
            ) from error

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=config.headless)
            page = browser.new_page()

            try:
                page.goto(config.url, wait_until="domcontentloaded", timeout=config.timeout_ms)
                page.wait_for_selector(config.result_selector, timeout=config.timeout_ms)

                result_elements = page.query_selector_all(config.result_selector)

                for element in result_elements:
                    item = {
                        "name": _safe_inner_text(element, config.name_selector),
                        "address": _safe_inner_text(element, config.address_selector),
                        "phone": _safe_inner_text(element, config.phone_selector),
                    }

                    if any(item.values()):
                        results.append(item)

            except PlaywrightTimeoutError as error:
                raise RuntimeError("Sidan eller resultaten tog för lång tid att ladda.") from error
            finally:
                browser.close()

    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError(f"Kunde inte skrapa sidan: {error}") from error

    return results
