from pathlib import Path

from src.data_handler import load_json, save_json
from src.scraper import ScraperConfig, scrape_search_page


DATA_FILE = Path("data/results.json")


def print_menu() -> None:
    print("\n=== Playwright Scraper ===")
    print("1. Skrapa en söksida")
    print("2. Visa sparad data")
    print("3. Avsluta")


def ask_for_config() -> ScraperConfig:
    print("\nAnge CSS-selektorer för den söksida du har rätt att skrapa.")
    print("Exempel: .result-card, .name, .address, .phone\n")

    url = input("Sök-URL: ").strip()
    result_selector = input("Selektor för varje resultat: ").strip()
    name_selector = input("Selektor för namn: ").strip()
    address_selector = input("Selektor för adress: ").strip()
    phone_selector = input("Selektor för telefonnummer: ").strip()

    return ScraperConfig(
        url=url,
        result_selector=result_selector,
        name_selector=name_selector,
        address_selector=address_selector,
        phone_selector=phone_selector,
    )


def scrape_and_save() -> None:
    try:
        config = ask_for_config()
        results = scrape_search_page(config)

        if not results:
            print("\nInga resultat hittades.")
            return

        save_json(DATA_FILE, results)
        print(f"\nKlar! Sparade {len(results)} resultat i {DATA_FILE}")

    except KeyboardInterrupt:
        print("\nAvbrutet av användaren.")
    except Exception as error:
        print(f"\nFel vid scraping: {error}")


def show_saved_data() -> None:
    try:
        data = load_json(DATA_FILE)

        if not data:
            print("\nIngen sparad data hittades.")
            return

        print(f"\nSparad data från {DATA_FILE}:")
        for index, item in enumerate(data, start=1):
            print(f"\nResultat {index}")
            print(f"  Namn: {item.get('name', '')}")
            print(f"  Adress: {item.get('address', '')}")
            print(f"  Telefon: {item.get('phone', '')}")

    except Exception as error:
        print(f"\nFel vid läsning av data: {error}")


def main() -> None:
    while True:
        print_menu()
        choice = input("Välj ett alternativ: ").strip()

        if choice == "1":
            scrape_and_save()
        elif choice == "2":
            show_saved_data()
        elif choice == "3":
            print("\nAvslutar programmet.")
            break
        else:
            print("\nOgiltigt val. Försök igen.")


if __name__ == "__main__":
    main()
