import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from webob import Request
from xblock.field_data import DictFieldData
from xblock.fields import ScopeIds
from opaque_keys.edx.keys import UsageKey

from scormstack_xblock.block import ScormStackXBlock
from scormstack_xblock.models import Attempt
from scormstack_xblock.packages import install_package
from .test_packages import package

pytestmark = pytest.mark.django_db


@pytest.fixture
def block(django_user_model):
    user = django_user_model.objects.create_user(username="author")
    runtime = Mock()
    runtime.is_author_mode = True
    user_service = Mock()
    user_service.get_current_user.return_value.opt_attrs = {"edx-platform.user_id": user.pk}
    permissions = Mock()
    permissions.can_write.return_value = True
    permissions.can_read.return_value = True
    runtime.service.side_effect = lambda b, name: user_service if name == "user" else (permissions if runtime.is_author_mode else None)
    runtime.handler_url.side_effect = lambda b, handler, suffix="": "/handler/" + handler + "/" + suffix
    runtime.local_resource_url.return_value = "/resource/runtime.js"
    usage = UsageKey.from_string("block-v1:org+course+run+type@scormstack+block@one")
    return ScormStackXBlock(runtime, field_data=DictFieldData({}), scope_ids=ScopeIds(user.pk, "scormstack", "definition", usage))


def call(block, handler, data=None):
    return getattr(block, handler)(Request.blank("/", method="POST", content_type="application/json", body=json.dumps(data or {}).encode()))


def test_no_studio_actions_from_lms(block):
    block.runtime.is_author_mode = False
    response = call(block, "save_settings", {"display_name": "Changed", "has_score": True})
    assert response.status_int == 403
    assert block.display_name == "ScormStack"


def test_read_only_studio_user_cannot_trigger_side_effects(block):
    block.studio_permissions().can_write.return_value = False
    assert call(block, "publish").status_int == 403
    assert call(block, "connect").status_int == 403


def test_import_preview_and_lms_progress(block):
    result = block.upload(SimpleNamespace(method="POST", POST={"package": SimpleNamespace(file=package())}))
    assert result.status_int == 200
    revision = block.package["revision"]
    preview = call(block, "launch", {"revision": revision}).json
    assert preview["preview"] is True
    assert Attempt.objects.count() == 0
    assert call(block, "save_progress", {"revision": revision}).status_int == 403
    block.runtime.is_author_mode = False
    launch = call(block, "launch", {"revision": revision}).json
    state = {"cmi.core.lesson_status": "completed", "cmi.core.score.raw": "80", "cmi.suspend_data": "resume"}
    save = call(block, "save_progress", {**launch, "sequence": 1, "data": state})
    assert save.status_int == 200
    events = [(args[1], args[2]) for args, _ in block.runtime.publish.call_args_list]
    assert ("completion", {"completion": 1.0}) in events
    assert ("grade", {"value": .8, "max_value": 1.0}) in events
    assert block.get_score().raw_earned == .8
    block.package = {**block.package, "revision": "new"}
    assert call(block, "save_progress", {**launch, "sequence": 2, "data": state}).status_int == 409


def test_invalid_upload_keeps_installed_version(block):
    block.package = install_package(package())
    previous = dict(block.package)
    result = block.upload(SimpleNamespace(method="POST", POST={"package": SimpleNamespace(file=io.BytesIO(b"broken"))}))
    assert result.status_int == 400
    assert block.package == previous


def test_media_ranges_and_stale_asset_rejection(block):
    block.package = install_package(package({"audio.mp3": b"0123456789"}))
    prefix = "assets/" + block.package["revision"] + "/"
    result = block.asset(Request.blank("/", headers={"Range": "bytes=3-5"}), prefix + "audio.mp3")
    assert result.status_int == 206
    assert result.body == b"345"
    assert result.content_type == "audio/mpeg"
    assert block.asset(Request.blank("/"), "assets/old/audio.mp3").status_int == 404
    assert block.asset(Request.blank("/"), prefix + "../index.html").status_int == 404


def test_templates_render(block):
    assert "Connect ScormStack" in block.studio_view().content
    assert "ss-frame" in block.student_view().content


def test_publication_flow_and_change_of_selection(block):
    course_id = "a4b6a1ce-920a-43c0-bd88-61420cbae7f1"
    block.course_id = course_id
    with patch("scormstack_xblock.block.Client") as client:
        client.return_value.publish.return_value = {"status": "pending", "request_id": "request-1"}
        job = call(block, "publish").json
        client.return_value.status.return_value = {"status": "completed", "download_url": "https://exports.example/module.zip"}
        assert call(block, "publication_status", job).json["status"] == "completed"
        with patch("scormstack_xblock.block.download_package", return_value=package()):
            result = call(block, "install_publication", job)
        assert result.status_int == 200
        assert block.package["course_id"] == course_id
        block.course_id = "another-course"
        assert call(block, "install_publication", job).status_int == 400


def test_failed_publication_does_not_replace_package(block):
    from scormstack_xblock.config import ScormStackError
    block.course_id = "a4b6a1ce-920a-43c0-bd88-61420cbae7f1"
    block.package = install_package(package())
    previous = dict(block.package)
    with patch("scormstack_xblock.block.Client") as client:
        client.return_value.publish.return_value = {"status": "completed", "download_url": "https://exports.example/module.zip"}
        job = call(block, "publish").json
        with patch("scormstack_xblock.block.download_package", side_effect=ScormStackError("Download failed")):
            assert call(block, "install_publication", job).status_int == 400
    assert block.package == previous
