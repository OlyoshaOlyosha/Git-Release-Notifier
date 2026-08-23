"""Pure-function tests for URL/date helpers in ui.handlers."""

from ui.handlers import _format_last_checked, _parse_owner_repo


def test_parse_owner_repo_happy():
    assert _parse_owner_repo("https://github.com/psf/requests") == ("psf", "requests")


def test_parse_owner_repo_trailing_slash():
    # Code strips trailing slashes via .strip("/"); this is tolerated, not rejected.
    assert _parse_owner_repo("https://github.com/psf/requests/") == ("psf", "requests")


def test_parse_owner_repo_bad_prefix():
    assert _parse_owner_repo("http://github.com/psf/requests") is None


def test_parse_owner_repo_wrong_domain():
    assert _parse_owner_repo("https://gitlab.com/psf/requests") is None


def test_parse_owner_repo_too_few_parts():
    assert _parse_owner_repo("https://github.com/psf") is None


def test_parse_owner_repo_base_only():
    # Just the prefix (with trailing slash) yields no owner/repo -> None.
    assert _parse_owner_repo("https://github.com/") is None


def test_format_last_checked_empty():
    assert _format_last_checked("") == "никогда"


def test_format_last_checked_valid():
    assert _format_last_checked("2024-01-15T10:30:00Z") == "2024.01.15 10:30"


def test_format_last_checked_garbage():
    # Malformed ISO is returned unchanged (ValueError branch).
    assert _format_last_checked("not-a-date") == "not-a-date"
