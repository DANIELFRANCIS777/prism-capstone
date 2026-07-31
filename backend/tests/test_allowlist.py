import pytest

from app.auth import GatewayError, enforce_allowlist
from tests.conftest import make_virtual_key


def _key(allowlist):
    # Not persisted - enforce_allowlist only reads the attribute in memory.
    return make_virtual_key(model_allowlist=allowlist)


def test_allowed_model_passes_without_raising():
    enforce_allowlist(_key(["fast"]), "fast")


def test_disallowed_model_is_rejected_with_403():
    with pytest.raises(GatewayError) as exc_info:
        enforce_allowlist(_key(["fast"]), "smart")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"]["type"] == "model_not_allowed"


def test_empty_allowlist_rejects_everything():
    with pytest.raises(GatewayError):
        enforce_allowlist(_key([]), "fast")


def test_allowlist_checks_the_literal_requested_alias():
    """A key allowed to call `fast` should not automatically be allowed to
    call `auto`, even though `auto` might resolve to `fast` internally -
    the allowlist governs what the caller asked for, not what serves it."""
    key = _key(["fast"])
    enforce_allowlist(key, "fast")
    with pytest.raises(GatewayError):
        enforce_allowlist(key, "auto")
