import json
import shlex
import sys
from pathlib import Path
from typing import Annotated

import click
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from core.config import load_config
from core.models import Note
from core.storage import Storage, VectorDimMismatch


# Heavy imports (onnxruntime, tokenizers, numpy) are pushed behind lazy
# helpers so `marbles log` and the other fast-path commands do not pay the
# ~half-second import cost of the embedding stack.

def _ensure_weights(model_name: str, model_dir: Path) -> dict[str, str]:
    from core.model_download import ensure_model_files
    return ensure_model_files(model_name, model_dir)


def _make_engine(model_name: str, model_dir: Path):
    from core.vector import EmbeddingEngine, get_known_model
    return EmbeddingEngine(model_dir=model_dir, config=get_known_model(model_name))


def _model_dim(model_name: str) -> int:
    from core.vector import get_known_model
    return get_known_model(model_name).dim

REPL_COMMANDS = ["log", "recent", "search", "count", "rm", "reembed", "help", "quit", "exit"]
REPL_HISTORY = "~/.marbles/.shell_history"

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="MakeMarbles: local-first AI journal (CLI + MCP).",
)
console = Console()


def _storage() -> Storage:
    return Storage()


def _render_notes(notes: list[Note], title: str) -> None:
    if not notes:
        console.print(f"[dim]No notes for {title}.[/dim]")
        return
    table = Table(title=title, show_lines=False, expand=True)
    table.add_column("id", style="magenta", no_wrap=True)
    table.add_column("when", style="dim", no_wrap=True)
    table.add_column("tag", style="cyan", no_wrap=True)
    table.add_column("content")
    for note in notes:
        when = note.created_at.astimezone().strftime("%m-%d %H:%M")
        table.add_row(note.id[:12], when, note.tag or "", note.content)
    console.print(table)


def _emit_json(payload) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _resolve_content(content: str | None) -> str:
    """Resolve note content from arg or stdin.

    Rules: explicit text wins; '-' or omitted arg reads stdin. Refuses to
    block on an interactive tty when no content was supplied."""
    if content and content != "-":
        if not content.strip():
            raise typer.BadParameter("content is whitespace-only.")
        return content
    if _is_interactive():
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
        console.print(f"[green]✓[/green] {note.id[:12]}  {first}{suffix}")


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
def rm(
    id_prefix: Annotated[str, typer.Argument(help="Note id or unique prefix.")],
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip confirmation prompt.")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the deleted id as JSON.")
    ] = False,
) -> None:
    """Delete a note by id or unique prefix."""
    storage = _storage()
    matches = storage.find_by_prefix(id_prefix)
    if not matches:
        console.print(f"[red]no note matches[/red] {id_prefix!r}")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[yellow]ambiguous[/yellow]: {len(matches)} notes match:")
        for n in matches:
            preview = n.content.splitlines()[0][:60]
            console.print(f"  {n.id[:12]}  {preview}")
        raise typer.Exit(1)
    target = matches[0]
    if not yes and _is_interactive():
        preview = target.content.splitlines()[0][:60]
        suffix = " …" if "\n" in target.content else ""
        console.print(f"[yellow]delete[/yellow] {target.id[:8]}  {preview}{suffix}")
        if not typer.confirm("confirm?", default=False):
            console.print("[dim]aborted.[/dim]")
            raise typer.Exit(1)
    storage.delete(target.id)
    if as_json:
        _emit_json({"deleted": target.id})
    else:
        console.print(f"[green]✓ deleted[/green] {target.id[:8]}")


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


@app.command()
def reembed(
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Target embedding model name. Defaults to the one in ~/.marbles/config.toml.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Report pending count without changing anything."),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit a JSON object instead of human output.")
    ] = False,
) -> None:
    """Re-vector notes under a given embedding model.

    With no flags, downloads the model if needed (HuggingFace primary,
    GitHub Releases mirror fallback), loads the engine, and embeds every
    note whose vector is missing or stamped under a different model. Safe
    to interrupt; resuming picks up where it left off.
    """
    config = load_config()
    target = model or config.embedding.model_name
    storage = _storage()
    pending = storage.pending_embed_count(target)

    if dry_run:
        if as_json:
            _emit_json({"model": target, "pending": pending, "dry_run": True})
            return
        if pending == 0:
            console.print(
                f"[green]✓ all notes are current[/green] under model [bold]{target}[/bold]"
            )
        else:
            console.print(
                f"[yellow]{pending} note(s) pending[/yellow] re-embedding under [bold]{target}[/bold]"
            )
        return

    if pending == 0:
        if as_json:
            _emit_json({"model": target, "processed": 0, "dry_run": False})
            return
        console.print(
            f"[green]✓ all notes are current[/green] under model [bold]{target}[/bold]"
        )
        return

    # Reset the vec index if the existing one has a different dimensionality
    # than the target model needs. Notes themselves are untouched; vectors are
    # a derived cache (see ADR 2026-06-13 §6.5).
    target_dim = _model_dim(target)
    existing_dim = storage.vec_table_dim()
    if existing_dim is not None and existing_dim != target_dim:
        if not as_json:
            console.print(
                f"[yellow]vec index is dim {existing_dim}, model [bold]{target}[/bold] "
                f"needs {target_dim}; resetting cache (notes are unaffected).[/yellow]"
            )
        storage.reset_vector_index()
        pending = storage.pending_embed_count(target)

    model_dir = config.embedding.models_dir / target
    if not as_json:
        console.print(
            f"[dim]Ensuring weights for [bold]{target}[/bold] under {model_dir} ...[/dim]"
        )
    _ensure_weights(target, model_dir)
    engine = _make_engine(target, model_dir)

    processed = 0
    if as_json:
        for note in storage.iter_pending_for_embed(target):
            vec = engine.embed_passage(note.content)
            try:
                storage.upsert_vector(note.id, vec, target)
            except VectorDimMismatch:
                # Should not happen given the pre-check above, but guard so a
                # mid-loop reset does not silently corrupt the run.
                storage.reset_vector_index()
                storage.upsert_vector(note.id, vec, target)
            processed += 1
        _emit_json(
            {"model": target, "processed": processed, "dry_run": False}
        )
        return

    progress_columns = [
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ]
    with Progress(*progress_columns, console=console) as progress:
        task = progress.add_task(f"embedding under {target}", total=pending)
        for note in storage.iter_pending_for_embed(target):
            vec = engine.embed_passage(note.content)
            try:
                storage.upsert_vector(note.id, vec, target)
            except VectorDimMismatch:
                storage.reset_vector_index()
                storage.upsert_vector(note.id, vec, target)
            processed += 1
            progress.update(task, advance=1)
    console.print(
        f"[green]✓ embedded[/green] {processed} note(s) under [bold]{target}[/bold]"
    )


def _repl_help() -> None:
    console.print(
        "[bold]Commands inside shell:[/bold]\n"
        '  [cyan]log[/cyan] "content" [-t tag]   capture a note\n'
        "  [cyan]log -e[/cyan]                   compose multi-line in $EDITOR\n"
        "  [cyan]recent[/cyan] [--days N] [--limit N]\n"
        "  [cyan]search[/cyan] <query> [--limit N]\n"
        "  [cyan]count[/cyan]\n"
        "  [cyan]rm[/cyan] <id-prefix> [-y]      delete a note\n"
        "  [cyan]reembed[/cyan] [-m model] [-n]   re-vector under a model (v0.2)\n"
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
        "[dim]marbles shell. [bold]help[/bold] for commands, "
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
