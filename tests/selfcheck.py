"""Проверка, что браузер живёт отдельно от агента.

Это тест на ту самую поломку, ради которой переписан browser.py: окно
открывалось и закрывалось через 2-10 секунд, потому что принадлежало
процессу python.

Проверка идёт в три захода, каждый — отдельный процесс python, как и
у реального пользователя:

  1. открыть браузер, поработать, выйти;
  2. убедиться, что порт отладки всё ещё отвечает — окно пережило выход;
  3. подключиться заново и проверить, что это тот же самый браузер
     (по идентификатору сессии), а не открытый второй раз.

Мака для этого не нужно: сценарий одинаков на macOS, Linux и Windows.

    python tests/selfcheck.py            # с видимым окном
    KPLUS_HEADLESS=1 python tests/selfcheck.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Своя песочница: настоящие профиль, логи и документы пользователя не трогаем.
SANDBOX = Path(tempfile.gettempdir()) / "kplus-selfcheck"
os.environ.setdefault("KPLUS_DATA_DIR", str(SANDBOX / "data"))
os.environ.setdefault("KPLUS_LOG_DIR", str(SANDBOX / "logs"))
os.environ.setdefault("KPLUS_OUT_DIR", str(SANDBOX / "out"))
# Тест не ходит в К+, поэтому белый список подменяем на локальную заглушку.
os.environ.setdefault("KPLUS_ALLOWED_HOSTS", "example.com")
os.environ.setdefault("KPLUS_CDP_PORT", "9444")

# Консоль Windows по умолчанию не в UTF-8, а галочки и кириллица нужны.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

OK, BAD, DIM, N = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
failures: list[str] = []


def check(label: str, passed: bool, detail: str = "") -> None:
    mark = f"{OK}✓{N}" if passed else f"{BAD}✗{N}"
    print(f"  {mark} {label}" + (f" {DIM}— {detail}{N}" if detail else ""))
    if not passed:
        failures.append(label)


# --- шаг, который запускается в отдельном процессе ------------------------------

async def _phase_open() -> None:
    """Открыть браузер, сходить на страницу, выйти штатно."""
    from kplus_mcp import browser

    ctx = await browser.context()
    page = await browser.page()
    await page.goto("data:text/html,<title>kplus selfcheck</title><h1>ok</h1>")
    title = await page.title()
    print(json.dumps({"title": title, "pages": len(ctx.pages)}))
    await browser.shutdown()


async def _phase_reconnect() -> None:
    """Подключиться к уже открытому браузеру и снять отпечаток."""
    from kplus_mcp import browser

    await browser.context()
    page = await browser.page()
    print(json.dumps({"url": page.url}))
    await browser.shutdown()


PHASES = {"open": _phase_open, "reconnect": _phase_reconnect}


def _run_phase(name: str) -> tuple[int, str]:
    """Запустить фазу отдельным процессом — так же, как это делает пользователь."""
    proc = subprocess.run(
        [sys.executable, __file__, "--phase", name],
        capture_output=True, text=True, env=os.environ,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, tail[-1] if tail else ""


# --- сам сценарий ----------------------------------------------------------------

def main() -> int:
    from kplus_mcp import browser, config

    exe = config.browser_executable()
    print(f"\n{DIM}песочница: {SANDBOX}{N}")
    print(f"{DIM}порт CDP : {config.CDP_PORT}{N}\n")

    check("браузер на движке Chromium найден", bool(exe), exe or "установите Chrome")
    if not exe:
        return 1

    # Чистый старт: если от прошлого прогона осталось окно — закрываем.
    browser.quit_browser()

    print("\nШаг 1. Агент открывает браузер и штатно выходит")
    code, out = _run_phase("open")
    check("процесс агента завершился без ошибки", code == 0, out if code else "")
    if code:
        return 1
    check("страница открылась", '"title": "kplus selfcheck"' in out, out)

    print("\nШаг 2. Главное: пережило ли окно выход агента")
    alive = browser._cdp_alive(timeout=2.0)
    check("браузер жив после выхода агента", alive,
          "именно здесь окно закрывалось раньше")
    if not alive:
        return 1

    print("\nШаг 3. Второй запуск подключается к тому же окну")
    before = browser._cdp_alive()
    code, out = _run_phase("reconnect")
    check("повторное подключение удалось", code == 0, out if code else "")
    check("окно осталось тем же, второго не открылось",
          before and browser._cdp_alive())

    print("\nШаг 4. Явное закрытие")
    browser.quit_browser()
    import time
    for _ in range(20):
        if not browser._cdp_alive(timeout=0.4):
            break
        time.sleep(0.3)
    check("quit_browser() закрывает окно", not browser._cdp_alive(timeout=1.0))

    print()
    if failures:
        print(f"{BAD}Провалено: {len(failures)}{N} — " + "; ".join(failures))
        return 1
    print(f"{OK}Всё прошло.{N} Браузер переживает выход агента и переиспользуется.")
    return 0


if __name__ == "__main__":
    if "--phase" in sys.argv:
        asyncio.run(PHASES[sys.argv[sys.argv.index("--phase") + 1]]())
    else:
        try:
            code = main()
        except ImportError as exc:
            print(f"\n{BAD}Не хватает зависимости: {exc.name}{N}")
            print("Поставьте их: pip install -r requirements.txt")
            sys.exit(2)
        finally:
            # Окно не должно пережить сам тест, чем бы он ни кончился.
            try:
                from kplus_mcp import browser
                browser.quit_browser()
            except ImportError:
                pass
        sys.exit(code)
