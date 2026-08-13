import json
from pathlib import Path
from typing import Any


def save_json(file_path: str | Path, data: list[dict[str, Any]]) -> None:
    path = Path(file_path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    except Exception as error:
        raise RuntimeError(f"Kunde inte spara JSON-data: {error}") from error


def load_json(file_path: str | Path) -> list[dict[str, Any]]:
    path = Path(file_path)

    try:
        if not path.exists():
            return []

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            raise ValueError("JSON-filen innehåller inte en lista.")

        return data

    except json.JSONDecodeError as error:
        raise RuntimeError("JSON-filen är inte giltig JSON.") from error
    except Exception as error:
        raise RuntimeError(f"Kunde inte läsa JSON-data: {error}") from error
