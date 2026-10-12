import io
import stat
import zipfile

import pytest
from django.test import override_settings

from scormstack_xblock.config import ScormStackError, storage
from scormstack_xblock.packages import asset_name, byte_range, inspect_package, install_package, safe_path


def package(extra=None, href="index.html", count=1):
    manifest = '<manifest xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"><metadata><schemaversion>1.2</schemaversion></metadata><organizations><organization><title>Demo</title></organization></organizations><resources>'
    manifest += ''.join(f'<resource identifier="s{i}" adlcp:scormtype="sco" href="{href}"/>' for i in range(count))
    manifest += '</resources></manifest>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in {"imsmanifest.xml": manifest, "index.html": "<h1>Hello</h1>", **(extra or {})}.items():
            archive.writestr(name, content)
    output.seek(0)
    return output


def test_install_immutable_versions_and_nested_names(settings):
    source = package({"one/image.png": b"one", "two/image.png": b"two"})
    first = install_package(source)
    second = install_package(source)
    assert first["revision"] == second["revision"]
    assert first["folder"] != second["folder"]
    assert storage().open(asset_name(first, "one/image.png")).read() == b"one"
    assert storage().open(asset_name(first, "two/image.png")).read() == b"two"


@pytest.mark.parametrize("path", ["../index.html", "/index.html", "a/../../x", "a\\x", "a//x", "C:/x"])
def test_zip_traversal_rejected(path):
    with pytest.raises(ScormStackError):
        inspect_package(package({path: "unsafe"}))


@pytest.mark.parametrize("href", ["https://evil.example/index.html", "//evil.example/index.html", "%2e%2e/index.html", "missing.html"])
def test_remote_and_escaped_launch_rejected(href):
    with pytest.raises(ScormStackError):
        inspect_package(package(href=href))


def test_multi_sco_rejected_instead_of_losing_content():
    with pytest.raises(ScormStackError, match="single-SCO"):
        inspect_package(package(count=2))


def test_xml_entity_rejected():
    with pytest.raises(ScormStackError):
        inspect_package(package({"imsmanifest.xml": '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><manifest>&e;</manifest>'}))


def test_nul_path_rejected():
    with pytest.raises(ScormStackError):
        safe_path("x\x00.html")


def test_zip_limits_apply_before_storage(settings):
    settings.SCORMSTACK = {"MAX_EXTRACTED_BYTES": 20}
    with pytest.raises(ScormStackError, match="large"):
        install_package(package())


def test_symlinks_rejected():
    source = package()
    with zipfile.ZipFile(source, "a") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../outside")
    source.seek(0)
    with pytest.raises(ScormStackError):
        inspect_package(source)


@pytest.mark.parametrize("header,expected", [(None, (0, 99, False)), ("bytes=10-20", (10, 20, True)), ("bytes=90-", (90, 99, True)), ("bytes=-10", (90, 99, True))])
def test_media_ranges(header, expected):
    assert byte_range(header, 100) == expected


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=20-10", "bytes=0-1,5-6", "bytes=-0"])
def test_bad_range(header):
    with pytest.raises(ScormStackError):
        byte_range(header, 100)
