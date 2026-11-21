"""The persisted user id (huri_core.core.user_config): where it lives, and
that an existing id is reused instead of replaced."""

import stat
import uuid

import pytest

from huri_core.core import user_config


@pytest.mark.parametrize(
    ("system", "env", "relative"),
    [
        ("Linux", {"XDG_CONFIG_HOME": "xdg"}, "xdg/huri"),
        ("Linux", {}, ".config/huri"),
        ("FreeBSD", {}, ".config/huri"),
        ("Windows", {"APPDATA": "roaming"}, "roaming/huri"),
        ("Windows", {}, "AppData/Roaming/huri"),
        ("Darwin", {}, "Library/Application Support/huri"),
    ],
)
def test_config_dir_follows_the_platform(tmp_path, monkeypatch, system, env, relative):
    monkeypatch.setattr(user_config.platform, "system", lambda: system)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(tmp_path / value))

    config_dir = user_config.get_config_dir()

    assert config_dir == tmp_path / relative
    assert config_dir.is_dir()
    assert user_config.get_config_dir() == config_dir


def test_missing_or_blank_id_reads_as_absent(tmp_path):
    blank = tmp_path / "_user_id"
    blank.write_text(" \n\t")
    assert user_config.load_user_id(str(tmp_path / "missing")) is None
    assert user_config.load_user_id(str(blank)) is None


def test_loaded_id_is_stripped(tmp_path):
    path = tmp_path / "_user_id"
    path.write_text("  user-1\n")
    assert user_config.load_user_id(str(path)) == "user-1"


def test_saved_id_is_private_on_unix(tmp_path, monkeypatch):
    monkeypatch.setattr(user_config.platform, "system", lambda: "Linux")
    path = tmp_path / "_user_id"
    user_config.save_user_id("user-1", str(path))
    assert path.read_text() == "user-1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_windows_save_keeps_the_existing_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(user_config.platform, "system", lambda: "Windows")
    path = tmp_path / "_user_id"
    path.write_text("seed")
    before = stat.S_IMODE(path.stat().st_mode)
    user_config.save_user_id("user-1", str(path))
    assert path.read_text() == "user-1"
    assert stat.S_IMODE(path.stat().st_mode) == before


def test_a_new_user_id_is_saved_and_then_reused(tmp_path):
    path = tmp_path / "_user_id"
    created = user_config.get_or_create_and_save_user_id(str(path))
    assert str(uuid.UUID(created)) == created
    assert path.read_text() == created
    assert user_config.get_or_create_and_save_user_id(str(path)) == created


def test_a_blank_id_file_is_replaced(tmp_path):
    path = tmp_path / "_user_id"
    path.write_text("\n")
    created = user_config.get_or_create_and_save_user_id(str(path))
    assert created and path.read_text() == created


def test_default_path_is_the_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(user_config.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    stored = tmp_path / "huri" / "_user_id"

    assert user_config.load_user_id() is None
    created = user_config.get_or_create_and_save_user_id()
    assert stored.read_text() == created
    assert user_config.load_user_id() == created

    user_config.save_user_id("user-1")
    assert stored.read_text() == "user-1"
    assert user_config.load_user_id() == "user-1"
    assert user_config.get_or_create_and_save_user_id() == "user-1"
