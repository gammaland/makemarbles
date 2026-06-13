import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import main as cli_main
from core.storage import Storage


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "cli.db"
    monkeypatch.setattr(cli_main, "_storage", lambda: Storage(db_path=db))
    return db


def test_log_with_arg_prints_confirmation(runner: CliRunner):
    result = runner.invoke(cli_main.app, ["log", "hello world", "--tag", "test"])
    assert result.exit_code == 0
    assert "hello world" in result.stdout


def test_log_json_emits_valid_note(runner: CliRunner):
    result = runner.invoke(cli_main.app, ["log", "json mode note", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout.strip())
    assert payload["content"] == "json mode note"
    assert payload["tag"] is None
    assert "id" in payload and "created_at" in payload


def test_log_reads_stdin_when_content_omitted(runner: CliRunner):
    result = runner.invoke(cli_main.app, ["log", "--json"], input="piped content\n")
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip())["content"] == "piped content"


def test_log_dash_reads_stdin(runner: CliRunner):
    result = runner.invoke(cli_main.app, ["log", "-", "--json"], input="dash content\n")
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip())["content"] == "dash content"


def test_log_empty_stdin_errors(runner: CliRunner):
    result = runner.invoke(cli_main.app, ["log"], input="   \n")
    assert result.exit_code != 0


def test_recent_json_returns_array(runner: CliRunner):
    runner.invoke(cli_main.app, ["log", "first"])
    runner.invoke(cli_main.app, ["log", "second"])
    result = runner.invoke(cli_main.app, ["recent", "--json"])
    assert result.exit_code == 0
    items = json.loads(result.stdout.strip())
    assert len(items) == 2
    assert items[0]["content"] == "second"


def test_search_json_filters_by_query(runner: CliRunner):
    runner.invoke(cli_main.app, ["log", "marathon plan"])
    runner.invoke(cli_main.app, ["log", "rust async"])
    result = runner.invoke(cli_main.app, ["search", "marathon", "--json"])
    assert result.exit_code == 0
    items = json.loads(result.stdout.strip())
    assert len(items) == 1
    assert items[0]["content"] == "marathon plan"


def test_count_json(runner: CliRunner):
    runner.invoke(cli_main.app, ["log", "a"])
    runner.invoke(cli_main.app, ["log", "b"])
    result = runner.invoke(cli_main.app, ["count", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout.strip()) == {"count": 2}
