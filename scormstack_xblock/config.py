from urllib.parse import urlsplit

from django.conf import settings
from django.core.files.storage import default_storage, storages
from django.utils.module_loading import import_string


class ScormStackError(ValueError):
    """An actionable, credential-free error safe to show to authors."""


def config():
    return getattr(settings, "SCORMSTACK", {})


def origin(key="API_ORIGIN"):
    value = config().get(key, "https://app.scormstack.io" if key == "API_ORIGIN" else "").rstrip("/")
    parsed = urlsplit(value)
    local = config().get("ALLOW_LOCAL_HTTP", False) and parsed.hostname in {"localhost", "127.0.0.1", "host.docker.internal"}
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))):
        raise ScormStackError(f"Configure a valid {key} origin in the platform SCORMSTACK settings.")
    return value


def storage():
    alias = config().get("STORAGE_ALIAS")
    return storages[alias] if alias else default_storage


def token_resolver(user_id):
    """Optional per-author server-side PAT resolver; never an XBlock field."""
    path = config().get("TOKEN_RESOLVER")
    return import_string(path)(user_id, origin()) if path else None
