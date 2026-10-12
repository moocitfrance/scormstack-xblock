"""Validate before extracting. Publish pointers only after every file is stored."""
import hashlib
import mimetypes
import posixpath
import re
import stat
import uuid
import zipfile
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from django.core.files.base import ContentFile, File

from .config import ScormStackError, config, storage


def safe_path(path):
    if (not isinstance(path, str) or not path or "\\" in path or "\x00" in path
            or any(ord(c) < 32 for c in path) or path.startswith("/")
            or any(p in {"..", ".", ""} for p in path.split("/")) or ":" in path):
        raise ScormStackError("The package contains an unsafe file path.")
    return str(PurePosixPath(path))


def inspect_package(source):
    limits = config()
    max_files = limits.get("MAX_FILES", 10000)
    max_size = limits.get("MAX_EXTRACTED_BYTES", 1024 * 1024 * 1024)
    max_file = limits.get("MAX_FILE_BYTES", 256 * 1024 * 1024)
    try:
        archive = zipfile.ZipFile(source)
        infos = archive.infolist()
        if len(infos) > max_files:
            raise ScormStackError("Too many files in this SCORM package.")
        files, total = {}, 0
        for info in infos:
            if info.orig_filename != info.filename:
                raise ScormStackError("The ZIP contains a truncated file name.")
            name = safe_path(info.filename.rstrip("/"))
            if info.is_dir():
                continue
            if (name in files or info.flag_bits & 1
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ScormStackError("Duplicate, encrypted or linked files are not supported.")
            total += info.file_size
            if info.file_size > max_file or total > max_size:
                raise ScormStackError("The extracted SCORM package is too large.")
            files[name] = info
        if "imsmanifest.xml" not in files or files["imsmanifest.xml"].file_size > 2 * 1024 * 1024:
            raise ScormStackError("A valid imsmanifest.xml is required at the ZIP root.")
        root = ElementTree.fromstring(archive.read("imsmanifest.xml"))
        # Single SCO only: do not silently discard a course's other learning objects.
        resources = [e for e in root.iter() if e.tag.split("}")[-1] == "resource"
                     and any(k.split("}")[-1].lower() == "scormtype" and v.lower() == "sco" for k, v in e.attrib.items())]
        if len(resources) != 1:
            raise ScormStackError("This version supports single-SCO packages, as exported by ScormStack.")
        resource = resources[0]
        if any("base" == k.split("}")[-1] for e in root.iter() for k in e.attrib):
            raise ScormStackError("Manifest xml:base paths are not supported.")
        href = urlsplit(resource.get("href", ""))
        if href.scheme or href.netloc:
            raise ScormStackError("The launch file must be inside the package.")
        launch = safe_path(unquote(href.path))
        if launch not in files or not launch.lower().endswith((".html", ".htm")):
            raise ScormStackError("The manifest launch HTML file is missing.")
        schema = next((e.text or "" for e in root.iter() if e.tag.split("}")[-1] == "schemaversion"), "")
        version = "2004" if "2004" in schema or "1.3" in schema else "1.2"
        title = next((e.text or "" for e in root.iter() if e.tag.split("}")[-1] == "title"), "SCORM module")
        return archive, files, {"launch": launch, "query": href.query, "fragment": href.fragment,
                                "version": version, "title": title[:255], "sco_id": resource.get("identifier", "")}
    except ScormStackError:
        raise
    except (zipfile.BadZipFile, ElementTree.ParseError, DefusedXmlException, ValueError, RuntimeError) as exc:
        raise ScormStackError("The ZIP or SCORM manifest is invalid.") from exc


def install_package(source):
    source.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(1024 * 1024):
        size += len(chunk)
        if size > config().get("MAX_ZIP_BYTES", 256 * 1024 * 1024):
            raise ScormStackError("The SCORM ZIP is too large.")
        digest.update(chunk)
    source.seek(0)
    archive, files, metadata = inspect_package(source)
    prefix = safe_path(config().get("STORAGE_PREFIX", "scormstack"))
    # A unique path makes installation atomic even on S3 and across Studio workers.
    folder = f"{prefix}/{uuid.uuid4().hex}"
    backend, written = storage(), []
    try:
        source.seek(0)
        name = backend.save(f"{folder}/package.zip", File(source))
        written.append(name)
        for path, info in files.items():
            with archive.open(info) as content:
                target = f"{folder}/content/{path}"
                name = backend.save(target, File(content, name=path))
                written.append(name)
                if name != target:
                    raise ScormStackError("Storage renamed an asset; publication was cancelled.")
    except Exception:
        for name in written:
            backend.delete(name)
        raise
    finally:
        archive.close()
    return {**metadata, "revision": digest.hexdigest(), "folder": folder, "zip_bytes": size}


def asset_name(package, path):
    # Handler paths arrive decoded by Django. Do not decode them a second time.
    return f"{safe_path(package['folder'])}/content/{safe_path(path)}"


def mime_type(path):
    return {".js": "application/javascript", ".mjs": "application/javascript",
            ".css": "text/css", ".mp3": "audio/mpeg", ".wasm": "application/wasm"}.get(
                posixpath.splitext(path)[1].lower(), mimetypes.guess_type(path)[0] or "application/octet-stream")


def byte_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size == 0:
        raise ScormStackError("Invalid byte range.")
    left, right = match.groups()
    start, end = (max(0, size - int(right)), size - 1) if not left else (int(left), min(int(right), size - 1) if right else size - 1)
    if start > end or start >= size:
        raise ScormStackError("Invalid byte range.")
    return start, end, True
