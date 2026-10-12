from unittest.mock import Mock, patch

import pytest

from scormstack_xblock.client import download_package, request_json
from scormstack_xblock.config import ScormStackError


@pytest.mark.parametrize("url", ["http://exports.example/a.zip", "https://127.0.0.1/a.zip", "https://evil.example/a.zip", "https://u:p@exports.example/a.zip"])
def test_download_rejects_untrusted_origins(settings, url):
    settings.SCORMSTACK = {"DOWNLOAD_HOSTS": ["exports.example"]}
    with patch("scormstack_xblock.client.requests.get") as get:
        with pytest.raises(ScormStackError):
            download_package(url)
        get.assert_not_called()


def test_download_rejects_private_resolution(settings):
    settings.SCORMSTACK = {"DOWNLOAD_HOSTS": ["exports.example"]}
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        with pytest.raises(ScormStackError, match="public"):
            download_package("https://exports.example/module.zip")


def test_no_redirects_or_token_forwarding(settings):
    settings.SCORMSTACK = {"DOWNLOAD_HOSTS": ["exports.example"]}
    response = Mock(status_code=200)
    response.iter_content.return_value = [b"zip-data"]
    get = Mock()
    get.return_value.__enter__ = Mock(return_value=response)
    get.return_value.__exit__ = Mock(return_value=False)
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]), patch("scormstack_xblock.client.requests.get", get):
        with download_package("https://exports.example/module.zip?signature=secret") as source:
            assert source.read() == b"zip-data"
    assert get.call_args.kwargs["allow_redirects"] is False
    assert "headers" not in get.call_args.kwargs
