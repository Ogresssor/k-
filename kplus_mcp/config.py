"""Пути, домены и ограничения. Всё переопределяется переменными окружения.

Принцип: программа переносимая. Она не устанавливается, не прописывается
в автозагрузку и не пишет ничего ни в системные папки, ни в домашнюю папку
пользователя — всё лежит внутри её собственной папки. Папку можно перенести
или удалить целиком, и следов не останется. Плюс потолок по времени, шагам
и объёму данных: сломать работу мака она не должна даже при сбое.

Единственное, что остаётся жить после выхода агента, — видимое окно
браузера: оно хранит вход в К+, и пользователь закрывает его как любое
другое окно.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env_file() -> None:
    """Читаем .env рядом с проектом, чтобы ключи не приходилось экспортировать
    вручную в каждом терминале. Уже заданные переменные окружения не трогаем —
    они главнее файла."""
    path = Path(os.environ.get("KPLUS_ENV_FILE", Path(__file__).parent.parent / ".env"))
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

def _portable_base() -> Path:
    """Папка, внутри которой живёт вся программа со всеми своими данными.

    Программа переносимая: она не устанавливается, ничего не пишет ни в
    системные папки, ни в домашнюю папку пользователя. Всё — профиль
    браузера, готовые документы, журнал — лежит рядом с ней. Папку можно
    перенести куда угодно или удалить целиком, и следов не останется.

    Из собранного приложения путь считаем от самого .app, а не от файла
    внутри него: данные должны лежать рядом с приложением, а не внутри,
    иначе они пропадут при следующей сборке.
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent.parent
        return exe.parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = Path(os.environ.get("KPLUS_BASE_DIR", _portable_base()))

# Единственные места на диске, куда программа пишет, — и все они внутри
# её собственной папки.
DATA_DIR = Path(os.environ.get("KPLUS_DATA_DIR", BASE_DIR / "Данные"))
PROFILE_DIR = DATA_DIR / "browser-profile"
OUT_DIR = Path(os.environ.get("KPLUS_OUT_DIR", BASE_DIR / "Документы"))
LOG_DIR = Path(os.environ.get("KPLUS_LOG_DIR", DATA_DIR / "Журнал"))

# Стартовая страница вашего доступа к К+.
BASE_URL = os.environ.get("KPLUS_BASE_URL", "https://consultant.ddns.net/cons/")

# Куда агенту разрешено ходить. Сравнение точное или по поддомену.
#
# Важно: сюда попадает ПОЛНОЕ имя хоста, а не «корень» домена. Для адресов на
# публичных сервисах динамических имён (ddns.net, no-ip.org и подобных) это
# принципиально: разрешив ddns.net целиком, мы открыли бы дорогу к чужим
# серверам, которые может завести кто угодно.
ALLOWED_HOST_SUFFIXES = tuple(
    h.strip().lower()
    for h in os.environ.get("KPLUS_ALLOWED_HOSTS",
                            "consultant.ru,consultant.ddns.net").split(",")
    if h.strip()
)

# Окно браузера видимое: К+ отсекает headless-трафик, да и логин удобнее руками.
HEADLESS = os.environ.get("KPLUS_HEADLESS", "0") == "1"

# Какой браузер использовать. Пусто — встроенный Chromium (его нужно скачать).
# "chrome"/"msedge" — уже установленный Google Chrome или Edge; качать не надо.
# Это выручает там, где сеть блокирует cdn.playwright.dev.
BROWSER_CHANNEL = os.environ.get("KPLUS_BROWSER_CHANNEL", "").strip()

# Путь к исполняемому файлу любого браузера на движке Chromium (Arc, Yandex,
# Comet и т.п.). Если задан — используется он, и качать ничего не нужно.
# Имеет приоритет над BROWSER_CHANNEL.
BROWSER_EXECUTABLE = os.environ.get("KPLUS_BROWSER_PATH", "").strip()

# Прокси ТОЛЬКО для запросов к модели. Браузер (К+) его не использует и ходит
# напрямую. Это развязывает конфликт: портал К+ хочет российский IP, а модель
# из-за рубежа — нужен заграничный выход. Формат: http://user:pass@host:port
# Пусто — модель ходит напрямую (для локальных моделей и там, где не блокируют).
MODEL_PROXY = os.environ.get("KPLUS_MODEL_PROXY", "").strip()

# --- потолки, чтобы ничто не разрослось бесконтрольно --------------------------

PAGE_TEXT_LIMIT = int(os.environ.get("KPLUS_PAGE_TEXT_LIMIT", "12000"))
DOC_CHUNK = int(os.environ.get("KPLUS_DOC_CHUNK", "20000"))

NAV_TIMEOUT_MS = int(os.environ.get("KPLUS_NAV_TIMEOUT_MS", "45000"))
LOGIN_WAIT_S = int(os.environ.get("KPLUS_LOGIN_WAIT_S", "300"))

# Больше вкладок агенту не нужно, а забытые вкладки едят память.
MAX_TABS = int(os.environ.get("KPLUS_MAX_TABS", "4"))

# Предохранитель от зацикливания: агент не может работать дольше и больше.
MAX_STEPS = int(os.environ.get("KPLUS_MAX_STEPS", "30"))
MAX_SECONDS = int(os.environ.get("KPLUS_MAX_SECONDS", "900"))

# --- темп обращений к К+ ---------------------------------------------------------
#
# Аккаунты блокируют не за автоматизацию как таковую, а за нагрузку: сорок
# документов за минуту там, где человек открывает пять за полчаса. Поэтому
# программа держит человеческий темп и человеческий объём.
#
# Пауза между обращениями к серверу К+. Живой человек читает страницу
# минимум несколько секунд, прежде чем нажать следующую ссылку.
MIN_INTERVAL_S = float(os.environ.get("KPLUS_MIN_INTERVAL_S", "4"))

# Сколько страниц К+ можно открыть за один запрос пользователя.
MAX_PAGES_PER_TASK = int(os.environ.get("KPLUS_MAX_PAGES_PER_TASK", "40"))

# И сколько всего за час работы — на случай десяти запросов подряд.
MAX_PAGES_PER_HOUR = int(os.environ.get("KPLUS_MAX_PAGES_PER_HOUR", "150"))


def host_allowed(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def ensure_dirs() -> None:
    for folder in (DATA_DIR, PROFILE_DIR, OUT_DIR, LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)


# --- браузер как отдельная программа ---------------------------------------------
#
# Агент не владеет браузером, а подключается к нему по порту отладки. Поэтому
# окно переживает выход агента: вход в К+ делается один раз, а не на каждый
# запрос. Порт слушает только 127.0.0.1 и снаружи недоступен.
CDP_PORT = int(os.environ.get("KPLUS_CDP_PORT", "9333"))

# Сколько ждать, пока браузер откроет порт после запуска.
BROWSER_START_S = int(os.environ.get("KPLUS_BROWSER_START_S", "30"))

# Чтобы окно можно было закрыть из другого запуска агента.
BROWSER_PID_FILE = DATA_DIR / "browser.pid"

# Где искать браузер, если он не задан явно. Порядок важен: сначала то, что
# ведёт себя предсказуемо при запуске второго экземпляра.
_CHANNEL_PATHS = {
    "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "msedge": "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "chromium": "/Applications/Chromium.app/Contents/MacOS/Chromium",
}

_FALLBACK_PATHS = (
    _CHANNEL_PATHS["chrome"],
    _CHANNEL_PATHS["msedge"],
    _CHANNEL_PATHS["chromium"],
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
    "/Applications/Yandex.app/Contents/MacOS/Yandex",
    "/Applications/Comet.app/Contents/MacOS/Comet",
    "/Applications/Arc.app/Contents/MacOS/Arc",
)

# Программа предназначена для мака, но разработка и проверка идут не на нём.
# Эти пути нужны только для того, чтобы прогонять код на Windows и в Linux —
# на поведение у пользователя они не влияют.
_DEV_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
)


def _scan_applications() -> list[str]:
    """Найти на маке любой браузер на движке Chromium, даже неизвестный нам.

    Скачивать мы ничего не будем никогда, поэтому список известных имён —
    это только приоритет, а не ограничение. Все сборки Chromium устроены
    одинаково: внутри .app лежит папка Frameworks, а в ней фреймворк с
    именем вида «Google Chrome Framework.framework». Ни у Safari, ни у
    Firefox, ни у обычных программ такого нет — признак надёжный.
    """
    found: list[str] = []
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        if not folder.is_dir():
            continue
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for app in entries:
            if app.suffix != ".app":
                continue
            frameworks = app / "Contents" / "Frameworks"
            if not frameworks.is_dir():
                continue
            try:
                chromium = any(f.name.endswith(" Framework.framework")
                               for f in frameworks.iterdir())
            except OSError:
                continue
            if not chromium:
                continue
            binary = app / "Contents" / "MacOS" / app.stem
            if os.access(binary, os.X_OK):
                found.append(str(binary))
    return found


def browser_executable() -> str:
    """Путь к уже установленному браузеру на движке Chromium.

    Порядок: явная настройка, затем знакомые браузеры в порядке
    предпочтения, затем всё остальное, что нашлось в «Программах».
    Пустая строка означает, что на компьютере нет ни одного — и тогда
    единственный выход это поставить браузер, качать его сама программа
    не станет.
    """
    if BROWSER_EXECUTABLE and os.access(BROWSER_EXECUTABLE, os.X_OK):
        return BROWSER_EXECUTABLE
    if BROWSER_CHANNEL:
        path = _CHANNEL_PATHS.get(BROWSER_CHANNEL.lower(), "")
        if path and os.access(path, os.X_OK):
            return path
    for path in _FALLBACK_PATHS + _DEV_PATHS:
        if os.access(path, os.X_OK):
            return path
    if scanned := _scan_applications():
        return scanned[0]
    from shutil import which

    for name in ("google-chrome", "chromium", "chromium-browser",
                 "microsoft-edge", "chrome.exe", "msedge.exe"):
        if found := which(name):
            return found
    return ""


def installed_browsers() -> list[str]:
    """Все найденные браузеры — чтобы показать пользователю выбор в интерфейсе."""
    seen, out = set(), []
    for path in list(_FALLBACK_PATHS) + _scan_applications() + list(_DEV_PATHS):
        if path not in seen and os.access(path, os.X_OK):
            seen.add(path)
            out.append(path)
    return out
