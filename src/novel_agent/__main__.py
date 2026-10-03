"""Run the local Novel Agent CLI."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from novel_agent.cli.shell import NovelCLI


async def main() -> None:
    parser = argparse.ArgumentParser(description="Local Project-driven Novel Agent")
    parser.add_argument("--workspace", type=Path, default=Path.cwd() / "projects")
    parser.add_argument("--env", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args()
    shell = NovelCLI(args.workspace, args.env)
    console = Console()
    status = Console(stderr=True)
    prompt = PromptSession()
    console.print("[bold]Novel Agent[/bold] · /help 查看命令")
    while True:
        try:
            line = await prompt.prompt_async(shell.prompt_label())
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            continue
        buffer: list[str] = []
        live: Live | None = None

        def on_event(event) -> None:
            status.print(f"[{event.kind}] {event.detail}", highlight=False)

        def on_content(chunk: str, pieces: list[str] = buffer) -> None:
            nonlocal live
            pieces.append(chunk)
            if live is None:
                live = Live(Markdown(""), console=console, refresh_per_second=8)
                live.start()
            live.update(Markdown("".join(pieces)))

        try:
            result = await shell.execute(line, on_event=on_event, on_content=on_content)
            if live:
                live.stop()
            if result.clear_screen:
                console.clear()
                continue
            if result.run is None:
                if line.strip().startswith(
                    ("/artifact show", "/artifact current", "/skill show")
                ):
                    console.print(Markdown(result.text))
                else:
                    console.print(result.text, highlight=False, markup=False)
            elif not buffer:
                console.print(Markdown(result.text))
            if result.artifact_text:
                console.print(Markdown(result.artifact_text))
            if result.exit_requested:
                break
        except KeyboardInterrupt:
            if live:
                live.stop()
            status.print("[Cancelled] 当前 run 已取消")
        except asyncio.CancelledError:
            if live:
                live.stop()
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            status.print("[Cancelled] 当前 run 已取消")
        except Exception as exc:
            if live:
                live.stop()
            status.print(f"[Failed] {exc}")


if __name__ == "__main__":
    asyncio.run(main())
