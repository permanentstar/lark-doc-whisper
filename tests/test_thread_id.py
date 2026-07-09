import pytest

from lark_doc_whisper.thread_id import build, parse


def test_build_basic():
    tid = build("R6dXdu", "ou_abc")
    assert tid == "doc__R6dXdu__user__ou_abc"


def test_parse_basic():
    assert parse("doc__R6dXdu__user__ou_abc") == ("R6dXdu", "ou_abc")


def test_round_trip():
    file_token = "doc_fake_file_token_0001"
    user = "ou_fake_user_0001"
    assert parse(build(file_token, user)) == (file_token, user)


@pytest.mark.parametrize("bad", ["", "doc_xx", "doc__xx__user__", "no-prefix"])
def test_parse_bad_inputs(bad):
    with pytest.raises(ValueError):
        parse(bad)


def test_build_rejects_separator_in_args():
    with pytest.raises(ValueError):
        build("a__b", "ou_x")
    with pytest.raises(ValueError):
        build("file", "ou__z")


def test_result_matches_deerflow_charset():
    """deerflow only allows alnum, underscore, hyphen."""
    import re
    tid = build("doc_fake_file_token_0002", "ou_fake_user_0002")
    assert re.match(r"^[A-Za-z0-9_-]+$", tid), tid
