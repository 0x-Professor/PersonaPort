from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from personaport import __version__
from personaport.browser.manager import BrowserManager
from personaport.browser.platforms import get_platform_adapter
from personaport.config import AppConfig, ConfigManager
from personaport.db import ConversationCache
from personaport.models import Conversation, PersonaProfile, Platform, ProcessedHistory
from personaport.processor import ConversationProcessor
from personaport.transfer import TransferService
from personaport.utils.console import (
    MANDATORY_WARNING,
    confirm_unsafe_mode,
    get_console,
    print_runtime_warning,
)

APP_HELP = (
    f"WARNING: {MANDATORY_WARNING}\n\n"
    "PersonaPort is a local-first CLI for moving conversation/persona context across AI platforms."
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=APP_HELP,
    rich_markup_mode="rich",
)


def _get_state(ctx: typer.Context) -> dict[str, Any]:
    if not isinstance(ctx.obj, dict):
        raise typer.BadParameter("App state is not initialized.")
    return ctx.obj


def _latest_conversation(conversations: list[Conversation]) -> Conversation:
    return sorted(conversations, key=lambda c: c.updated_at, reverse=True)[0]


def _render_output_files(console: Any, output_files: dict[str, str]) -> None:
    table = Table(title="Generated Output Files")
    table.add_column("Type", style="cyan")
    table.add_column("Path", style="green")
    for key, value in output_files.items():
        table.add_row(key, value)
    console.print(table)


@app.callback()
def main(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", help="Optional config.yaml path override."
    ),
) -> None:
    console = get_console()
    print_runtime_warning(console)

    config_manager = ConfigManager(config_path=config)
    app_config = config_manager.load()
    cache = ConversationCache(app_config.db_path)
    processor = ConversationProcessor(
        console=console,
        default_model=app_config.default_summary_model,
        max_chunk_chars=app_config.max_chunk_chars,
        max_context_chars=app_config.max_context_chars,
        litellm_timeout_seconds=app_config.litellm_timeout_seconds,
    )
    transfer = TransferService(console=console, processed_dir=app_config.processed_dir)

    ctx.obj = {
        "console": console,
        "config_manager": config_manager,
        "config": app_config,
        "cache": cache,
        "processor": processor,
        "transfer": transfer,
    }


@app.command("version")
def version() -> None:
    """Print PersonaPort version."""
    typer.echo(f"personaport {__version__}")


@app.command()
def login(
    ctx: typer.Context,
    platform: Platform = typer.Option(..., "--platform", case_sensitive=False),
    headless: bool = typer.Option(
        False, "--headless/--no-headless", help="Run browser headless (default: visible)."
    ),
) -> None:
    """Open a browser for manual login and save Playwright storage-state."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]
    app_config: AppConfig = state["config"]

    adapter = get_platform_adapter(platform)
    state_path = config_manager.session_state_path(app_config, platform)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with BrowserManager(state_path=state_path, headless=headless).open() as runtime:
        page = runtime.context.new_page()
        adapter.login(page, console)
        typer.prompt(
            "Press Enter once login is complete and the chat UI is visible",
            default="",
            show_default=False,
        )
        runtime.context.storage_state(path=str(state_path))

    console.print(f"[green]Saved session state:[/green] {state_path}")


@app.command()
def export(
    ctx: typer.Context,
    from_platform: Platform = typer.Option(..., "--from", case_sensitive=False),
    to_platform: Platform | None = typer.Option(None, "--to", case_sensitive=False),
    all_history: bool = typer.Option(
        False,
        "--all",
        help="Merge all conversations into one migration payload for target transfer.",
    ),
    safe_mode: bool = typer.Option(
        True,
        "--safe-mode/--unsafe-mode",
        help="Safe mode only uses official export flows and avoids scraping.",
    ),
    no_scrape: bool = typer.Option(
        False,
        "--no-scrape",
        help="Disable scraping fallback even in unsafe mode.",
    ),
    model: str | None = typer.Option(None, "--model", help="Override LiteLLM model."),
    headless: bool = typer.Option(False, "--headless/--no-headless"),
    auto_inject: bool = typer.Option(
        True,
        "--auto-inject/--no-auto-inject",
        help="Automatically inject generated migration prompt into target platform.",
    ),
) -> None:
    """Export source platform history, process it, and optionally migrate to target."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]
    app_config: AppConfig = state["config"]
    cache: ConversationCache = state["cache"]
    processor: ConversationProcessor = state["processor"]
    transfer: TransferService = state["transfer"]

    if not safe_mode and not no_scrape:
        if not confirm_unsafe_mode(console):
            raise typer.Abort()

    source_state_path = config_manager.session_state_path(app_config, from_platform)
    if not source_state_path.exists():
        raise typer.BadParameter(
            f"No saved session for {from_platform.value}. Run `personaport login --platform {from_platform.value}` first."
        )

    adapter = get_platform_adapter(from_platform)
    export_result = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(description=f"Exporting from {from_platform.value}...", total=None)
        with BrowserManager(state_path=source_state_path, headless=headless).open() as runtime:
            page = runtime.context.new_page()
            export_result = adapter.export_data(
                page,
                app_config.exports_dir,
                safe_mode=safe_mode,
                no_scrape=no_scrape,
                console=console,
            )

    if export_result is None:
        raise typer.BadParameter("Export failed before adapter execution.")

    console.print(export_result.message)

    conversations: list[Conversation] = []
    if export_result.export_path and export_result.export_path.exists():
        conversations = processor.load_conversations(
            export_result.export_path, source_platform=from_platform
        )
    elif export_result.conversations:
        conversations = export_result.conversations

    if not conversations:
        console.print(
            "[yellow]No conversations loaded into cache.[/yellow] "
            "If export was manual, run `personaport process --file <export.zip>` after downloading."
        )
        raise typer.Exit(code=0)

    for conversation in conversations:
        cache.save_conversation(conversation)

    selected_conversation = (
        processor.combine_conversations(conversations, from_platform)
        if all_history
        else _latest_conversation(conversations)
    )

    persona = processor.extract_persona(conversations, model=model, use_llm=True)
    cache.save_persona(persona)

    target = to_platform or Platform.GENERIC
    condensed = processor.condense_history(selected_conversation, model=model, use_llm=True)
    artifact = transfer.build_artifact(
        selected_conversation,
        persona,
        condensed_history=condensed,
        target_platform=target,
    )
    output_files = transfer.write_artifact(
        artifact, target_platform=target, prefix=f"migrate_to_{target.value}"
    )
    _render_output_files(console, output_files)

    processed = ProcessedHistory(
        conversation_id=selected_conversation.id,
        target_platform=target,
        condensed_history=condensed,
        full_history=selected_conversation.to_history_text(),
        persona_prompt=persona.system_prompt,
        output_files=output_files,
    )
    cache.save_processed_history(processed)

    if to_platform and auto_inject:
        target_state_path = config_manager.session_state_path(app_config, to_platform)
        if not target_state_path.exists():
            console.print(
                f"[yellow]Target session missing for {to_platform.value}. "
                f"Run `personaport login --platform {to_platform.value}` before auto-injection.[/yellow]"
            )
            raise typer.Exit(code=0)

        transfer.inject_to_target(
            target_platform=to_platform,
            state_path=target_state_path,
            prompt_text=artifact.prompt_markdown,
            knowledge_file=Path(output_files["knowledge_text"]),
            headless=headless,
        )


@app.command()
def process(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    from_platform: Platform | None = typer.Option(None, "--from", case_sensitive=False),
    target: Platform = typer.Option(Platform.GENERIC, "--target", case_sensitive=False),
    persona: str | None = typer.Option(None, "--persona", help="Manual persona override."),
    model: str | None = typer.Option(None, "--model"),
    summarize: bool = typer.Option(
        True, "--summarize/--no-summarize", help="Condense history for context windows."
    ),
    conversation_id: str | None = typer.Option(
        None, "--conversation-id", help="Optional specific conversation ID from export."
    ),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    """Process an exported file into migration artifacts."""
    state = _get_state(ctx)
    console = state["console"]
    cache: ConversationCache = state["cache"]
    processor: ConversationProcessor = state["processor"]
    transfer: TransferService = state["transfer"]

    conversations = processor.load_conversations(file, source_platform=from_platform)
    if not conversations:
        raise typer.BadParameter("No conversations parsed from input file.")

    for conversation in conversations:
        cache.save_conversation(conversation)

    if conversation_id:
        selected = next((c for c in conversations if c.id == conversation_id), None)
        if selected is None:
            raise typer.BadParameter(f"Conversation ID not found in file: {conversation_id}")
    else:
        selected = _latest_conversation(conversations)

    persona_profile = processor.extract_persona(
        conversations,
        persona_override=persona,
        model=model,
        use_llm=True,
    )
    cache.save_persona(persona_profile)

    condensed = (
        processor.condense_history(selected, model=model, use_llm=True)
        if summarize
        else selected.to_history_text()
    )
    artifact = transfer.build_artifact(
        selected,
        persona_profile,
        condensed_history=condensed,
        target_platform=target,
    )
    output_files = transfer.write_artifact(
        artifact,
        target_platform=target,
        output_dir=output_dir,
        prefix=f"migrate_to_{target.value}",
    )

    processed = ProcessedHistory(
        conversation_id=selected.id,
        target_platform=target,
        condensed_history=condensed,
        full_history=selected.to_history_text(),
        persona_prompt=persona_profile.system_prompt,
        output_files=output_files,
    )
    cache.save_processed_history(processed)

    _render_output_files(console, output_files)


@app.command()
def migrate(
    ctx: typer.Context,
    input_value: str = typer.Option(
        "session",
        "--input",
        help="`session`, conversation ID in cache, or export file path.",
    ),
    target: Platform = typer.Option(..., "--target", case_sensitive=False),
    source: Platform | None = typer.Option(None, "--source", case_sensitive=False),
    persona: str | None = typer.Option(None, "--persona"),
    model: str | None = typer.Option(None, "--model"),
    auto_inject: bool = typer.Option(
        True,
        "--auto-inject/--no-auto-inject",
        help="Automatically open target platform and inject generated migration prompt.",
    ),
    headless: bool = typer.Option(False, "--headless/--no-headless"),
) -> None:
    """Generate migration output from cache/file and optionally inject into target."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]
    app_config: AppConfig = state["config"]
    cache: ConversationCache = state["cache"]
    processor: ConversationProcessor = state["processor"]
    transfer: TransferService = state["transfer"]

    selected_conversation: Conversation | None = None
    source_conversations: list[Conversation] = []

    candidate_path = Path(input_value).expanduser()
    if input_value == "session":
        selected_conversation = cache.get_latest_conversation(
            source.value if source else None
        )
        if selected_conversation:
            source_conversations = [selected_conversation]
    elif candidate_path.exists():
        source_conversations = processor.load_conversations(
            candidate_path, source_platform=source
        )
        if source_conversations:
            selected_conversation = _latest_conversation(source_conversations)
    else:
        selected_conversation = cache.get_conversation(input_value)
        if selected_conversation:
            source_conversations = [selected_conversation]

    if selected_conversation is None:
        raise typer.BadParameter(
            "Could not resolve input. Use `--input session`, a known conversation ID, or an existing file path."
        )

    persona_profile = processor.extract_persona(
        source_conversations,
        persona_override=persona,
        model=model,
        use_llm=True,
    )
    cache.save_persona(persona_profile)

    condensed = processor.condense_history(selected_conversation, model=model, use_llm=True)
    artifact = transfer.build_artifact(
        selected_conversation,
        persona_profile,
        condensed_history=condensed,
        target_platform=target,
    )
    output_files = transfer.write_artifact(
        artifact, target_platform=target, prefix=f"migrate_to_{target.value}"
    )
    _render_output_files(console, output_files)

    processed = ProcessedHistory(
        conversation_id=selected_conversation.id,
        target_platform=target,
        condensed_history=condensed,
        full_history=selected_conversation.to_history_text(),
        persona_prompt=persona_profile.system_prompt,
        output_files=output_files,
    )
    cache.save_processed_history(processed)

    if auto_inject:
        target_state_path = config_manager.session_state_path(app_config, target)
        if not target_state_path.exists():
            raise typer.BadParameter(
                f"No saved session for {target.value}. Run `personaport login --platform {target.value}` first."
            )
        transfer.inject_to_target(
            target_platform=target,
            state_path=target_state_path,
            prompt_text=artifact.prompt_markdown,
            knowledge_file=Path(output_files["knowledge_text"]),
            headless=headless,
        )


if __name__ == "__main__":
    app()
