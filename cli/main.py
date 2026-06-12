from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from core.models import Note
from core.storage import Storage

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


@app.command()
def log(
    content: Annotated[str, typer.Argument(help="The note content.")],
    tag: Annotated[str | None, typer.Option("--tag", "-t", help="Optional tag.")] = None,
) -> None:
    """Capture a new note."""
    note = Note(content=content, tag=tag)
    _storage().add(note)
    console.print(f"[green]✓[/green] {note.id[:8]}  {content}")


@app.command()
def recent(
    days: Annotated[int, typer.Option("--days", "-d", help="Look back N days.")] = 7,
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows to show.")] = 50,
) -> None:
    """Show recent notes (default: last 7 days)."""
    notes = _storage().recent(days=days, limit=limit)
    _render_notes(notes, f"last {days} day(s)")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="FTS5 search query.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows to show.")] = 20,
) -> None:
    """Full-text keyword search across notes."""
    notes = _storage().search(query, limit=limit)
    _render_notes(notes, f"matches for {query!r}")


@app.command()
def count() -> None:
    """Print total note count."""
    console.print(f"[bold]{_storage().count()}[/bold] notes")


if __name__ == "__main__":
    app()
