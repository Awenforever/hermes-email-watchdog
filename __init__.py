"""Email Watchdog plugin registration."""

from __future__ import annotations

from .plugin_cli import email_watchdog_command, register_cli


def register(ctx) -> None:
    ctx.register_cli_command(
        name="email-watchdog",
        help="Install, configure, inspect, and run Email Watchdog",
        setup_fn=register_cli,
        handler_fn=email_watchdog_command,
        description="Read-only email triage with actionable-only notifications.",
    )
