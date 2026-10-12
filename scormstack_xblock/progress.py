"""Database-serialized writes, with idempotent retries and explicit tab conflicts."""
import json
import math
import re
import secrets

from django.db import transaction

from .config import ScormStackError
from .models import Attempt

READ_ONLY = {
    "cmi.core.student_id", "cmi.core.student_name", "cmi.core.entry", "cmi.core.lesson_mode",
    "cmi.core.credit", "cmi.core.total_time", "cmi.learner_id", "cmi.learner_name", "cmi.entry",
    "cmi.mode", "cmi.credit", "cmi.total_time", "cmi.launch_data", "cmi._version",
}


def validate_data(data):
    if not isinstance(data, dict) or len(data) > 10000:
        raise ScormStackError("Invalid SCORM state.")
    if len(json.dumps(data).encode()) > 1024 * 1024:
        raise ScormStackError("SCORM state exceeds the 1 MiB limit.")
    for key, value in data.items():
        if (not re.fullmatch(r"cmi\.[A-Za-z0-9_.]+", key) or key in READ_ONLY
                or key.endswith(("._children", "._count")) or not isinstance(value, str)):
            raise ScormStackError("Invalid SCORM data element.")
    return dict(data)


def start(user_id, usage_id, revision):
    with transaction.atomic():
        Attempt.objects.get_or_create(user_id=user_id, usage_id=usage_id, revision=revision)
        row = Attempt.objects.select_for_update().get(user_id=user_id, usage_id=usage_id, revision=revision)
        row.session = secrets.token_hex(24)
        row.sequence = 0
        row.session_seconds = 0
        row.save()
        data = dict(row.data)
        data.pop("cmi.core.session_time", None)
        data.pop("cmi.session_time", None)
        seconds = row.total_seconds
        return {"session": row.session, "data": data, "revision": revision,
                "total_time_12": f"{int(seconds // 3600):04d}:{int(seconds % 3600 // 60):02d}:{seconds % 60:05.2f}",
                "total_time_2004": f"PT{seconds:.2f}S"}


def commit(user_id, usage_id, revision, session, sequence, data, publish):
    cleaned = validate_data(data)
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ScormStackError("Invalid save sequence.")
    with transaction.atomic():
        row = Attempt.objects.select_for_update().filter(user_id=user_id, usage_id=usage_id, revision=revision).first()
        if not row or not secrets.compare_digest(row.session, str(session)):
            raise ScormStackError("This module was opened in another tab. Reload here to resume.")
        if sequence <= row.sequence:
            return {"sequence": row.sequence}  # Response lost after a successful save.
        if sequence != row.sequence + 1:
            raise ScormStackError("A previous save is missing. Reload the module.")
        row.data = cleaned
        elapsed = session_duration(cleaned)
        row.total_seconds += max(0, elapsed - row.session_seconds)
        row.session_seconds = max(elapsed, row.session_seconds)
        row.sequence = sequence
        row.save()
        # Publish inside the transaction: an exception rolls back sequence and permits retry.
        publish(cleaned)
        return {"sequence": sequence}


def session_duration(data):
    if "cmi.core.session_time" in data:
        match = re.fullmatch(r"(\d{2,6}):([0-5]\d):([0-5]\d(?:\.\d{1,2})?)", data["cmi.core.session_time"])
        if match:
            h, m, s = map(float, match.groups())
            return h * 3600 + m * 60 + s
    elif "cmi.session_time" in data:
        match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", data["cmi.session_time"])
        if match and any(match.groups()):
            h, m, s = (float(v or 0) for v in match.groups())
            return h * 3600 + m * 60 + s
    else:
        return 0
    raise ScormStackError("Invalid SCORM session time.")


def outcome(data, version):
    prefix = "cmi.core." if version == "1.2" else "cmi."
    if version == "1.2":
        completion = data.get(prefix + "lesson_status") in {"completed", "passed", "failed"}
    else:
        completion = data.get("cmi.completion_status") == "completed"
    try:
        if version == "2004" and "cmi.score.scaled" in data:
            ratio = float(data["cmi.score.scaled"])
        else:
            raw = float(data[prefix + "score.raw"])
            minimum = float(data.get(prefix + "score.min", "0"))
            maximum = float(data.get(prefix + "score.max", "100"))
            if not all(math.isfinite(v) for v in (raw, minimum, maximum)) or maximum <= minimum:
                return completion, None
            ratio = (raw - minimum) / (maximum - minimum)
        score = max(0.0, min(1.0, ratio)) if math.isfinite(ratio) else None
    except (KeyError, TypeError, ValueError):
        score = None
    return completion, score
