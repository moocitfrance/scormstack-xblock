import hashlib
import json
import re
import uuid
from functools import wraps
from importlib.resources import files
from urllib.parse import quote

from django.core.cache import cache
from django.template import Context, Engine
from django.utils import timezone
from web_fragments.fragment import Fragment
from webob import Response
from xblock.completable import CompletableXBlockMixin
from xblock.core import XBlock
from xblock.exceptions import JsonHandlerError
from xblock.fields import Boolean, Dict, Integer, Scope, String
from xblock.scorable import Score

from . import oauth, packages, progress
from .client import Client, download_package
from .config import ScormStackError, origin, storage, token_resolver
from .models import Attempt, Connection


def author_handler(fn):
    @wraps(fn)
    def wrapped(self, data, suffix=""):
        if not self.can_author():
            raise JsonHandlerError(403, "This action is only available in Studio.")
        self.user_id()  # Authenticated author; CMS handler also enforces course author access.
        try:
            return fn(self, data, suffix)
        except ScormStackError as exc:
            raise JsonHandlerError(400, str(exc)) from exc
    return XBlock.json_handler(wrapped)


@XBlock.needs("user")
@XBlock.wants("studio_user_permissions")
class ScormStackXBlock(CompletableXBlockMixin, XBlock):
    public_dir = "static"
    display_name = String(default="ScormStack", scope=Scope.settings)
    course_id = String(default="", scope=Scope.settings)
    package = Dict(default={}, scope=Scope.content)
    height = Integer(default=650, scope=Scope.settings)
    has_score = Boolean(default=True, scope=Scope.settings)
    icon_class = "problem"
    has_author_view = True

    def studio_permissions(self):
        return self.runtime.service(self, "studio_user_permissions")

    def can_author(self):
        permissions = self.studio_permissions()
        course_key = getattr(self.scope_ids.usage_id, "course_key", None)
        return permissions is not None and course_key is not None and permissions.can_write(course_key)

    def require_studio_read(self):
        permissions = self.studio_permissions()
        if permissions is not None:
            course_key = getattr(self.scope_ids.usage_id, "course_key", None)
            if course_key is None or not permissions.can_read(course_key):
                raise JsonHandlerError(403, "You do not have access to this course.")

    def user_id(self):
        attrs = self.runtime.service(self, "user").get_current_user().opt_attrs
        value = attrs.get("edx-platform.user_id")
        if not value:
            raise JsonHandlerError(401, "Sign in to use this module.")
        return value

    @property
    def usage(self):
        return str(self.scope_ids.usage_id)

    def resource(self, name):
        return files("scormstack_xblock").joinpath(name).read_text()

    def fragment(self, template, script, context):
        html = Engine().from_string(self.resource("templates/" + template)).render(Context(context))
        fragment = Fragment(html)
        fragment.add_css(self.resource("static/style.css"))
        fragment.add_javascript(self.resource("static/" + script))
        return fragment

    def studio_view(self, context=None):
        fragment = self.fragment("studio.html", "studio.js", {"block": self})
        fragment.initialize_js("ScormStackStudio")
        return fragment

    def author_view(self, context=None):
        return self.student_view({"preview": True})

    def student_view(self, context=None):
        preview = bool((context or {}).get("preview")) or self.studio_permissions() is not None
        fragment = self.fragment("player.html", "player.js", {"block": self})
        fragment.initialize_js("ScormStackPlayer", {
            "preview": preview, "revision": self.package.get("revision"),
            "runtimeUrl": self.runtime.local_resource_url(self, "static/runtime.js"),
            "height": self.height,
        })
        return fragment

    @author_handler
    def connection(self, data, suffix=""):
        return {"connected": bool(token_resolver(self.user_id())) or Connection.objects.filter(
            user_id=self.user_id(), origin=origin()).exists(), "editor": self.editor_url()}

    @author_handler
    def connect(self, data, suffix=""):
        return {"url": oauth.begin(self.user_id())}

    @author_handler
    def disconnect(self, data, suffix=""):
        oauth.disconnect(self.user_id())
        return {"connected": False}

    def editor_url(self):
        return origin() + ("/edit/" + quote(self.course_id, safe="") if self.course_id else "/")

    @author_handler
    def courses(self, data, suffix=""):
        page = data.get("page", 1)
        if not isinstance(page, int) or page < 1:
            raise ScormStackError("Invalid page.")
        response = Client(self.user_id()).courses(page)
        return {"courses": [{"id": c["id"], "title": c.get("title", "Untitled")} for c in response["data"]],
                "pagination": response.get("pagination", {})}

    @author_handler
    def create_course(self, data, suffix=""):
        title = str(data.get("title", "")).strip()
        if not title or len(title) > 255:
            raise ScormStackError("Enter a title of 1–255 characters.")
        created = Client(self.user_id()).create(title)["data"]
        # Selection alone does not replace an installed package.
        return {"id": created["id"], "title": created.get("title", title),
                "editor": origin() + "/edit/" + str(uuid.UUID(created["id"]))}

    @author_handler
    def save_settings(self, data, suffix=""):
        try:
            course = str(uuid.UUID(data["course_id"])) if data.get("course_id") else ""
            height = int(data.get("height", 650))
        except (ValueError, TypeError):
            raise ScormStackError("Invalid course ID or player height.")
        title = str(data.get("display_name", "ScormStack")).strip()
        if not title or len(title) > 255 or not 300 <= height <= 1600 or not isinstance(data.get("has_score"), bool):
            raise ScormStackError("Check the title, height (300–1600) and grading setting.")
        self.display_name, self.course_id, self.height, self.has_score = title, course, height, data["has_score"]
        return {"editor": self.editor_url()}

    def job_key(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ScormStackError("Invalid publication request.")
        scope = hashlib.sha256(f"{self.user_id()}:{self.usage}".encode()).hexdigest()
        return f"scormstack-export:{scope}:{job_id}"

    @author_handler
    def publish(self, data, suffix=""):
        if not self.course_id:
            raise ScormStackError("Select and save a ScormStack course first.")
        result = Client(self.user_id()).publish(self.course_id)
        job_id = uuid.uuid4().hex
        cache.set(self.job_key(job_id), {"course_id": self.course_id, "result": result}, 3600)
        return {"job_id": job_id, "status": result.get("status", "pending")}

    @author_handler
    def publication_status(self, data, suffix=""):
        key = self.job_key(str(data.get("job_id", "")))
        job = cache.get(key)
        if not job or job["course_id"] != self.course_id:
            raise ScormStackError("The publication request expired or the selected course changed.")
        result = job["result"]
        if result.get("status") not in {"completed", "failed"}:
            result = Client(self.user_id()).status(job["course_id"], result["request_id"])
            cache.set(key, {**job, "result": result}, 3600)
        return {"status": result.get("status"), "progress": result.get("progress")}

    @author_handler
    def install_publication(self, data, suffix=""):
        key = self.job_key(str(data.get("job_id", "")))
        job = cache.get(key)
        if not job or job["course_id"] != self.course_id or job["result"].get("status") != "completed":
            raise ScormStackError("No completed publication is ready to import.")
        if not cache.add(key + ":installing", True, 600):
            raise ScormStackError("This package is already being imported.")
        try:
            with download_package(job["result"]["download_url"]) as source:
                package = packages.install_package(source)
            package.update({"course_id": self.course_id, "imported_at": timezone.now().isoformat()})
            self.package = package
            return {"revision": package["revision"], "title": package["title"]}
        finally:
            cache.delete(key + ":installing")

    @XBlock.handler
    def upload(self, request, suffix=""):
        if not self.can_author() or request.method != "POST":
            return Response(status=403)
        self.user_id()
        try:
            upload = request.POST.get("package")
            if not getattr(upload, "file", None):
                raise ScormStackError("Choose a SCORM ZIP file.")
            package = packages.install_package(upload.file)
            package.update({"imported_at": timezone.now().isoformat(), "course_id": ""})
            self.package = package
            self.course_id = ""
            return Response(json_body={"revision": package["revision"], "title": package["title"]})
        except ScormStackError as exc:
            return Response(json_body={"error": str(exc)}, status=400)

    @XBlock.json_handler
    def launch(self, data, suffix=""):
        if not self.package or data.get("revision") != self.package.get("revision"):
            raise JsonHandlerError(409, "This module changed. Reload the page.")
        self.require_studio_read()
        preview = self.studio_permissions() is not None
        session = {"data": {}, "session": "", "revision": self.package["revision"]} if preview else progress.start(
            self.user_id(), self.usage, self.package["revision"])
        path = "assets/" + self.package["revision"] + "/" + quote(self.package["launch"], safe="/")
        launch = self.runtime.handler_url(self, "asset", suffix=path)
        if self.package.get("query"):
            launch += ("&" if "?" in launch else "?") + self.package["query"]
        if self.package.get("fragment"):
            launch += "#" + self.package["fragment"]
        # The identity belongs to the LMS, never sent to ScormStack.
        return {**session, "launch": launch, "version": self.package["version"],
                "preview": preview, "learner_id": str(self.user_id()), "learner_name": ""}

    @XBlock.json_handler
    def save_progress(self, data, suffix=""):
        if self.studio_permissions() is not None:
            raise JsonHandlerError(403, "Studio previews do not save learner results.")
        if data.get("revision") != self.package.get("revision"):
            raise JsonHandlerError(409, "A new module version was published. Reload to continue.")
        def publish(values):
            complete, score = progress.outcome(values, self.package["version"])
            self.emit_completion(float(complete))
            if self.has_score and score is not None:
                self.runtime.publish(self, "grade", {"value": score, "max_value": 1.0})
        try:
            return progress.commit(self.user_id(), self.usage, self.package["revision"],
                                   data.get("session"), data.get("sequence"), data.get("data"), publish)
        except ScormStackError as exc:
            raise JsonHandlerError(409, str(exc)) from exc

    def get_score(self):
        row = Attempt.objects.filter(user_id=self.user_id(), usage_id=self.usage,
                                     revision=self.package.get("revision", "")).first()
        _, score = progress.outcome(row.data if row else {}, self.package.get("version", "1.2"))
        return Score(score or 0.0, 1.0)

    def max_score(self):
        return 1.0 if self.has_score else None

    @XBlock.handler
    def asset(self, request, suffix=""):
        # These are normal authenticated XBlock handlers, never thirdparty URLs.
        self.user_id()
        try:
            self.require_studio_read()
        except JsonHandlerError:
            return Response(status=403)
        if request.method not in {"GET", "HEAD"} or not suffix.startswith("assets/") or not self.package:
            return Response(status=404)
        try:
            revision, path = suffix[len("assets/"):].split("/", 1)
            if revision != self.package["revision"]:
                return Response(status=404)
            name = packages.asset_name(self.package, path)
            backend = storage()
            size = backend.size(name)
        except (ScormStackError, FileNotFoundError, ValueError):
            return Response(status=404)
        try:
            start, end, partial = packages.byte_range(request.headers.get("Range"), size)
        except ScormStackError:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        def chunks():
            with backend.open(name, "rb") as content:
                content.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = content.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(max(0, end - start + 1)),
                   "Content-Type": packages.mime_type(name),
                   "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "same-origin"}
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(app_iter=[] if request.method == "HEAD" else chunks(),
                        status=206 if partial else 200, headers=headers)
