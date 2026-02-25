from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

MANDATORY_WARNING = (
    "THIS TOOL USES BROWSER AUTOMATION WHICH MAY VIOLATE PLATFORM TOS. "
    "USE AT YOUR OWN RISK. RISK OF ACCOUNT BAN. "
    "WE STRONGLY RECOMMEND USING OFFICIAL MANUAL EXPORT INSTEAD."
)


def get_console() -> Console:
    return Console()


def print_runtime_warning(console: Console) -> None:
    warning = Text(MANDATORY_WARNING, style="bold red")
    panel = Panel(
        warning,
        title="PersonaPort Safety Warning",
        border_style="red",
        expand=False,
    )
    console.print(panel)


def confirm_unsafe_mode(console: Console) -> bool:
    console.print(
        "[bold red]UNSAFE MODE ENABLED:[/bold red] scraping fallback may violate ToS."
    )
    return Confirm.ask("Proceed anyway?", default=False, console=console)
