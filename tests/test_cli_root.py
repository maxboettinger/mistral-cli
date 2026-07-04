from click.testing import CliRunner

from mistral_cli import __version__
from mistral_cli.cli.main import cli


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
