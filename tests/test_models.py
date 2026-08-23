"""Tests for subscriptions persistence in core.models."""

import asyncio

import core.models
from core.models import RepoEntry, Subscriptions, atomic_update, load_subscriptions, save_subscriptions


def _sample_subs() -> Subscriptions:
    repo: RepoEntry = {
        "url": "https://github.com/o/r",
        "name": "o/r",
        "last_release_id": 10,
        "cached_releases": [],
        "last_checked": "2024-01-01T00:00:00Z",
        "notify_prerelease": False,
    }
    return {"users": {1: [repo]}}


def test_load_missing_file_returns_default(tmp_subs):
    result = load_subscriptions()
    assert result == {"users": {}}


def test_load_invalid_json_returns_default(tmp_path, monkeypatch):
    path = tmp_path / "subscriptions.json"
    path.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(core.models, "SUBSCRIPTIONS_FILE", str(path))
    assert load_subscriptions() == {"users": {}}


def test_load_string_uid_keys_converted_to_int(tmp_path, monkeypatch):
    path = tmp_path / "subscriptions.json"
    path.write_text('{"users": {"42": [{"name": "o/r"}]}}', encoding="utf-8")
    monkeypatch.setattr(core.models, "SUBSCRIPTIONS_FILE", str(path))
    subs = load_subscriptions()
    assert 42 in subs["users"]
    assert subs["users"][42][0]["name"] == "o/r"


def test_load_users_not_a_dict_returns_default(tmp_path, monkeypatch):
    path = tmp_path / "subscriptions.json"
    path.write_text('{"users": "notadict"}', encoding="utf-8")
    monkeypatch.setattr(core.models, "SUBSCRIPTIONS_FILE", str(path))
    assert load_subscriptions() == {"users": {}}


def test_save_and_load_round_trip(tmp_subs):
    save_subscriptions(_sample_subs())
    loaded = load_subscriptions()
    assert loaded == _sample_subs()


def test_atomic_update_applies_and_persists(tmp_subs):
    save_subscriptions({"users": {}})

    async def run():
        await atomic_update(lambda subs: subs["users"].update(_sample_subs()["users"]))

    asyncio.run(run())
    assert load_subscriptions() == _sample_subs()


def test_atomic_update_concurrent_no_clobber(tmp_subs):
    save_subscriptions({"users": {}})

    async def run():
        def add_a(subs):
            subs.setdefault("users", {}).setdefault(1, []).append(
                {
                    "url": "https://github.com/a/b",
                    "name": "a/b",
                    "last_release_id": None,
                    "cached_releases": [],
                    "last_checked": "",
                    "notify_prerelease": False,
                }
            )

        def add_b(subs):
            subs.setdefault("users", {}).setdefault(2, []).append(
                {
                    "url": "https://github.com/c/d",
                    "name": "c/d",
                    "last_release_id": None,
                    "cached_releases": [],
                    "last_checked": "",
                    "notify_prerelease": False,
                }
            )

        await asyncio.gather(atomic_update(add_a), atomic_update(add_b))

    asyncio.run(run())
    subs = load_subscriptions()
    assert "a/b" in [r["name"] for r in subs["users"][1]]
    assert "c/d" in [r["name"] for r in subs["users"][2]]
