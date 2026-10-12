from unittest.mock import Mock

import pytest

from scormstack_xblock.config import ScormStackError
from scormstack_xblock.models import Attempt
from scormstack_xblock.progress import commit, outcome, start

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="learner")


def test_resume_deduplication_and_tab_conflict(user):
    session = start(user.pk, "block-1", "a")
    publish = Mock()
    state = {"cmi.suspend_data": "selected answer", "cmi.core.lesson_status": "incomplete"}
    commit(user.pk, "block-1", "a", session["session"], 1, state, publish)
    commit(user.pk, "block-1", "a", session["session"], 1, {}, publish)
    publish.assert_called_once_with(state)
    resumed = start(user.pk, "block-1", "a")
    assert resumed["data"] == state
    with pytest.raises(ScormStackError, match="another tab"):
        commit(user.pk, "block-1", "a", session["session"], 2, {}, publish)
    assert start(user.pk, "block-1", "b")["data"] == {}
    assert Attempt.objects.get(revision="a").data == state


def test_grade_failure_can_be_retried(user):
    session = start(user.pk, "block-1", "a")
    with pytest.raises(RuntimeError):
        commit(user.pk, "block-1", "a", session["session"], 1, {}, Mock(side_effect=RuntimeError))
    assert Attempt.objects.get().sequence == 0
    assert commit(user.pk, "block-1", "a", session["session"], 1, {}, Mock()) == {"sequence": 1}


def test_reject_out_of_order_and_read_only_data(user):
    session = start(user.pk, "b", "r")
    for seq, state in [(2, {}), (1, {"cmi.core.student_id": "another learner"}), (1, {"cmi.suspend_data": 42}), (True, {})]:
        with pytest.raises(ScormStackError):
            commit(user.pk, "b", "r", session["session"], seq, state, Mock())


def test_session_time_is_accumulated_once_and_resumes(user):
    first = start(user.pk, "b", "r")
    commit(user.pk, "b", "r", first["session"], 1, {"cmi.core.session_time": "0000:01:00.00"}, Mock())
    commit(user.pk, "b", "r", first["session"], 2, {"cmi.core.session_time": "0000:01:20.00"}, Mock())
    second = start(user.pk, "b", "r")
    assert second["total_time_12"] == "0000:01:20.00"
    assert "cmi.core.session_time" not in second["data"]
    commit(user.pk, "b", "r", second["session"], 1, {"cmi.core.session_time": "0000:00:30.00"}, Mock())
    assert start(user.pk, "b", "r")["total_time_2004"] == "PT110.00S"


@pytest.mark.parametrize("data,version,expected", [
    ({"cmi.core.lesson_status": "failed", "cmi.core.score.raw": "50"}, "1.2", (True, .5)),
    ({"cmi.completion_status": "incomplete", "cmi.success_status": "passed"}, "2004", (False, None)),
    ({"cmi.completion_status": "completed", "cmi.score.scaled": ".75"}, "2004", (True, .75)),
    ({"cmi.core.score.raw": "NaN"}, "1.2", (False, None)),
    ({"cmi.core.score.raw": "Infinity"}, "1.2", (False, None)),
    ({"cmi.core.score.raw": "10", "cmi.core.score.max": "20"}, "1.2", (False, .5)),
])
def test_outcomes(data, version, expected):
    assert outcome(data, version) == expected
