import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

import pytest
from django.utils import timezone

from scormstack_xblock import oauth
from scormstack_xblock.config import ScormStackError
from scormstack_xblock.models import Authorization, Connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="oauth-author")


def test_pkce_issuer_user_binding_and_replay(user):
    with patch.object(oauth, "request_json", return_value={"client_id": "registered-client"}) as request:
        url = oauth.begin(user.pk)
    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert "courses:publish" not in params["scope"][0]
    assert request.call_args.kwargs["json"]["token_endpoint_auth_method"] == "none"
    state = params["state"][0]
    row = Authorization.objects.get()
    assert row.state_hash == hashlib.sha256(state.encode()).hexdigest()
    assert row.verifier != oauth.unseal(row.verifier)
    with pytest.raises(ScormStackError):
        oauth.complete(user.pk + 1, state, "code", "https://app.scormstack.io")
    with pytest.raises(ScormStackError):
        oauth.complete(user.pk, state, "code", "https://wrong.example")
    with patch.object(oauth, "request_json", return_value={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}) as exchange:
        oauth.complete(user.pk, state, "code", "https://app.scormstack.io")
        assert exchange.call_args.kwargs["data"]["code_verifier"] == oauth.unseal(row.verifier)
    connection = Connection.objects.get()
    assert connection.access_token != "access"
    assert oauth.access_token(user.pk) == "access"
    with pytest.raises(ScormStackError):
        oauth.complete(user.pk, state, "code", "https://app.scormstack.io")


def test_expired_authorization_rejected(user):
    with patch.object(oauth, "request_json", return_value={"client_id": "client"}):
        url = oauth.begin(user.pk)
    Authorization.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ScormStackError):
        oauth.complete(user.pk, parse_qs(urlsplit(url).query)["state"][0], "code", "https://app.scormstack.io")


def test_refresh_rotation(user):
    Connection.objects.create(user=user, origin="https://app.scormstack.io", client_id="client",
        access_token=oauth.seal("old"), refresh_token=oauth.seal("rotate"), expires_at=timezone.now())
    with patch.object(oauth, "request_json", return_value={"access_token": "new", "refresh_token": "rotated", "expires_in": 3600}) as request:
        assert oauth.access_token(user.pk) == "new"
        assert oauth.access_token(user.pk) == "new"
    request.assert_called_once()
    assert oauth.unseal(Connection.objects.get().refresh_token) == "rotated"
