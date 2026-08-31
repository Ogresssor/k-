"""Запуск агента из терминала — работает с любой моделью, без всякого клиента.

    kplus "напиши претензию в страховую..."            # модель по умолчанию
    kplus --provider perplexity "найди практику по..."
    kplus --provider ollama --model qwen2.5:14b "..."
    kplus                                              # диалоговый режим
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from . import browser, tools
from .agent import PROVIDERS, run
from .errors import install_hook, log_event, report_crash

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

C_DIM, C_TOOL, C_OFF = "\033[2m", "\033[36m", "\033[0m"


def _event(kind: str, text: str) -> None:
    if kind == "tool":
        print(f"{C_TOOL}  → {text}{C_OFF}", file=sys.stderr, flush=True)


async def _one(task: str, provider: str | None, model: str | None) -> None:
    answer = await run(task, provider=provider, model=model, on_event=_event)
    print("\n" + answer)


async def _chat(provider: str | None, model: str | None) -> None:
    print(f"{C_DIM}Диалог с агентом К+. Пустая строка или Ctrl+C — выход.{C_OFF}")
    while True:
        try:
            task = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not task:
            break
        await _one(task, provider, model)


def main() -> None:
    ap = argparse.ArgumentParser(prog="kplus", description="ИИ-агент с доступом к КонсультантПлюс")
    ap.add_argument("task", nargs="*", help="запрос; без него — диалоговый режим")
    ap.add_argument("--provider", choices=sorted(PROVIDERS), help="какой моделью думать")
    ap.add_argument("--model", help="конкретная модель провайдера")
    ap.add_argument("--list-tools", action="store_true", help="показать инструменты и выйти")
    args = ap.parse_args()

    if args.list_tools:
        for t in tools.TOOLS:
            print(f"{t.name:22} {t.description.splitlines()[0]}")
        return

    task = " ".join(args.task).strip()

    install_hook()
    log_event("Старт из терминала")

    async def go() -> None:
        try:
            if task:
                await _one(task, args.provider, args.model)
            else:
                await _chat(args.provider, args.model)
        finally:
            await browser.shutdown()

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
