from click.testing import CliRunner

from moxtral import __version__
from moxtral.cli.main import cli


def test_root_help_lists_available_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "config" in result.output
    assert "ocr" in result.output
    assert "transcribe" in result.output


def test_root_version_reports_package_version() -> None:
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_h_is_a_help_alias_for_root_and_subcommands() -> None:
    root = CliRunner().invoke(cli, ["-h"])
    subcommand = CliRunner().invoke(cli, ["ocr", "-h"])

    assert root.exit_code == 0
    assert "Usage:" in root.output
    assert "ocr" in root.output
    assert subcommand.exit_code == 0
    assert "--table-format" in subcommand.output
