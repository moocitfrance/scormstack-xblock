import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .client import request_json
from .config import ScormStackError, config, origin, token_resolver
from .models import Authorization, Connection

SCOPES = "courses:read courses:create courses:update"


def cipher():
    # SECRET_KEY is server-only; supply a separate stable key when rotating it.
    secret = config().get("TOKEN_ENCRYPTION_SECRET", settings.SECRET_KEY)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def seal(value):
    return cipher().encrypt(value.encode()).decode()


def unseal(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise ScormStackError("The saved connection cannot be decrypted. Reconnect ScormStack.") from exc


def begin(user_id):
    api = origin()
    callback = origin("STUDIO_ORIGIN") + "/scormstack/oauth/callback/"
    # Public client + PKCE: no client secret to expose or distribute with the plugin.
    registration = request_json("POST", api + "/oauth/register", json={
        "client_name": "ScormStack for Open edX", "redirect_uris": [callback],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", "scope": SCOPES,
    })
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
    Authorization.objects.filter(expires_at__lt=timezone.now()).delete()
    Authorization.objects.create(
        state_hash=hashlib.sha256(state.encode()).hexdigest(), user_id=user_id,
        origin=api, client_id=registration["client_id"], verifier=seal(verifier),
        redirect_uri=callback, expires_at=timezone.now() + timedelta(minutes=10),
    )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return api + "/oauth/authorize?" + urlencode({
        "client_id": registration["client_id"], "redirect_uri": callback,
        "response_type": "code", "scope": SCOPES, "resource": api + "/api/blocks-mcp",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })


def complete(user_id, state, code, issuer):
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    with transaction.atomic():
        pending = Authorization.objects.select_for_update().filter(state_hash=state_hash, user_id=user_id).first()
        if not pending or pending.expires_at < timezone.now() or issuer != pending.origin or issuer != origin():
            raise ScormStackError("The authorization expired or belongs to a different connection. Start again.")
        token = request_json("POST", pending.origin + "/oauth/token", data={
            "grant_type": "authorization_code", "client_id": pending.client_id,
            "code": code, "redirect_uri": pending.redirect_uri,
            "code_verifier": unseal(pending.verifier), "resource": pending.origin + "/api/blocks-mcp",
        })
        Connection.objects.update_or_create(user_id=user_id, origin=pending.origin, defaults={
            "client_id": pending.client_id, "access_token": seal(token["access_token"]),
            "refresh_token": seal(token["refresh_token"]),
            "expires_at": timezone.now() + timedelta(seconds=int(token["expires_in"])),
        })
        pending.delete()


def access_token(user_id):
    explicit = token_resolver(user_id)
    if explicit:
        return explicit
    with transaction.atomic():
        connection = Connection.objects.select_for_update().filter(user_id=user_id, origin=origin()).first()
        if not connection:
            raise ScormStackError("Connect your ScormStack account first.")
        if connection.expires_at <= timezone.now() + timedelta(seconds=60):
            # Serialize refresh token rotation. Never automatically retry an uncertain exchange.
            token = request_json("POST", connection.origin + "/oauth/token", data={
                "grant_type": "refresh_token", "client_id": connection.client_id,
                "refresh_token": unseal(connection.refresh_token),
                "resource": connection.origin + "/api/blocks-mcp",
            })
            connection.access_token = seal(token["access_token"])
            connection.refresh_token = seal(token["refresh_token"])
            connection.expires_at = timezone.now() + timedelta(seconds=int(token["expires_in"]))
            connection.save()
        return unseal(connection.access_token)


def disconnect(user_id):
    with transaction.atomic():
        row = Connection.objects.select_for_update().filter(user_id=user_id, origin=origin()).first()
        if row:
            # Revocation endpoint returns an empty response rather than JSON.
            import requests
            try:
                result = requests.post(row.origin + "/oauth/revoke", data={
                    "client_id": row.client_id, "token": unseal(row.refresh_token),
                    "token_type_hint": "refresh_token",
                }, timeout=(5, 15), allow_redirects=False)
                if result.status_code != 200:
                    raise ScormStackError("Revocation failed. Retry or revoke the connection in ScormStack settings.")
            except requests.RequestException as exc:
                raise ScormStackError("Revocation failed. Retry or revoke the connection in ScormStack settings.") from exc
            row.delete()
