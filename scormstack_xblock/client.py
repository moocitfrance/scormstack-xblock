import ipaddress
import socket
import tempfile
from urllib.parse import quote, urlsplit

import requests

from .config import ScormStackError, config, origin


def request_json(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=(5, 65), allow_redirects=False, **kwargs)
        if not response.ok or response.is_redirect:
            if response.status_code in (401, 403):
                raise ScormStackError("Reconnect ScormStack and check your workspace permissions and subscription.")
            raise ScormStackError(f"ScormStack returned HTTP {response.status_code}. Retry from Studio.")
        if len(response.content) > 4 * 1024 * 1024:
            raise ScormStackError("ScormStack returned an unexpectedly large response.")
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        if isinstance(exc, ScormStackError):
            raise
        raise ScormStackError("ScormStack could not be reached. No new package has been activated.") from exc


class Client:
    def __init__(self, user_id):
        from .oauth import access_token
        self.token = access_token(user_id)

    def call(self, method, path, **kwargs):
        return request_json(method, origin() + "/api/v1/" + path,
                            headers={"Authorization": "Bearer " + self.token}, **kwargs)

    def courses(self, page=1):
        return self.call("GET", "courses", params={"page": page, "limit": 50})

    def create(self, title):
        return self.call("POST", "courses", json={"title": title})

    def publish(self, course_id):
        return self.call("POST", f"courses/{quote(course_id, safe='')}/publish", json={
            "format": "scorm", "version": "1.2", "include_assets": True,
            "track_progress": False, "learner_identification_type": "none",
        })["data"]

    def status(self, course_id, request_id):
        return self.call("GET", f"courses/{quote(course_id, safe='')}/publish/{quote(request_id, safe='')}")["data"]


def download_package(url):
    """Only server-returned package URLs; credentials never follow asset requests."""
    parsed = urlsplit(url)
    allowed = set(config().get("DOWNLOAD_HOSTS", []))
    if (parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ScormStackError("Ask the operator to allow the export hostname in SCORMSTACK.DOWNLOAD_HOSTS.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ScormStackError("The export hostname must resolve to a public address.")
        source = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        with requests.get(url, timeout=(5, 60), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ScormStackError("The package download failed or expired. Start a new export.")
            size = 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > config().get("MAX_ZIP_BYTES", 256 * 1024 * 1024):
                    raise ScormStackError("The SCORM ZIP is too large.")
                source.write(chunk)
        source.seek(0)
        return source
    except (OSError, requests.RequestException, ScormStackError) as exc:
        if "source" in locals():
            source.close()
        if isinstance(exc, ScormStackError):
            raise
        raise ScormStackError("Could not download the SCORM package. The previous version is unchanged.") from exc
