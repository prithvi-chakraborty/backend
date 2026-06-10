import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip().strip('"').strip("'")
    if cleaned == "":
        return default
    return cleaned


@dataclass
class Settings:
    margin_engine_url: str | None = _env("MARGIN_ENGINE_URL")
    deadstock_engine_url: str | None = _env("DEADSTOCK_ENGINE_URL")
    credit_engine_url: str | None = _env("CREDIT_ENGINE_URL")
    planner_url: str | None = _env("PLANNER_URL")
    request_timeout_s: int = int(_env("REQUEST_TIMEOUT_S", "60"))
    export_dir: str = _env("EXPORT_DIR", "exports") or "exports"


settings = Settings()
