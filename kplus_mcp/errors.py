"""Журнал ошибок и понятные сообщения пользователю.

Задача: человек, у которого что-то сломалось, не должен разбираться в
трассировках. Он видит короткое сообщение и одну инструкцию — прислать файл
отчёта. Отчёт собирается автоматически, ключи из него вырезаются.

Всё пишется только внутрь папки самой программы (см. config.LOG_DIR),
для журналов). Размер ограничен: три файла по 1 МБ, дальше старое затирается —
журнал не может незаметно съесть диск.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import sys
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config

# Журнал лежит внутри папки программы, а не в ~/Library/Logs: программа
# переносимая и в домашнюю папку пользователя не пишет.
LOG_DIR = config.LOG_DIR
LOG_FILE = LOG_DIR / "kplus.log"
MAX_BYTES = 1_000_000
BACKUPS = 2

_log: logging.Logger | None = None

# Всё, что похоже на ключ или пароль, в отчёт попадать не должно.
_SECRET_KEYS = re.compile(
    r"(API_KEY|APIKEY|TOKEN|SECRET|PASSWORD|PASSWD|KPLUS_LOGIN)", re.I)
_SECRET_VALUE = re.compile(
    r"\b(sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{10,}|gsk_[A-Za-z0-9_\-]{8,}"
    r"|pplx-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]{8,})")


def redact(text: str) -> str:
    """Вырезать ключи и пароли из произвольного текста."""
    text = _SECRET_VALUE.sub("[СКРЫТО]", text)
    out = []
    for line in text.splitlines():
        if "=" in line and _SECRET_KEYS.search(line.split("=", 1)[0]):
            key = line.split("=", 1)[0]
            out.append(f"{key}=[СКРЫТО]")
        else:
            out.append(line)
    return "\n".join(out)


def get_logger() -> logging.Logger:
    global _log
    if _log is not None:
        return _log

    log = logging.getLogger("kplus")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES,
                                      backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        log.addHandler(handler)
    except OSError:
        # Нет доступа к папке журналов — работаем без него, но не падаем.
        log.addHandler(logging.NullHandler())
    _log = log
    return log


def log_event(message: str, level: int = logging.INFO) -> None:
    get_logger().log(level, redact(message))


def log_exception(where: str, exc: BaseException) -> None:
    get_logger().error(
        "%s: %s: %s\n%s", where, type(exc).__name__, redact(str(exc)),
        redact("".join(traceback.format_exception(exc))))


FRIENDLY = {
    "TimeoutError": "КонсультантПлюс долго не отвечает. Проверьте интернет и попробуйте ещё раз.",
    "ConnectError": "Не удалось подключиться к сети. Проверьте интернет.",
    "ConnectTimeout": "Сервер не отвечает. Возможно, он временно недоступен.",
    "SystemExit": "",
    "KeyboardInterrupt": "",
}


def explain(exc: BaseException) -> str:
    """Короткое человеческое объяснение вместо трассировки."""
    name = type(exc).__name__
    if name in FRIENDLY:
        return FRIENDLY[name]
    text = str(exc)
    if "ERR_CERT" in text or "SSL" in text.upper():
        return ("Браузер не доверяет сертификату сайта К+. "
                "Если так и должно быть, обратитесь к тому, кто выдал вам доступ.")
    if "net::ERR" in text:
        return "Страница К+ не открылась. Проверьте интернет и адрес доступа."
    if "Не найден браузер" in text or "Executable doesn't exist" in text:
        return ("На компьютере нет ни одного браузера на движке Chromium. "
                "Поставьте Google Chrome обычным способом и запустите "
                "программу снова — качать браузер она не умеет и не будет.")
    return "Произошла непредвиденная ошибка."


def report_crash(exc: BaseException, where: str = "работа программы") -> None:
    """Записать в журнал и показать пользователю понятное сообщение."""
    log_exception(where, exc)
    print("\n" + "─" * 58, file=sys.stderr)
    print(f"  {explain(exc)}", file=sys.stderr)
    print("  Подробности записаны в журнал.", file=sys.stderr)
    print("\n  Если повторяется — запустите «Отчёт об ошибке.command»", file=sys.stderr)
    print("  и пришлите получившийся файл с рабочего стола.", file=sys.stderr)
    print("─" * 58 + "\n", file=sys.stderr)


def build_report() -> Path:
    """Собрать один текстовый файл, который пользователь пришлёт разработчику."""
    from . import config

    root = Path(__file__).resolve().parent.parent
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    # Отчёт кладём рядом с программой, а не на Рабочий стол: за пределы
    # своей папки программа не выходит.
    dest = config.BASE_DIR / f"Отчёт КонсультантПлюс {stamp}.txt"

    parts = [
        "ОТЧЁТ О РАБОТЕ ПОМОЩНИКА К+",
        f"Составлен: {datetime.now():%d.%m.%Y %H:%M}",
        "",
        "Ключи доступа и пароли из отчёта удалены.",
        "",
        "── Система ──",
        f"macOS/ОС: {platform.platform()}",
        f"Процессор: {platform.machine()}",
        f"Python: {sys.version.split()[0]}",
        f"Папка программы: {root}",
    ]

    from importlib.metadata import PackageNotFoundError, version

    for pkg in ("playwright", "mcp", "httpx", "python-docx"):
        try:
            parts.append(f"{pkg}: {version(pkg)}")
        except PackageNotFoundError:
            parts.append(f"{pkg}: не установлен")

    parts += ["", "── Настройки ──",
              f"Адрес К+: {config.BASE_URL}",
              f"Разрешённые адреса: {', '.join(config.ALLOWED_HOST_SUFFIXES)}",
              f"Провайдер модели: {os.environ.get('KPLUS_PROVIDER', 'не задан')}",
              f"Модель: {os.environ.get('KPLUS_MODEL', 'по умолчанию')}"]

    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
                 "PERPLEXITY_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        if os.environ.get(name):
            parts.append(f"Ключ {name}: задан")

    parts += ["", "── Состояние файлов ──",
              f"Профиль браузера: {'есть' if config.PROFILE_DIR.exists() else 'нет'}",
              f"Папка документов: {'есть' if config.OUT_DIR.exists() else 'нет'}",
              f"Окружение .venv: {'есть' if (root / '.venv').exists() else 'нет'}"]

    parts += ["", "── Журнал (последние записи) ──"]
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        parts += [redact(line) for line in lines[-400:]] or ["журнал пуст"]
    except OSError:
        parts.append("журнал недоступен или ещё не создан")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return dest


def install_hook() -> None:
    """Перехватывать всё, что не поймано, чтобы пользователь не видел трассировку."""
    def hook(kind, value, tb) -> None:
        if kind in (KeyboardInterrupt, SystemExit):
            sys.__excepthook__(kind, value, tb)
            return
        report_crash(value, "необработанная ошибка")

    sys.excepthook = hook
