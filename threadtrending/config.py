"""Settings resolution: process env -> .env -> st.secrets -> defaults.

Google credentials are kept in memory as a dict and handed to
``from_service_account_info``. No temp file is ever written, so a private key
never lands on disk and Windows ACL questions never come up.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

from .paths import PATHS

log = logging.getLogger(__name__)

SECRET_KEY_RE = re.compile(r"(KEY|TOKEN|SECRET|CREDENTIAL|PRIVATE|PASSWORD)", re.I)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"


@dataclass(frozen=True)
class Settings:
    google_tts_credentials: dict[str, Any] | None = None
    gemini_api_key: str | None = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    ffmpeg_path: Path | None = None
    ffprobe_path: Path | None = None
    duration_budget_s: float = 50.0
    theme: Literal["dark", "light"] = "dark"

    @property
    def has_tts(self) -> bool:
        return self.google_tts_credentials is not None

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_any_background_provider(self) -> bool:
        return bool(self.pexels_api_key or self.pixabay_api_key)

    def redacted(self) -> dict[str, Any]:
        """Safe for logs and for the diagnostics panel."""
        out: dict[str, Any] = {}
        for field_name, value in self.__dict__.items():
            if SECRET_KEY_RE.search(field_name):
                out[field_name] = "<set>" if value else None
            else:
                out[field_name] = str(value) if isinstance(value, Path) else value
        return out


def _streamlit_secrets() -> dict[str, Any]:
    """Read st.secrets, but only when a Streamlit runtime is actually present.

    Importing streamlit outside a run just to probe secrets emits warnings and,
    with no secrets.toml, raises -- so both are swallowed deliberately.
    """
    try:
        import streamlit as st

        return dict(st.secrets)
    except Exception:
        return {}


def _as_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_path(value: Any) -> Path | None:
    if not value:
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_file() else None


def _load_google_credentials(secrets: dict[str, Any]) -> dict[str, Any] | None:
    """Accept a secrets.toml table, an inline JSON blob, or a path on disk."""
    table = secrets.get("gcp_service_account")
    if isinstance(table, dict) and table.get("private_key"):
        return dict(table)

    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON; ignoring")

    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or secrets.get(
        "GOOGLE_APPLICATION_CREDENTIALS"
    )
    if path:
        p = Path(str(path)).expanduser()
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Cannot read service account at %s: %s", p, exc)
    return None


def load(**overrides: Any) -> Settings:
    """Build Settings. Later sources never override an earlier, explicit one."""
    load_dotenv(PATHS.root / ".env", override=False)
    secrets = _streamlit_secrets()

    def pick(name: str, default: Any = None) -> Any:
        return os.getenv(name) or secrets.get(name) or default

    settings = Settings(
        google_tts_credentials=_load_google_credentials(secrets),
        gemini_api_key=pick("GEMINI_API_KEY") or pick("GOOGLE_API_KEY"),
        gemini_model=pick("GEMINI_MODEL", DEFAULT_GEMINI_MODEL),
        pexels_api_key=pick("PEXELS_API_KEY"),
        pixabay_api_key=pick("PIXABAY_API_KEY"),
        ffmpeg_path=_as_path(pick("FFMPEG_PATH")),
        ffprobe_path=_as_path(pick("FFPROBE_PATH")),
        duration_budget_s=_as_float(pick("DURATION_BUDGET_S"), 50.0),
    )
    return replace(settings, **overrides) if overrides else settings
