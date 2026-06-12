import os
import re
from dataclasses import dataclass


ENGINE_ENV_ALIASES = {
    "margin-mastery": (
        "MARGIN_ENGINE_URL",
        "MARGIN_MASTERY_URL",
        "MARGIN_SERVICE_URL",
        "MARGIN_URL",
        "VITE_MARGIN_ENGINE_URL",
    ),
    "deadstock": (
        "DEADSTOCK_ENGINE_URL",
        "DEAD_STOCK_ENGINE_URL",
        "DEADSTOCK_SERVICE_URL",
        "DEADSTOCK_URL",
        "VITE_DEADSTOCK_ENGINE_URL",
    ),
    "credit-remove": (
        "CREDIT_ENGINE_URL",
        "CREDIT_REMOVE_URL",
        "CREDIT_SERVICE_URL",
        "CREDIT_URL",
        "VITE_CREDIT_ENGINE_URL",
    ),
    "planner": (
        "PLANNER_URL",
        "PLANNER_ENGINE_URL",
        "PROFIT_PATHWAY_URL",
        "PROFIT_PATHWAY_PLANNER_URL",
        "VITE_PLANNER_URL",
    ),
}

DEFAULT_ENGINE_URLS = {
    "margin-mastery": "https://margin-mastery.vercel.app",
    "deadstock": "https://deadstock-count.vercel.app",
    "credit-remove": "https://credit-compass2.vercel.app",
    "planner": "https://profit-pathway-planner2.vercel.app",
}

_SERVICE_LIST_ENV_NAMES = (
    "ENGINE_URLS",
    "SERVICE_URLS",
    "DEPLOYED_ENGINE_URLS",
    "CORS_ORIGINS",
)


def _clean_value(value: str | None, default: str | None = None) -> str | None:
    if value is None:
        return default
    cleaned = str(value).strip().strip('"').strip("'")
    if cleaned == "":
        return default
    return cleaned


def _normalized_env_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _env(name: str, default: str | None = None) -> str | None:
    exact = _clean_value(os.getenv(name))
    if exact is not None:
        return exact

    wanted = _normalized_env_key(name)
    for existing_name, value in os.environ.items():
        if _normalized_env_key(existing_name) == wanted:
            return _clean_value(value, default)
    return default


def _env_first(names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = _env(name)
        if value:
            return value, name
    return None, None


def _service_list_tokens() -> list[str]:
    tokens: list[str] = []
    for env_name in _SERVICE_LIST_ENV_NAMES:
        raw = _env(env_name)
        if not raw:
            continue
        tokens.extend(piece.strip() for piece in re.split(r"[,;\r\n]+", raw) if piece.strip())
    return tokens


def runtime_engine_url(engine_name: str) -> tuple[str | None, str | None]:
    aliases = ENGINE_ENV_ALIASES.get(engine_name, ())
    value, source = _env_first(aliases)
    if value:
        return value, source

    alias_keys = {_normalized_env_key(alias): alias for alias in aliases}
    for token in _service_list_tokens():
        if "=" not in token:
            continue
        embedded_name, embedded_value = token.split("=", 1)
        matched_alias = alias_keys.get(_normalized_env_key(embedded_name))
        cleaned_value = _clean_value(embedded_value)
        if matched_alias and cleaned_value:
            return cleaned_value, f"embedded:{matched_alias}"
    default_url = DEFAULT_ENGINE_URLS.get(engine_name)
    if default_url:
        return default_url, "built-in-default"
    return None, None


def service_discovery_candidates() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for token in _service_list_tokens():
        value = token.split("=", 1)[1] if "=" in token else token
        cleaned = _clean_value(value)
        if not cleaned or not cleaned.startswith(("http://", "https://")):
            continue
        normalized = cleaned.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    return candidates


@dataclass
class Settings:
    margin_engine_url: str | None = runtime_engine_url("margin-mastery")[0]
    deadstock_engine_url: str | None = runtime_engine_url("deadstock")[0]
    credit_engine_url: str | None = runtime_engine_url("credit-remove")[0]
    planner_url: str | None = runtime_engine_url("planner")[0]
    request_timeout_s: int = int(_env("REQUEST_TIMEOUT_S", "60"))
    export_dir: str = _env("EXPORT_DIR", "exports") or "exports"


settings = Settings()
