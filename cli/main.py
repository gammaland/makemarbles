import json
import shlex
import sys
from typing import Annotated

import click
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.table import Table

from core.models import Note
from core.storage import Storage

REPL_COMMANDS = ["log", "recent", "search", "count", "help", "quit", "exit"]
REPL_HISTORY = "~/.marbles/.shell_history"

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="MakeMarbles — local-first AI journal (CLI + MCP).",
)
console = Console()


def _storage() -> Storage:
    return Storage()


def _render_notes(notes: list[Note], title: str) -> None:
    if not notes:
        console.print(f"[dim]No notes for {title}.[/dim]")
        return
    table = Table(title=title, show_lines=False, expand=True)
    table.add_column("when", style="dim", no_wrap=True)
    table.add_column("tag", style="cyan", no_wrap=True)
    table.add_column("content")
    for note in notes:
        when = note.created_at.astimezone().strftime("%m-%d %H:%M")
        table.add_row(when, note.tag or "", note.content)
    console.print(table)


def _emit_json(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _resolve_content(content: str | None) -> str:
    """Resolve note content from arg or stdin.

    Rules: explicit text wins; '-' or omitted arg reads stdin. Refuses to
    block on an interactive tty when no content was supplied."""
    if content and content != "-":
        if not content.strip():
            raise typer.BadParameter("content is whitespace-only.")
        return content
    if sys.stdin.isatty():
        raise typer.BadParameter(
            "no content provided (pass a string, pipe stdin, or use '-')."
        )
    data = sys.stdin.read().strip()
    if not data:
        raise typer.BadParameter("stdin was empty.")
    return data


EDITOR_TEMPLATE = (
    "\n"
    "# Write your note above. Lines starting with '#' are ignored.\n"
    "# Save and quit to capture. Quit without saving to abort.\n"
)


def _content_from_editor() -> str:
    raw = click.edit(EDITOR_TEMPLATE, extension=".md")
    if raw is None:
        raise typer.BadParameter("editor aborted; nothing captured.")
    body = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    ).strip()
    if not body:
        raise typer.BadParameter("editor produced empty content.")
    return body


@app.command()
def log(
    content: Annotated[
        str | None,
        typer.Argument(help="Note content. Omit or use '-' to read from stdin."),
    ] = None,
    tag: Annotated[str | None, typer.Option("--tag", "-t", help="Optional tag.")] = None,
    edit: Annotated[
        bool,
        typer.Option("--editor", "-e", help="Compose multi-line note in $EDITOR."),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the created note as JSON.")
    ] = False,
) -> None:
    """Capture a new note."""
    text = _content_from_editor() if edit else _resolve_content(content)
    note = Note(content=text, tag=tag)
    _storage().add(note)
    if as_json:
        _emit_json(note.model_dump(mode="json"))
    else:
        first = text.splitlines()[0] if text else ""
        suffix = " …" if "\n" in text else ""
        console.print(f"[green]✓[/green] {note.id[:8]}  {first}{suffix}")


@app.command()
def recent(
    days: Annotated[int, typer.Option("--days", "-d", help="Look back N days.")] = 7,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows to show.")] = 50,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit results as a JSON array.")
    ] = False,
) -> None:
    """Show recent notes (default: last 7 days)."""
    notes = _storage().recent(days=days, limit=limit)
    if as_json:
        _emit_json([n.model_dump(mode="json") for n in notes])
        return
    _render_notes(notes, f"last {days} day(s)")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="FTS5 search query.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows to show.")] = 20,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit results as a JSON array.")
    ] = False,
) -> None:
    """Full-text keyword search across notes."""
    notes = _storage().search(query, limit=limit)
    if as_json:
        _emit_json([n.model_dump(mode="json") for n in notes])
        return
    _render_notes(notes, f"matches for {query!r}")


@app.command()
def count(
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit count as JSON object.")
    ] = False,
) -> None:
    """Print total note count."""
    n = _storage().count()
    if as_json:
        _emit_json({"count": n})
        return
    console.print(f"[bold]{n}[/bold] notes")


def _repl_help() -> None:
    console.print(
        "[bold]Commands inside shell:[/bold]\n"
        '  [cyan]log[/cyan] "content" [-t tag]   capture a note\n'
        "  [cyan]log -e[/cyan]                   compose multi-line in $EDITOR\n"
        "  [cyan]recent[/cyan] [--days N] [--limit N]\n"
        "  [cyan]search[/cyan] <query> [--limit N]\n"
        "  [cyan]count[/cyan]\n"
        "  [cyan]help[/cyan]                    show this\n"
        "  [cyan]quit[/cyan] / [cyan]exit[/cyan] / Ctrl-D    leave"
    )


@app.command()
def shell() -> None:
    """Drop into an interactive marbles shell (REPL)."""
    from pathlib import Path

    history_path = Path(REPL_HISTORY).expanduser()
    history_path.parent.mkdir(parents=True, exist_ok=True)

    session: PromptSession[str] = PromptSession(
        history=FileHistory(str(history_path)),
        completer=WordCompleter(REPL_COMMANDS, ignore_case=True),
    )

    console.print(
        "[dim]marbles shell — [bold]help[/bold] for commands, "
        "[bold]quit[/bold] or Ctrl-D to exit.[/dim]"
    )

    while True:
        try:
            line = session.prompt("marbles> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not line:
            continue
        if line in {"quit", "exit"}:
            return
        if line == "help":
            _repl_help()
            continue
        try:
            argv = shlex.split(line)
        except ValueError as e:
            console.print(f"[red]parse error:[/red] {e}")
            continue
        try:
            app(argv, standalone_mode=False)
        except SystemExit:
            pass
        except typer.Exit:
            pass
        except Exception as e:
            console.print(f"[red]error:[/red] {e}")


if __name__ == "__main__":
    app()
