from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm
from rich.table import Table

from personaport import __version__
from personaport.browser.manager import (
    BrowserManager,
    chromium_executable_status,
    harden_session_state_permissions,
)
from personaport.browser.platforms import get_platform_adapter
from personaport.config import AppConfig, ConfigManager
from personaport.db import ConversationCache
from personaport.llm import (
    PROVIDERS,
    normalize_provider_name,
    provider_from_model,
    provider_key_env_vars,
)
from personaport.models import Conversation, Platform, ProcessedHistory
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
    "PersonaPort is a local-first CLI for moving conversation/persona context across AI platforms. "
    "Gemini support is experimental in v0.1."
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=APP_HELP,
    rich_markup_mode="rich",
)
provider_app = typer.Typer(help="Manage LLM provider API keys and defaults.")
app.add_typer(provider_app, name="provider")


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(f"personaport {__version__}")
    raise typer.Exit(code=0)


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


def _collect_attachment_files(output_files: dict[str, str]) -> list[Path]:
    chunk_keys = sorted(
        key for key in output_files.keys() if key.startswith("knowledge_chunk_")
    )
    if chunk_keys:
        return [Path(output_files[key]) for key in chunk_keys]
    if "knowledge_text" in output_files:
        return [Path(output_files["knowledge_text"])]
    return []


def _playwright_install_command() -> str:
    return f"{sys.executable} -m playwright install chromium"


def _ensure_chromium_installed(console: Any) -> None:
    installed, executable = chromium_executable_status()
    if installed:
        return
    cmd = _playwright_install_command()
    console.print(
        "[red]Playwright Chromium is not installed.[/red] "
        f"Run `[bold]{cmd}[/bold]` or `personaport install-deps`."
    )
    raise typer.Exit(code=1)


def _warn_if_experimental_platform(console: Any, platform: Platform | None) -> None:
    if platform == Platform.GEMINI:
        console.print(
            "[yellow]Gemini support is experimental in v0.1 and may require manual steps.[/yellow]"
        )


def _resolve_remote_llm_policy(
    *,
    console: Any,
    provider: str | None,
    model: str | None,
    no_remote_llm: bool,
) -> tuple[bool, Callable[[str], bool] | None]:
    model_provider = provider_from_model(model)
    explicit_remote_model = bool(model_provider and model_provider != "ollama")
    explicit_remote_provider = bool(provider and provider != "ollama")

    if no_remote_llm and (explicit_remote_provider or explicit_remote_model):
        raise typer.BadParameter(
            "--no-remote-llm cannot be used with remote --llm-provider/--model."
        )

    if no_remote_llm:
        return False, None

    if explicit_remote_provider or explicit_remote_model:
        return True, None

    state = {"asked": False, "allowed": True}

    def _prompt(candidate_model: str) -> bool:
        if state["asked"]:
            return state["allowed"]
        state["asked"] = True
        console.print(
            "[yellow]Local LLM is unavailable. Remote fallback may send conversation data to hosted providers.[/yellow]"
        )
        state["allowed"] = Confirm.ask(
            f"Allow remote LLM fallback for this run? (next model: {candidate_model})",
            default=False,
            console=console,
        )
        if not state["allowed"]:
            console.print("[yellow]Remote LLM fallback disabled for this run.[/yellow]")
        return state["allowed"]

    return True, _prompt


def _first_run_marker(config: AppConfig) -> Path:
    return config.home_dir / ".onboarding_seen"


def _resolve_llm_runtime(
    *,
    config_manager: ConfigManager,
    provider: str | None,
    api_key: str | None,
    save_api_key: bool,
) -> tuple[str | None, dict[str, str]]:
    normalized_provider = normalize_provider_name(provider, allow_custom=True)
    if provider and normalized_provider is None:
        valid = ", ".join(sorted(PROVIDERS.keys()))
        raise typer.BadParameter(
            f"Unsupported --llm-provider `{provider}`. Choose one of: {valid}, "
            "or pass a custom provider slug (lowercase letters, digits, _ or -)."
        )
    if api_key and not normalized_provider:
        raise typer.BadParameter("--api-key requires --llm-provider.")

    keys: dict[str, str] = {}
    if normalized_provider and api_key:
        keys[normalized_provider] = api_key.strip()
        if save_api_key:
            stored = config_manager.set_provider_key(normalized_provider, api_key.strip())
            if not stored:
                raise typer.BadParameter(
                    "Could not store API key in keyring. "
                    "Run without --save-api-key or configure your OS keyring."
                )
    return normalized_provider, keys


def _confirm_target_injection(console: Any, target_platform: Platform) -> bool:
    return Confirm.ask(
        f"Inject processed payload into {target_platform.value} now?",
        default=False,
        console=console,
    )


def _manual_export_markers(platform: Platform) -> set[str]:
    mapping = {
        Platform.CHATGPT: {"chatgpt", "openai", "conversation", "conversations", "export"},
        Platform.CLAUDE: {"claude", "anthropic", "conversation", "export"},
        Platform.GEMINI: {"gemini", "google", "conversation", "export"},
    }
    return mapping.get(platform, {"conversation", "export"})


def _find_latest_export_candidate(
    *,
    platform: Platform,
    search_dirs: list[Path],
    not_before: datetime,
) -> Path | None:
    markers = _manual_export_markers(platform)
    candidates: list[Path] = []
    threshold = not_before.timestamp() - 120

    for directory in search_dirs:
        if not directory.exists():
            continue
        for pattern in ("*.zip", "*.json", "*.html"):
            for path in directory.glob(pattern):
                if not path.is_file():
                    continue
                name = path.name.lower()
                if not any(marker in name for marker in markers):
                    continue
                try:
                    if path.stat().st_mtime < threshold:
                        continue
                except OSError:
                    continue
                candidates.append(path)

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _wait_for_manual_export_file(
    *,
    platform: Platform,
    exports_dir: Path,
    not_before: datetime,
    wait_minutes: int,
    console: Any,
) -> Path | None:
    search_dirs = [exports_dir, Path.home() / "Downloads", Path.cwd()]
    deadline = time.time() + (wait_minutes * 60)
    announced_wait = False

    while True:
        candidate = _find_latest_export_candidate(
            platform=platform, search_dirs=search_dirs, not_before=not_before
        )
        if candidate:
            return candidate
        if time.time() >= deadline:
            return None
        if not announced_wait:
            console.print(
                "[yellow]Waiting for manual export file in Downloads/exports directory...[/yellow]"
            )
            announced_wait = True
        time.sleep(10)


@app.callback()
def main(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", help="Optional config.yaml path override."
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print PersonaPort version and exit.",
    ),
) -> None:
    console = get_console()
    print_runtime_warning(console)

    config_manager = ConfigManager(config_path=config)
    had_config = config_manager.config_path.exists()
    app_config = config_manager.load()
    cache = ConversationCache(app_config.db_path)
    processor = ConversationProcessor(
        console=console,
        default_model=app_config.default_summary_model,
        max_chunk_chars=app_config.max_chunk_chars,
        max_context_chars=app_config.max_context_chars,
        litellm_timeout_seconds=app_config.litellm_timeout_seconds,
        provider_key_lookup=config_manager.get_provider_key,
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

    if not had_config and ctx.invoked_subcommand not in {"init", "install-deps"}:
        console.print(
            "[cyan]First run detected.[/cyan] "
            "Use `personaport init` for guided setup, then `personaport login --platform <name>`."
        )


@app.command("version")
def version() -> None:
    """Print PersonaPort version."""
    typer.echo(f"personaport {__version__}")


@app.command("install-deps")
def install_deps(
    ctx: typer.Context,
    check_only: bool = typer.Option(
        False,
        "--check-only",
        help="Only verify Chromium availability; do not install.",
    ),
) -> None:
    """Install required browser dependencies (Playwright Chromium)."""
    state = _get_state(ctx)
    console = state["console"]
    installed, executable = chromium_executable_status()
    if installed:
        console.print(f"[green]Chromium is ready:[/green] {executable}")
        raise typer.Exit(code=0)

    if check_only:
        console.print(
            "[yellow]Chromium is not installed.[/yellow] "
            f"Run `{_playwright_install_command()}` or `personaport install-deps`."
        )
        raise typer.Exit(code=1)

    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    console.print(f"[cyan]Running:[/cyan] {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise typer.Exit(code=result.returncode)

    installed, executable = chromium_executable_status()
    if not installed:
        raise typer.BadParameter(
            "Playwright install command completed, but Chromium is still unavailable."
        )
    console.print(f"[green]Chromium installed:[/green] {executable}")


@app.command()
def init(
    ctx: typer.Context,
    install_browser: bool = typer.Option(
        True,
        "--install-browser/--skip-install-browser",
        help="Install Playwright Chromium if missing.",
    ),
) -> None:
    """Guided first-run setup for config and browser runtime."""
    state = _get_state(ctx)
    console = state["console"]
    app_config: AppConfig = state["config"]

    if install_browser:
        installed, _ = chromium_executable_status()
        if not installed:
            install_deps(ctx)

    marker = _first_run_marker(app_config)
    marker.write_text(
        datetime.now(timezone.utc).isoformat(),
        encoding="utf-8",
    )

    console.print("[green]PersonaPort setup complete.[/green]")
    console.print("Next steps:")
    console.print("1) personaport login --platform chatgpt")
    console.print("2) personaport export --from chatgpt --to claude --safe-mode --no-scrape")
    console.print("3) personaport migrate --input session --target claude --no-auto-inject")


@app.command()
def logout(
    ctx: typer.Context,
    platform: Platform | None = typer.Option(
        None,
        "--platform",
        case_sensitive=False,
        help="Delete saved session for one platform.",
    ),
    all_sessions: bool = typer.Option(
        False,
        "--all",
        help="Delete all saved platform sessions.",
    ),
) -> None:
    """Delete saved browser session state files."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]
    app_config: AppConfig = state["config"]

    if not all_sessions and platform is None:
        raise typer.BadParameter("Use --platform <name> or --all.")

    targets: list[Platform]
    if all_sessions:
        targets = [Platform.CHATGPT, Platform.CLAUDE, Platform.GEMINI]
    elif platform is not None:
        targets = [platform]
    else:
        targets = []

    deleted = 0
    missing = 0
    for target in targets:
        state_path = config_manager.session_state_path(app_config, target)
        if state_path.exists():
            state_path.unlink()
            deleted += 1
            console.print(f"[green]Deleted session:[/green] {target.value}")
        else:
            missing += 1

    if deleted == 0:
        console.print("[yellow]No matching session files were found.[/yellow]")
    elif missing > 0:
        console.print(f"[yellow]Skipped {missing} missing session file(s).[/yellow]")


@provider_app.command("list")
def provider_list(
    ctx: typer.Context,
    show_key_status: bool = typer.Option(
        True,
        "--show-key-status/--hide-key-status",
        help="Show whether API key is configured for each provider.",
    ),
) -> None:
    """List configured LLM providers and default models."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]

    table = Table(title="LLM Providers")
    table.add_column("Provider", style="cyan")
    table.add_column("Default Model", style="green")
    table.add_column("API Key Env", style="magenta")
    if show_key_status:
        table.add_column("Configured", style="yellow")
    table.add_column("Notes", style="white")

    for name in sorted(PROVIDERS.keys()):
        spec = PROVIDERS[name]
        env_vars = ", ".join(provider_key_env_vars(name)) if spec.key_env_vars else "-"
        key_status = "-"
        if show_key_status and spec.requires_api_key:
            configured = bool(config_manager.get_provider_key(name))
            key_status = "yes" if configured else "no"
        elif show_key_status:
            key_status = "n/a"
        row: list[str] = [
            name,
            spec.default_model,
            env_vars,
        ]
        if show_key_status:
            row.append(key_status)
        row.append(spec.notes or "-")
        table.add_row(*row)
    console.print(table)


@provider_app.command("set-key")
def provider_set_key(
    ctx: typer.Context,
    provider: str = typer.Option(..., "--provider", help="Provider name, e.g. groq."),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="API key value. If omitted, you will be prompted securely.",
    ),
) -> None:
    """Store provider API key in OS keyring."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]
    normalized_provider = normalize_provider_name(provider, allow_custom=True)
    if normalized_provider is None:
        valid = ", ".join(sorted(PROVIDERS.keys()))
        raise typer.BadParameter(
            f"Unknown provider `{provider}`. Valid values: {valid}, or custom provider slug."
        )

    spec = PROVIDERS.get(normalized_provider)
    if spec is not None and not spec.requires_api_key:
        raise typer.BadParameter(f"{normalized_provider} does not require an API key.")

    secret = api_key or typer.prompt(
        f"Enter API key for {normalized_provider}",
        hide_input=True,
        confirmation_prompt=True,
    )
    if not secret.strip():
        raise typer.BadParameter("API key cannot be empty.")
    stored = config_manager.set_provider_key(normalized_provider, secret.strip())
    if not stored:
        raise typer.BadParameter(
            "Failed to store API key in keyring. Check keyring setup on this machine."
        )
    console.print(f"[green]Stored API key for provider:[/green] {normalized_provider}")


@provider_app.command("delete-key")
def provider_delete_key(
    ctx: typer.Context,
    provider: str = typer.Option(..., "--provider", help="Provider name, e.g. groq."),
) -> None:
    """Delete provider API key from OS keyring."""
    state = _get_state(ctx)
    console = state["console"]
    config_manager: ConfigManager = state["config_manager"]
    normalized_provider = normalize_provider_name(provider, allow_custom=True)
    if normalized_provider is None:
        valid = ", ".join(sorted(PROVIDERS.keys()))
        raise typer.BadParameter(
            f"Unknown provider `{provider}`. Valid values: {valid}, or custom provider slug."
        )

    deleted = config_manager.delete_provider_key(normalized_provider)
    if deleted:
        console.print(f"[green]Deleted API key for provider:[/green] {normalized_provider}")
    else:
        console.print(
            f"[yellow]No stored API key found for provider:[/yellow] {normalized_provider}"
        )


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
    _warn_if_experimental_platform(console, platform)
    _ensure_chromium_installed(console)

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

    if harden_session_state_permissions(state_path):
        console.print(
            "[green]Applied restrictive permissions to session file.[/green]"
        )
    else:
        console.print(
            "[yellow]Could not tighten session file permissions automatically. "
            "Protect this file manually.[/yellow]"
        )
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
    llm_provider: str | None = typer.Option(
        None,
        "--llm-provider",
        help="Provider used for persona extraction/summarization (ollama, groq, openrouter, ...).",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="Temporary API key for --llm-provider (not persisted unless --save-api-key).",
    ),
    save_api_key: bool = typer.Option(
        False,
        "--save-api-key",
        help="Save --api-key in OS keyring for future runs.",
    ),
    model: str | None = typer.Option(None, "--model", help="Override LiteLLM model."),
    no_remote_llm: bool = typer.Option(
        False,
        "--no-remote-llm",
        help="Keep LLM processing local-only (blocks hosted-provider fallback).",
    ),
    export_file: Path | None = typer.Option(
        None,
        "--export-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Manual export file (ZIP/JSON/HTML) to process in the same command.",
    ),
    wait_for_export: int = typer.Option(
        0,
        "--wait-for-export",
        min=0,
        help=(
            "Minutes to poll for downloaded manual export files after triggering official export. "
            "Use 0 for no waiting."
        ),
    ),
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
    command_started_at = datetime.now(timezone.utc)
    _warn_if_experimental_platform(console, from_platform)
    _warn_if_experimental_platform(console, to_platform)
    selected_provider, runtime_api_keys = _resolve_llm_runtime(
        config_manager=config_manager,
        provider=llm_provider,
        api_key=api_key,
        save_api_key=save_api_key,
    )
    allow_remote_llm, remote_prompt = _resolve_remote_llm_policy(
        console=console,
        provider=selected_provider,
        model=model,
        no_remote_llm=no_remote_llm,
    )

    if not safe_mode and not no_scrape:
        if not confirm_unsafe_mode(console):
            raise typer.Abort()

    conversations: list[Conversation] = []
    if export_file:
        console.print(f"[green]Using manual export file:[/green] {export_file}")
        conversations = processor.load_conversations(
            export_file.expanduser(), source_platform=from_platform
        )
    else:
        _ensure_chromium_installed(console)
        source_state_path = config_manager.session_state_path(app_config, from_platform)
        if not source_state_path.exists():
            raise typer.BadParameter(
                f"No saved session for {from_platform.value}. "
                f"Run `personaport login --platform {from_platform.value}` first, "
                "or pass `--export-file <path>`."
            )

        adapter = get_platform_adapter(from_platform)
        export_result = None
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task(description=f"Exporting from {from_platform.value}...", total=None)
            with BrowserManager(
                state_path=source_state_path,
                downloads_path=app_config.exports_dir,
                headless=headless,
            ).open() as runtime:
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

        if export_result.export_path and export_result.export_path.exists():
            conversations = processor.load_conversations(
                export_result.export_path, source_platform=from_platform
            )
        elif export_result.conversations:
            conversations = export_result.conversations
        elif export_result.status == "manual_required":
            discovered_export = _wait_for_manual_export_file(
                platform=from_platform,
                exports_dir=app_config.exports_dir,
                not_before=command_started_at,
                wait_minutes=wait_for_export,
                console=console,
            )
            if discovered_export:
                console.print(f"[green]Found manual export:[/green] {discovered_export}")
                conversations = processor.load_conversations(
                    discovered_export, source_platform=from_platform
                )

    if conversations:
        console.print(
            f"[green]Loaded {len(conversations)} conversation(s) from {from_platform.value} export.[/green]"
        )

    if not conversations:
        console.print(
            "[yellow]No conversations loaded into cache.[/yellow] "
            "Provide `--export-file <path>` or run "
            "`personaport process --file <export.zip> --from "
            f"{from_platform.value}` after downloading."
        )
        raise typer.Exit(code=0)

    for conversation in conversations:
        cache.save_conversation(conversation)

    selected_conversation = (
        processor.combine_conversations(conversations, from_platform)
        if all_history
        else _latest_conversation(conversations)
    )

    persona = processor.extract_persona(
        conversations,
        model=model,
        provider=selected_provider,
        api_keys=runtime_api_keys,
        use_llm=True,
        allow_remote_fallback=allow_remote_llm,
        remote_fallback_prompt=remote_prompt,
    )
    cache.save_persona(persona)

    target = to_platform or Platform.GENERIC
    condensed = processor.condense_history(
        selected_conversation,
        model=model,
        provider=selected_provider,
        api_keys=runtime_api_keys,
        use_llm=True,
        allow_remote_fallback=allow_remote_llm,
        remote_fallback_prompt=remote_prompt,
    )
    context_map = processor.build_context_map(conversations)
    artifact = transfer.build_artifact(
        selected_conversation,
        persona,
        condensed_history=condensed,
        context_map=context_map,
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
        _ensure_chromium_installed(console)
        if not _confirm_target_injection(console, to_platform):
            console.print("[yellow]Injection cancelled by user. Output files were generated.[/yellow]")
            raise typer.Exit(code=0)
        target_state_path = config_manager.session_state_path(app_config, to_platform)
        if not target_state_path.exists():
            console.print(
                f"[yellow]Target session missing for {to_platform.value}. "
                f"Run `personaport login --platform {to_platform.value}` before auto-injection.[/yellow]"
            )
            raise typer.Exit(code=0)

        attachment_files = _collect_attachment_files(output_files)
        transfer.inject_to_target(
            target_platform=to_platform,
            state_path=target_state_path,
            prompt_text=artifact.prompt_markdown,
            knowledge_files=attachment_files,
            headless=headless,
        )


@app.command()
def process(
    ctx: typer.Context,
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False),
    from_platform: Platform | None = typer.Option(None, "--from", case_sensitive=False),
    target: Platform = typer.Option(Platform.GENERIC, "--target", case_sensitive=False),
    all_history: bool = typer.Option(
        False,
        "--all",
        help="Combine all conversations from the input export into one migration bundle.",
    ),
    persona: str | None = typer.Option(None, "--persona", help="Manual persona override."),
    llm_provider: str | None = typer.Option(
        None,
        "--llm-provider",
        help="Provider used for persona extraction/summarization (ollama, groq, openrouter, ...).",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="Temporary API key for --llm-provider (not persisted unless --save-api-key).",
    ),
    save_api_key: bool = typer.Option(
        False,
        "--save-api-key",
        help="Save --api-key in OS keyring for future runs.",
    ),
    model: str | None = typer.Option(None, "--model"),
    no_remote_llm: bool = typer.Option(
        False,
        "--no-remote-llm",
        help="Keep LLM processing local-only (blocks hosted-provider fallback).",
    ),
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
    config_manager: ConfigManager = state["config_manager"]
    _warn_if_experimental_platform(console, from_platform)
    _warn_if_experimental_platform(console, target)

    selected_provider, runtime_api_keys = _resolve_llm_runtime(
        config_manager=config_manager,
        provider=llm_provider,
        api_key=api_key,
        save_api_key=save_api_key,
    )
    allow_remote_llm, remote_prompt = _resolve_remote_llm_policy(
        console=console,
        provider=selected_provider,
        model=model,
        no_remote_llm=no_remote_llm,
    )

    conversations = processor.load_conversations(file, source_platform=from_platform)
    if not conversations:
        raise typer.BadParameter("No conversations parsed from input file.")

    for conversation in conversations:
        cache.save_conversation(conversation)

    if all_history:
        selected = processor.combine_conversations(
            conversations, from_platform or Platform.GENERIC
        )
    elif conversation_id:
        selected = next((c for c in conversations if c.id == conversation_id), None)
        if selected is None:
            raise typer.BadParameter(f"Conversation ID not found in file: {conversation_id}")
    else:
        selected = _latest_conversation(conversations)

    persona_profile = processor.extract_persona(
        conversations,
        persona_override=persona,
        model=model,
        provider=selected_provider,
        api_keys=runtime_api_keys,
        use_llm=True,
        allow_remote_fallback=allow_remote_llm,
        remote_fallback_prompt=remote_prompt,
    )
    cache.save_persona(persona_profile)

    condensed = (
        processor.condense_history(
            selected,
            model=model,
            provider=selected_provider,
            api_keys=runtime_api_keys,
            use_llm=True,
            allow_remote_fallback=allow_remote_llm,
            remote_fallback_prompt=remote_prompt,
        )
        if summarize
        else selected.to_history_text()
    )
    context_map = processor.build_context_map(conversations)
    artifact = transfer.build_artifact(
        selected,
        persona_profile,
        condensed_history=condensed,
        context_map=context_map,
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
    all_history: bool = typer.Option(
        False,
        "--all",
        help="Combine all resolved source conversations into one migration bundle.",
    ),
    persona: str | None = typer.Option(None, "--persona"),
    llm_provider: str | None = typer.Option(
        None,
        "--llm-provider",
        help="Provider used for persona extraction/summarization (ollama, groq, openrouter, ...).",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="Temporary API key for --llm-provider (not persisted unless --save-api-key).",
    ),
    save_api_key: bool = typer.Option(
        False,
        "--save-api-key",
        help="Save --api-key in OS keyring for future runs.",
    ),
    model: str | None = typer.Option(None, "--model"),
    no_remote_llm: bool = typer.Option(
        False,
        "--no-remote-llm",
        help="Keep LLM processing local-only (blocks hosted-provider fallback).",
    ),
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
    _warn_if_experimental_platform(console, source)
    _warn_if_experimental_platform(console, target)
    selected_provider, runtime_api_keys = _resolve_llm_runtime(
        config_manager=config_manager,
        provider=llm_provider,
        api_key=api_key,
        save_api_key=save_api_key,
    )
    allow_remote_llm, remote_prompt = _resolve_remote_llm_policy(
        console=console,
        provider=selected_provider,
        model=model,
        no_remote_llm=no_remote_llm,
    )

    selected_conversation: Conversation | None = None
    source_conversations: list[Conversation] = []

    candidate_path = Path(input_value).expanduser()
    if input_value == "session":
        if all_history:
            cached_conversations = cache.list_conversations(limit=5000)
            if source:
                source_conversations = [
                    conversation
                    for conversation in cached_conversations
                    if (
                        conversation.source_platform.value
                        if isinstance(conversation.source_platform, Platform)
                        else str(conversation.source_platform)
                    )
                    == source.value
                ]
            else:
                source_conversations = cached_conversations
            if source_conversations:
                selected_conversation = processor.combine_conversations(
                    source_conversations, source or Platform.GENERIC
                )
        else:
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
            selected_conversation = (
                processor.combine_conversations(
                    source_conversations, source or Platform.GENERIC
                )
                if all_history
                else _latest_conversation(source_conversations)
            )
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
        provider=selected_provider,
        api_keys=runtime_api_keys,
        use_llm=True,
        allow_remote_fallback=allow_remote_llm,
        remote_fallback_prompt=remote_prompt,
    )
    cache.save_persona(persona_profile)

    condensed = processor.condense_history(
        selected_conversation,
        model=model,
        provider=selected_provider,
        api_keys=runtime_api_keys,
        use_llm=True,
        allow_remote_fallback=allow_remote_llm,
        remote_fallback_prompt=remote_prompt,
    )
    context_map = processor.build_context_map(source_conversations)
    artifact = transfer.build_artifact(
        selected_conversation,
        persona_profile,
        condensed_history=condensed,
        context_map=context_map,
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
        _ensure_chromium_installed(console)
        if not _confirm_target_injection(console, target):
            console.print("[yellow]Injection cancelled by user. Output files were generated.[/yellow]")
            raise typer.Exit(code=0)
        target_state_path = config_manager.session_state_path(app_config, target)
        if not target_state_path.exists():
            raise typer.BadParameter(
                f"No saved session for {target.value}. Run `personaport login --platform {target.value}` first."
            )
        attachment_files = _collect_attachment_files(output_files)
        transfer.inject_to_target(
            target_platform=target,
            state_path=target_state_path,
            prompt_text=artifact.prompt_markdown,
            knowledge_files=attachment_files,
            headless=headless,
        )


if __name__ == "__main__":
    app()
