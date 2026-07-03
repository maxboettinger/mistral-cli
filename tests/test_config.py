import os
import stat
from pathlib import Path

import pytest

from mistral_cli.config import AppConfig, ConfigStore
from mistral_cli.errors import ConfigError


def test_app_config_is_immutable_and_uses_defaults() -> None:
    config = AppConfig()

    assert config == AppConfig(config_version=1, api_key=None)
    with pytest.raises(AttributeError):
        config.api_key = "changed"  # type: ignore[misc]


def test_load_absent_file_returns_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.toml")

    assert store.load() == AppConfig()


def test_environment_api_key_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\napi_key = "file-key"\n', encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "environment-key")

    assert ConfigStore(path).resolve_api_key() == "environment-key"


def test_explicit_environment_mapping_is_used(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\napi_key = "file-key"\n', encoding="utf-8")

    store = ConfigStore(path, environ={"MISTRAL_API_KEY": "explicit-key"})

    assert store.resolve_api_key() == "explicit-key"


@pytest.mark.parametrize("environment_key", ["", "   "])
def test_blank_environment_api_key_falls_back_to_file(
    tmp_path: Path, environment_key: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\napi_key = "file-key"\n', encoding="utf-8")

    store = ConfigStore(path, environ={"MISTRAL_API_KEY": environment_key})

    assert store.resolve_api_key() == "file-key"


def test_missing_credentials_error_explains_both_configuration_options(
    tmp_path: Path,
) -> None:
    store = ConfigStore(tmp_path / "config.toml", environ={})

    with pytest.raises(ConfigError) as error:
        store.resolve_api_key()

    message = str(error.value)
    assert "MISTRAL_API_KEY" in message
    assert "mistral config set api-key" in message


def test_set_creates_private_nested_configuration(tmp_path: Path) -> None:
    path = tmp_path / "one" / "two" / "config.toml"

    ConfigStore(path).set("api-key", "secret")

    assert ConfigStore(path).load().api_key == "secret"
    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_set_preserves_existing_parent_mode_and_repairs_file_mode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "config.toml"
    path.parent.mkdir(mode=0o755)
    path.write_text("config_version = 1\n", encoding="utf-8")
    path.chmod(0o644)

    ConfigStore(path).set("api-key", "secret")

    if os.name == "posix":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o755
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_set_relative_path_preserves_existing_working_directory_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_directory = tmp_path / "working"
    custom_directory.mkdir(mode=0o751)
    monkeypatch.chdir(custom_directory)

    ConfigStore(Path("config.toml")).set("api-key", "secret")

    if os.name == "posix":
        assert stat.S_IMODE(custom_directory.stat().st_mode) == 0o751
        assert stat.S_IMODE((custom_directory / "config.toml").stat().st_mode) == 0o600


def test_set_works_when_fchmod_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    monkeypatch.delattr(os, "fchmod", raising=False)

    ConfigStore(path).set("api-key", "secret")

    assert ConfigStore(path).load().api_key == "secret"


def test_set_atomically_replaces_file_without_temp_leftovers(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("config_version = 1\n", encoding="utf-8")
    original_inode = path.stat().st_ino

    ConfigStore(path).set("api-key", "secret")

    assert path.stat().st_ino != original_inode
    assert list(tmp_path.iterdir()) == [path]


def test_set_preserves_supported_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\napi_key = "old"\n', encoding="utf-8")

    ConfigStore(path).set("api-key", "new")

    assert ConfigStore(path).load() == AppConfig(config_version=1, api_key="new")


@pytest.mark.parametrize("value", ["", " \t "])
def test_set_rejects_blank_values(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="must not be blank"):
        ConfigStore(tmp_path / "config.toml").set("api-key", value)


def test_set_rejects_unknown_names(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown configuration name"):
        ConfigStore(tmp_path / "config.toml").set("other", "value")


def test_set_wraps_surrogate_encoding_error_without_writing_secret(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    secret = "should-never-echo\ud800"

    with pytest.raises(ConfigError) as error:
        ConfigStore(path).set("api-key", secret)

    assert secret not in str(error.value)
    assert not path.exists()


def test_load_rejects_malformed_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not = [valid", encoding="utf-8")

    with pytest.raises(ConfigError, match="Could not parse"):
        ConfigStore(path).load()


def test_load_wraps_invalid_utf8_as_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b'api_key = "\xff"\n')

    with pytest.raises(ConfigError, match="Could not parse"):
        ConfigStore(path).load()


def test_set_does_not_replace_malformed_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    malformed = "not = [valid"
    path.write_text(malformed, encoding="utf-8")

    with pytest.raises(ConfigError, match="Could not parse"):
        ConfigStore(path).set("api-key", "secret")

    assert path.read_text(encoding="utf-8") == malformed
    assert list(tmp_path.iterdir()) == [path]


def test_load_rejects_unknown_top_level_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("config_version = 1\nunknown = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="Unknown configuration key"):
        ConfigStore(path).load()


@pytest.mark.parametrize("version", ["2", '"1"', "true"])
def test_load_rejects_invalid_config_version(tmp_path: Path, version: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"config_version = {version}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="config_version"):
        ConfigStore(path).load()


@pytest.mark.parametrize("api_key", ["42", "true", '""', '"   "'])
def test_load_rejects_invalid_configured_api_keys(tmp_path: Path, api_key: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        f"config_version = 1\napi_key = {api_key}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="API key"):
        ConfigStore(path).load()


def test_unset_removes_api_key_and_retains_version(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('config_version = 1\napi_key = "secret"\n', encoding="utf-8")
    store = ConfigStore(path)

    assert store.unset("api-key") is True
    assert store.load() == AppConfig(config_version=1)
    assert store.unset("api-key") is False


def test_unset_rejects_unknown_names(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Unknown configuration name"):
        ConfigStore(tmp_path / "config.toml").unset("other")


def test_redacted_never_returns_configured_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'config_version = 1\napi_key = "extremely-secret"\n',
        encoding="utf-8",
    )

    redacted = ConfigStore(path).redacted()

    assert redacted == {"config_version": 1, "api_key": "********"}
    assert "extremely-secret" not in repr(redacted)


def test_redacted_omits_unconfigured_api_key(tmp_path: Path) -> None:
    assert ConfigStore(tmp_path / "config.toml").redacted() == {"config_version": 1}
