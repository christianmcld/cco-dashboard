"""
Universal API Key Loader — SINGLE SOURCE OF TRUTH

ALL scripts must import from here. Never hardcode API keys.
Never read from .env files. Never look anywhere else.

Usage:
    from lib.secrets import get_key, get_all_keys

    apify_token = get_key("APIFY_TOKEN")
    exa_key = get_key("EXA_API_KEY")
    all_keys = get_all_keys()

Source: ~/.ai_secrets.json → api_keys section
"""

import json
from pathlib import Path

SECRETS_PATH = Path.home() / ".ai_secrets.json"
_cache = None


def _load():
    global _cache
    if _cache is None:
        if SECRETS_PATH.exists():
            data = json.loads(SECRETS_PATH.read_text())
            _cache = data.get("api_keys", {})
        else:
            _cache = {}
    return _cache


def get_key(key_name: str, default: str = "") -> str:
    """Get a single API key by name. Returns empty string if not found."""
    keys = _load()
    value = keys.get(key_name, default)
    if not value:
        # Also check environment variables as fallback
        import os
        value = os.environ.get(key_name, default)
    return value or default


def get_all_keys() -> dict:
    """Get all API keys as a dict."""
    return dict(_load())


def get_service(service_name: str) -> dict:
    """Get service config (e.g., 'gohighlevel' returns account, location_id, etc.)."""
    if SECRETS_PATH.exists():
        data = json.loads(SECRETS_PATH.read_text())
        return data.get("services", {}).get(service_name, {})
    return {}


def has_key(key_name: str) -> bool:
    """Check if a key exists and has a non-empty value."""
    return bool(get_key(key_name))


def list_keys() -> list:
    """List all available key names."""
    return list(_load().keys())


def list_missing() -> list:
    """List keys that are placeholder (empty string)."""
    return [k for k, v in _load().items() if not v]
