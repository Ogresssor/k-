"""Мастер настройки: задаёт вопросы на русском и сам пишет все настройки.

Пользователь не открывает ни одного файла и не пишет ни строчки кода.
Запускается двойным щелчком по «Настроить.command».
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from . import config

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# Адрес по умолчанию — портал, через который работает заказчик.
DEFAULT_KPLUS_URL = "https://consultant.ddns.net/cons/"

B, N, DIM = "\033[1m", "\033[0m", "\033[2m"
OK, WARN = "\033[32m", "\033[33m"

for _stream in (sys.stdout, sys.stderr):
    # Терминал с не-UTF-8 кодировкой не должен ронять мастер на русском тексте.
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def say(text: str = "") -> None:
    print(text, flush=True)


def head(text: str) -> None:
    say(f"\n{B}{text}{N}\n" + "─" * min(len(text) + 2, 60))


def ask(question: str, default: str = "") -> str:
    suffix = f" {DIM}[{default}]{N}" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        say("\nОтменено.")
        sys.exit(1)
    return answer or default


def choose(question: str, options: list[tuple[str, str, str]]) -> str:
    """options: (код, название, пояснение). Возвращает код."""
    say(f"\n{B}{question}{N}\n")
    for i, (_, title, note) in enumerate(options, 1):
        say(f"  {B}{i}{N}. {title}")
        for line in note.splitlines():
            say(f"     {DIM}{line}{N}")
        say()
    while True:
        raw = ask("Введите номер", "1")
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        say(f"{WARN}Нужен номер от 1 до {len(options)}.{N}")


def read_env() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip()
    return data


def write_env(data: dict[str, str]) -> None:
    lines = [
        "# Настройки агента К+. Файл создан мастером настройки.",
        "# Чтобы что-то поменять, проще запустить «Настроить.command» заново.",
        "",
    ]
    lines += [f"{k}={v}" for k, v in data.items() if v]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)  # ключ виден только владельцу
    except OSError:
        pass


# --- шаги ------------------------------------------------------------------------

PROVIDER_OPTIONS = [
    ("gemini", "Google Gemini — бесплатно, без карты  ← рекомендую",
     "Ключ выдаётся за минуту по аккаунту Google. Есть суточный лимит,\n"
     "для нескольких дел в день его хватает с запасом.\n"
     "Важно: на бесплатном тарифе Google вправе использовать ваши запросы\n"
     "для улучшения своих моделей. Для дел с персональными данными — вариант 3."),
    ("groq", "Groq — бесплатно, без карты",
     "Тоже бесплатный ключ, отвечает очень быстро.\n"
     "Модели послабее в юридических текстах, чем Gemini."),
    ("ollama", "Локальная модель — бесплатно и полностью приватно",
     "Модель работает на самом маке, наружу не уходит ничего.\n"
     "Нужно скачать Ollama и модель (несколько ГБ), нужен мак с 16 ГБ памяти.\n"
     "Качество заметно ниже — для черновиков сгодится, для важного нет."),
    ("openai", "Платный ключ (OpenAI, Perplexity, OpenRouter, DeepSeek)",
     "Если у вас уже есть ключ. Оплата по факту использования."),
]

KEY_PAGES = {
    "gemini": ("https://aistudio.google.com/apikey",
               "Откроется страница Google AI Studio.\n"
               "Войдите под Google, нажмите «Create API key» и скопируйте ключ."),
    "groq": ("https://console.groq.com/keys",
             "Откроется консоль Groq. Зарегистрируйтесь и нажмите «Create API Key»."),
    "openai": ("https://platform.openai.com/api-keys", "Страница ключей OpenAI."),
    "perplexity": ("https://www.perplexity.ai/account/api/keys", "Страница ключей Perplexity."),
    "openrouter": ("https://openrouter.ai/keys", "Страница ключей OpenRouter."),
    "deepseek": ("https://platform.deepseek.com/api_keys", "Страница ключей DeepSeek."),
}

KEY_ENV = {
    "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY", "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def step_consent(env: dict[str, str]) -> None:
    """Честное предупреждение до установки.

    Пользователь должен узнать про риск для своего доступа к К+ здесь, а не
    после блокировки. Согласие записывается с датой — чтобы потом не было
    разговора «мне никто не говорил»."""
    if env.get("KPLUS_CONSENT"):
        return

    head("Прежде чем начать — прочитайте")
    say("Программа заходит в КонсультантПлюс под вашей учётной записью и")
    say("листает страницы вместо вас. Для К+ это выглядит как ваша работа,")
    say("потому что это и есть ваша работа — просто быстрее.\n")
    say("Что важно понимать:\n")
    say("  • Лицензия К+ обычно ограничивает автоматическую обработку")
    say("    материалов. Формально такое использование может нарушать")
    say("    условия вашего доступа.")
    say("  • Если доступ вам выдала организация или региональный центр —")
    say("    решение о блокировке принимают они, а не автор программы.")
    say("  • Программа держит человеческий темп и не выкачивает базу:")
    say(f"    пауза {int(config.MIN_INTERVAL_S)} с между страницами, не более")
    say(f"    {config.MAX_PAGES_PER_TASK} страниц на запрос и {config.MAX_PAGES_PER_HOUR} в час.")
    say("    Это ровно тот объём, который открывает человек за работой.")
    say("  • Риск не нулевой. Решение — ваше.\n")
    say("Если доступ к К+ у вас не свой, а по чужой договорённости —")
    say("лучше сначала спросите у того, кто его выдал.\n")

    answer = ask("Понятно, продолжаем? (д/н)", "д")
    if not answer.lower().startswith(("д", "y")):
        say("\nХорошо. Ничего не установлено, ничего не изменено.")
        sys.exit(0)

    env["KPLUS_CONSENT"] = datetime.now().strftime("%Y-%m-%d")


def step_provider(env: dict[str, str]) -> None:
    head("Шаг 1 из 3. Чем будет думать агент")

    # Если модель и ключ уже прописаны заранее (готовый архив) — не переспрашиваем.
    prov = env.get("KPLUS_PROVIDER", "")
    has_key = prov in ("ollama", "lmstudio") or env.get(KEY_ENV.get(prov, "___"))
    if prov and has_key:
        say(f"{OK}Модель уже настроена: {prov}. Пропускаю.{N}")
        return

    provider = choose("Выберите вариант:", PROVIDER_OPTIONS)

    if provider == "openai":
        provider = choose("Какой сервис?", [
            ("openai", "OpenAI (GPT)", ""),
            ("perplexity", "Perplexity", "Работает через текстовый протокол — медленнее."),
            ("openrouter", "OpenRouter", "Один ключ ко всем моделям, включая Claude."),
            ("deepseek", "DeepSeek", "Дешевле остальных."),
        ])

    env["KPLUS_PROVIDER"] = provider

    if provider in ("ollama", "lmstudio"):
        step_local_model(env)
        return

    url, hint = KEY_PAGES[provider]
    say(f"\n{hint}\n")
    if ask("Открыть эту страницу в браузере? (д/н)", "д").lower().startswith(("д", "y")):
        webbrowser.open(url)
    else:
        say(f"{DIM}Адрес: {url}{N}")

    say()
    existing = env.get(KEY_ENV[provider], "")
    prompt = "Вставьте ключ сюда и нажмите Enter"
    if existing:
        prompt += f" {DIM}(Enter — оставить прежний){N}"
    key = ask(prompt, existing)
    if not key:
        say(f"{WARN}Ключ не введён. Без него агент не заработает — "
            f"запустите мастер ещё раз, когда получите ключ.{N}")
    env[KEY_ENV[provider]] = key


def step_local_model(env: dict[str, str]) -> None:
    say()
    if shutil.which("ollama"):
        say(f"{OK}Ollama уже установлена.{N}")
    else:
        say("Ollama не найдена. Скачайте её с ollama.com, установите и вернитесь сюда.")
        if ask("Открыть сайт? (д/н)", "д").lower().startswith(("д", "y")):
            webbrowser.open("https://ollama.com/download")
        ask("Нажмите Enter, когда установите")

    model = ask("Какую модель использовать", "qwen2.5:14b")
    env["KPLUS_MODEL"] = model
    if shutil.which("ollama"):
        say(f"\nСкачиваю модель {model} — это несколько ГБ, может занять время…\n")
        try:
            subprocess.run(["ollama", "pull", model], check=False)
        except OSError as e:
            say(f"{WARN}Не удалось запустить ollama: {e}{N}")


def step_kplus(env: dict[str, str]) -> None:
    head("Шаг 2 из 3. Ваш КонсультантПлюс")

    # Адрес уже прописан заранее (готовый архив) — не переспрашиваем.
    if env.get("KPLUS_BASE_URL"):
        say(f"{OK}Адрес К+ уже задан: {env['KPLUS_BASE_URL']}. Пропускаю.{N}")
        return
    say("Если вы заходите в К+ по обычному для вас адресу — просто нажмите Enter.\n"
        "Если по другому — откройте К+ как всегда и скопируйте адрес из\n"
        "адресной строки браузера.\n")
    url = ask("Адрес страницы входа в К+",
              env.get("KPLUS_BASE_URL") or DEFAULT_KPLUS_URL)
    if not url.startswith("http"):
        url = "https://" + url
    env["KPLUS_BASE_URL"] = url

    host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].removeprefix("www.")
    if host and not (host == "consultant.ru" or host.endswith(".consultant.ru")):
        # В белый список идёт ТОЧНОЕ имя хоста, а не «корень» домена.
        # Адреса вида xxx.ddns.net живут на публичном сервисе динамических имён:
        # разрешив ddns.net целиком, мы открыли бы дорогу к чужим серверам,
        # которые там может завести кто угодно.
        say(f"\n{DIM}Адрес {host} добавлен в список разрешённых.{N}")
        env["KPLUS_ALLOWED_HOSTS"] = f"consultant.ru,{host}"


def step_claude(env: dict[str, str]) -> None:
    head("Шаг 3 из 3. Подключение к Claude (необязательно)")
    cfg = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if not cfg.parent.is_dir():
        say("Claude Desktop на этом маке не найден — пропускаю.")
        say("Агентом можно пользоваться через «Спросить К+.command».")
        return

    say("Claude Desktop найден. Могу подключить агента к нему,\n"
        "тогда К+ будет доступен прямо в чате Claude.\n")
    if not ask("Подключить? (д/н)", "д").lower().startswith(("д", "y")):
        return

    data: dict = {}
    if cfg.is_file():
        # Чужие настройки не теряем: сначала копия, потом правка.
        shutil.copy(cfg, cfg.with_suffix(".json.backup"))
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            say(f"{WARN}Файл настроек Claude был повреждён — создаю заново. "
                f"Копия старого: {cfg.with_suffix('.json.backup')}{N}")

    data.setdefault("mcpServers", {})["kplus"] = {
        "command": str(ROOT / ".venv" / "bin" / "python"),
        "args": ["-m", "kplus_mcp.server"],
        "cwd": str(ROOT),
    }
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"{OK}Готово. Перезапустите Claude Desktop — появится К+.{N}")


def step_login() -> None:
    head("Первый вход в КонсультантПлюс")
    say("Сейчас откроется окно браузера. Войдите в К+ как обычно —\n"
        "логин и пароль вводите руками. Программа их не сохраняет\n"
        "и никуда не передаёт, она просто запомнит, что вы уже вошли.\n\n"
        "Окно останется открытым и после настройки — так вход\n"
        "не придётся повторять. Закрыть его можно как любое окно\n"
        "браузера.\n")
    if not ask("Войти сейчас? (д/н)", "д").lower().startswith(("д", "y")):
        say("Хорошо, войдёте при первом запросе.")
        return

    import asyncio

    from . import browser, tools

    async def go() -> None:
        try:
            say("\nОткрываю окно…\n")
            say(await tools.kplus_login())
        finally:
            await browser.shutdown()

    try:
        asyncio.run(go())
    except Exception as e:
        say(f"{WARN}Не получилось: {e}{N}")
        say("Ничего страшного — попробуете при первом запросе.")


def main() -> None:
    say(f"\n{B}Настройка юридического ИИ-агента с доступом к КонсультантПлюс{N}")
    say(f"{DIM}Несколько коротких шагов. Ничего редактировать руками не придётся.{N}")

    env = read_env()
    # «Настроить» мог определить, что вместо встроенного Chromium используется
    # установленный Chrome/Edge — сохраняем этот выбор в настройки.
    channel = os.environ.get("KPLUS_BROWSER_CHANNEL", "").strip()
    if channel:
        env["KPLUS_BROWSER_CHANNEL"] = channel
    exe = os.environ.get("KPLUS_BROWSER_PATH", "").strip()
    if exe:
        env["KPLUS_BROWSER_PATH"] = exe
    step_consent(env)
    step_provider(env)
    step_kplus(env)
    write_env(env)
    step_claude(env)
    write_env(env)
    step_login()

    head("Всё настроено")
    say("Чтобы задать вопрос — двойной щелчок по файлу «Спросить К+.command».")
    say("Готовые документы будут складываться в папку «Документы» рядом с программой.")
    say(f"\n{DIM}Поменять настройки можно, запустив «Настроить.command» ещё раз.{N}\n")
    ask("Нажмите Enter, чтобы закрыть")


if __name__ == "__main__":
    main()
