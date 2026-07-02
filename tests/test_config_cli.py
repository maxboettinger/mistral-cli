from pathlib import Path

from click.testing import CliRunner

from mistral_cli.cli.main import cli
from mistral_cli.config import ConfigStore


def test_config_set_prompts_for_hidden_confirmed_api_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    secret = "prompt-secret"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key"],
        input=f"{secret}\n{secret}\n",
    )

    assert result.exit_code == 0
    assert "API key" in result.output
    assert secret not in result.output
    assert ConfigStore(path).load().api_key == secret


def test_config_set_stdin_and_show_redact_api_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    secret = "stdin-secret"
    runner = CliRunner()

    set_result = runner.invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", "--stdin"],
        input=f"{secret}\n",
    )
    show_result = runner.invoke(
        cli,
        ["--config", str(path), "config", "show"],
    )

    assert set_result.exit_code == 0
    assert secret not in set_result.output
    assert show_result.exit_code == 0
    assert "config_version = 1" in show_result.output
    assert "********" in show_result.output
    assert secret not in show_result.output


def test_config_set_stdin_strips_only_trailing_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", "--stdin"],
        input="  key-with-spaces  \r\n",
    )

    assert result.exit_code == 0
    assert ConfigStore(path).load().api_key == "  key-with-spaces  "


def test_config_set_stdin_rejects_empty_input(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", "--stdin"],
        input="\n",
    )

    assert result.exit_code != 0
    assert "must not be empty" in result.output
    assert not path.exists()


def test_config_set_stdin_rejects_embedded_extra_lines(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    secret = "first-line"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", "--stdin"],
        input=f"{secret}\nsecond-line\n",
    )

    assert result.exit_code != 0
    assert "single line" in result.output
    assert secret not in result.output
    assert not path.exists()


def test_config_set_rejects_positional_secret_without_echoing_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    secret = "should-never-echo"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", secret],
    )

    assert result.exit_code != 0
    assert "prompt" in result.output
    assert "--stdin" in result.output
    assert secret not in result.output
    assert not path.exists()


def test_config_set_rejects_option_shaped_secret_without_echoing_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    secret = "--top-secret-token"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", secret],
    )

    assert result.exit_code != 0
    assert "prompt" in result.output
    assert "--stdin" in result.output
    assert secret not in result.output
    assert not path.exists()


def test_config_set_stdin_rejects_invalid_utf8_without_echoing_input(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    secret = b"should-never-echo\xff\n"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "set", "api-key", "--stdin"],
        input=secret,
    )

    assert result.exit_code != 0
    assert "Error:" in result.output
    assert "Traceback" not in result.output
    assert "should-never-echo" not in result.output
    assert not path.exists()


def test_config_set_help_has_no_positional_value_argument(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        ["--config", str(tmp_path / "config.toml"), "config", "set", "--help"],
    )

    assert result.exit_code == 0
    assert "{api-key}" in result.output
    assert "VALUE" not in result.output


def test_config_unset_reports_removed_and_already_absent(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    ConfigStore(path).set("api-key", "secret")
    runner = CliRunner()

    removed = runner.invoke(
        cli,
        ["--config", str(path), "config", "unset", "api-key"],
    )
    absent = runner.invoke(
        cli,
        ["--config", str(path), "config", "unset", "api-key"],
    )

    assert removed.exit_code == 0
    assert "removed" in removed.output.lower()
    assert absent.exit_code == 0
    assert "already absent" in absent.output.lower()
    assert "secret" not in removed.output + absent.output


def test_config_path_prints_effective_path(tmp_path: Path) -> None:
    path = tmp_path / "custom.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "path"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == str(path)


def test_config_errors_are_clean_click_errors(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not = [valid", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "show"],
    )

    assert result.exit_code != 0
    assert "Error: Could not parse" in result.output
    assert "Traceback" not in result.output


def test_config_show_invalid_utf8_is_a_clean_click_error(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b'api_key = "\xff"\n')

    result = CliRunner().invoke(
        cli,
        ["--config", str(path), "config", "show"],
    )

    assert result.exit_code != 0
    assert "Error: Could not parse" in result.output
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, UnicodeDecodeError)
