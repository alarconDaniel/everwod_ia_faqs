import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
import psycopg2


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

for env_path in (ROOT_DIR / ".env", ROOT_DIR.parent / ".env"):
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

FAQ_SCHEMA = os.getenv("FAQ_SCHEMA", "faq_mvp")
FAQ_RAW_SCHEMA = os.getenv("FAQ_RAW_SCHEMA", "everwod_raw")
FAQ_INGEST_SOURCE = os.getenv("FAQ_INGEST_SOURCE", FAQ_RAW_SCHEMA)


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def get_float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def get_db_config() -> Dict[str, str]:
    return {
        "dbname": os.getenv("DB_NAME", "everwod_faq_mvp"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
    }


def get_db_connection() -> psycopg2.extensions.connection:
    config = get_db_config()
    return psycopg2.connect(
        dbname=config["dbname"],
        user=config["user"],
        password=config["password"],
        host=config["host"],
        port=config["port"],
    )


def get_cors_origins() -> List[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def json_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("value", "text", "content", "message"):
            if key in payload:
                result = json_text(payload[key])
                if result:
                    return result
        parts = [json_text(value) for value in payload.values()]
        return " ".join(part for part in parts if part)
    if isinstance(payload, list):
        parts = [json_text(item) for item in payload]
        return " ".join(part for part in parts if part)
    return str(payload).strip()


def normalize_text(text: Any) -> str:
    return " ".join(str(text or "").strip().split())


def fold_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", normalize_text(text).lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_question(text: str) -> str:
    folded = fold_text(text)
    folded = re.sub(r"[^\w\s?]", " ", folded, flags=re.UNICODE)
    return normalize_text(folded)


def save_json(data: Any, path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, default=str)


def save_json_lines(records: List[Dict[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_lines(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = line.strip()
            if payload:
                records.append(json.loads(payload))
    return records
