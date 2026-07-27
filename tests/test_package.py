import subprocess
import sys

from typer.testing import CliRunner

from vla_wam_daily import __version__
from vla_wam_daily.cli import app


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help_exits_successfully() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0


def test_module_help_exits_successfully() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "vla_wam_daily", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "Usage:" in result.stdout
