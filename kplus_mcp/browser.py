"""Браузер живёт отдельно от агента и переживает его выход.

Раньше окно открывалось через launch_persistent_context и потому
принадлежало процессу python: команда отработала — окно закрылось,
через 2-10 секунд после открытия. Это было не сбоем, а устройством.

Теперь владение перевёрнуто. Браузер запускается как самостоятельная
программа с открытым портом отладки (CDP), а агент подключается к нему
клиентом. Отсюда три следствия:

  * окно не закрывается, когда агент отработал команду и вышел;
  * следующий запрос переиспользует уже открытое окно — вход в К+
    и прогретая сессия остаются на месте;
  * скачивать нечего: работаем с установленным Chrome, а не со сборкой
    Chromium от Playwright, которую часто режет сеть.

Окно видимое, и пользователь закрывает его как любое другое окно
браузера. Программно — quit_browser(), но обычной работе это не нужно.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from . import config, pace
from .errors import log_event

_lock = asyncio.Lock()
_pw: Any = None
_browser: Browser | None = None
_ctx: BrowserContext | None = None


_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _bypass_proxy_for_localhost() -> None:
    """Запретить прокси вмешиваться в разговор с собственным браузером.

    Прокси в этой программе нужен только для похода к модели. Но HTTP_PROXY
    из окружения перехватывает всё подряд, включая запрос на 127.0.0.1: сам
    прокси отвечает на него 407 или 503, и агент решает, что браузера нет.
    Драйвер Playwright написан на Node и ведёт себя так же, поэтому одного
    trust_env=False на стороне httpx не хватает — нужен NO_PROXY в окружении.

    Уже заданный NO_PROXY не затираем: у пользователя там может быть свой
    список, который ломать нельзя.
    """
    for var in ("NO_PROXY", "no_proxy"):
        current = [h.strip() for h in os.environ.get(var, "").split(",") if h.strip()]
        missing = [h for h in _LOCAL_HOSTS if h not in current]
        if missing:
            os.environ[var] = ",".join(current + missing)


def _cdp_url() -> str:
    return f"http://127.0.0.1:{config.CDP_PORT}"


def _cdp_alive(timeout: float = 0.6) -> bool:
    """Отвечает ли уже открытый браузер на порту отладки.

    trust_env=False здесь обязателен. По умолчанию httpx уважает HTTP_PROXY
    из окружения и отправляет через прокси даже запрос на 127.0.0.1 — прокси
    отвечает 503, и агент решает, что браузер не открылся, хотя тот работает.
    Прокси в этой программе нужен только для похода к модели; браузер и
    разговор с ним по локальной петле идут мимо него всегда.
    """
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            return client.get(f"{_cdp_url()}/json/version").status_code == 200
    except Exception:
        return False


def _spawn_browser() -> None:
    """Открыть браузер отдельным процессом и отвязаться от него.

    start_new_session уводит его в собственную сессию: он переживает выход
    агента и не ловит Ctrl+C, адресованный агенту.
    """
    exe = config.browser_executable()
    if not exe:
        raise RuntimeError(
            "Не найден браузер на движке Chromium. Установите Google Chrome "
            "или укажите путь к браузеру в KPLUS_BROWSER_PATH."
        )
    config.ensure_dirs()
    args = [
        exe,
        f"--remote-debugging-port={config.CDP_PORT}",
        # Свой профиль, а не обычный профиль пользователя: вкладки, пароли и
        # сессии не пересекаются. Это ещё и обязательное условие — начиная с
        # Chrome 136 порт отладки на профиле по умолчанию запрещён.
        f"--user-data-dir={config.PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=ru-RU",
        "--window-size=1440,900",
        "about:blank",
    ]
    if config.HEADLESS:
        args.insert(1, "--headless=new")

    log_event(f"Открываю браузер: {Path(exe).name}")
    proc = subprocess.Popen(
        args,
        env=dict(os.environ, TZ="Europe/Moscow"),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Запоминаем pid, чтобы окно можно было закрыть из другого запуска агента.
    try:
        config.BROWSER_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass

    # Порт открывается не мгновенно — ждём, но не бесконечно.
    deadline = time.monotonic() + config.BROWSER_START_S
    while time.monotonic() < deadline:
        if _cdp_alive():
            return
        if proc.poll() is not None:
            # Браузер вышел сам. Обычная причина — запущенный экземпляр того же
            # браузера перехватил запуск (так делают Arc, Comet и Яндекс).
            raise RuntimeError(
                f"Браузер {Path(exe).name} завершился сразу после запуска. "
                "Закройте все его окна и повторите запрос, либо укажите в "
                "KPLUS_BROWSER_PATH другой браузер на движке Chromium."
            )
        time.sleep(0.3)
    raise RuntimeError(
        f"Браузер не открыл порт отладки за {config.BROWSER_START_S} с."
    )


async def context() -> BrowserContext:
    global _pw, _browser, _ctx
    async with _lock:
        if _ctx is not None:
            return _ctx
        _bypass_proxy_for_localhost()
        if _cdp_alive():
            log_event("Подключаюсь к уже открытому браузеру")
        else:
            _spawn_browser()
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.connect_over_cdp(_cdp_url())
        # У браузера с настоящим профилем контекст ровно один — он же профиль.
        _ctx = _browser.contexts[0] if _browser.contexts else await _browser.new_context()
        _ctx.set_default_timeout(config.NAV_TIMEOUT_MS)
        return _ctx


async def page() -> Page:
    """Текущая вкладка. Лишние вкладки, которые открыл сам сайт, закрываем —
    иначе за долгую сессию их накапливается десяток и память течёт."""
    ctx = await context()
    live = [p for p in ctx.pages if not p.is_closed()]
    for extra in live[config.MAX_TABS:]:
        try:
            await extra.close()
        except Exception:
            pass
    if live:
        return live[0]
    return await ctx.new_page()


async def shutdown() -> None:
    """Отключиться от браузера, оставив окно открытым.

    Это конец работы агента, а не конец работы браузера. Вход в К+ живёт
    в окне, и следующий запрос подключится к нему без повторного логина.

    _browser.close() здесь намеренно не вызывается: для соединения по CDP
    он может закрыть сам браузер, а нам нужно ровно обратное. Достаточно
    остановить драйвер — сокет закроется, окно останется.
    """
    global _pw, _browser, _ctx
    _ctx = None
    _browser = None
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None
    log_event("Агент отключился, окно браузера осталось открытым")


def quit_browser() -> None:
    """Закрыть окно браузера. Нужно при смене профиля или переустановке."""
    try:
        pid = int(config.BROWSER_PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log_event(f"Браузер закрыт (pid {pid})")
    except (ProcessLookupError, PermissionError, OSError):
        pass
    config.BROWSER_PID_FILE.unlink(missing_ok=True)


# atexit-страховки здесь больше нет, и это не упущение. Она закрывала окно
# при выходе агента — ровно то поведение, от которого мы избавляемся. К тому
# же прежняя её версия создавала новый event loop для объектов, привязанных
# к старому, и потому всегда падала внутрь пустого except.
async def goto(url: str) -> Page:
    if not config.host_allowed(url):
        raise ValueError(
            f"Домен вне белого списка: {url}. Разрешены: {', '.join(config.ALLOWED_HOST_SUFFIXES)}"
        )
    p = await page()
    await pace.before_request()
    await p.goto(url, wait_until="domcontentloaded")
    await settle(p)
    return p


async def settle(p: Page, ms: int = 1200) -> None:
    """К+ дорисовывает выдачу скриптами — даём кадру устояться."""
    try:
        await p.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await p.wait_for_timeout(ms)


# --- снимок страницы для модели -------------------------------------------------

_SNAPSHOT_JS = r"""
() => {
  const clean = (s) => (s || '').trim().replace(/\s+/g, ' ');

  // Подпись поля ищем как это делает человек: сначала явные атрибуты, потом
  // связанный <label>, потом текст слева — в старых интранет-версиях К+
  // вёрстка табличная, и подпись лежит в соседней ячейке.
  const labelFor = (el) => {
    let t = clean(el.getAttribute('aria-label') || el.getAttribute('placeholder')
                  || el.getAttribute('title'));
    if (t) return t;
    const id = el.getAttribute('id');
    if (id) {
      const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
      if (lab) { t = clean(lab.innerText); if (t) return t; }
    }
    const wrap = el.closest('label');
    if (wrap) { t = clean(wrap.innerText); if (t) return t; }
    const cell = el.closest('td, th');
    if (cell && cell.previousElementSibling) {
      t = clean(cell.previousElementSibling.innerText);
      if (t) return t;
    }
    let prev = el.previousSibling;
    while (prev) {
      t = clean(prev.textContent);
      if (t) return t.slice(0, 60);
      prev = prev.previousSibling;
    }
    return '';
  };

  const out = [];
  let i = 0;
  const sel = 'a[href], button, input, textarea, select, [role=button], [role=link], [role=tab], [onclick]';
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    const hidden = (el.getAttribute('type') || '').toLowerCase() === 'hidden';
    if (hidden || !r.width || !r.height || st.visibility === 'hidden' || st.display === 'none') continue;
    i += 1;
    const ref = 'e' + i;
    el.setAttribute('data-kp-ref', ref);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const isInput = tag === 'textarea' || tag === 'select'
                    || (tag === 'input' && !['submit', 'button', 'image', 'reset'].includes(type));
    // Поле ввода узнаётся по внешней подписи, кнопка и ссылка — по своему
    // тексту. Перепутать порядок нельзя: у кнопки рядом может лежать чужой
    // текст, и она подпишется им.
    const own = clean(el.innerText || el.value);
    out.push({
      ref,
      tag,
      type,
      text: (isInput ? clean(labelFor(el) || el.value)
                     : (own || labelFor(el))).slice(0, 120),
      name: clean(el.getAttribute('name') || el.getAttribute('id')).slice(0, 60),
      input: isInput,  // в поле можно печатать, в кнопку нет
      href: (el.getAttribute('href') || '').slice(0, 300),
    });
    if (i > 400) break;
  }
  return { url: location.href, title: document.title, elements: out,
           text: (document.body ? document.body.innerText : '') };
}
"""


async def snapshot(text_limit: int | None = None) -> dict:
    p = await page()
    data = await p.evaluate(_SNAPSHOT_JS)
    limit = text_limit or config.PAGE_TEXT_LIMIT
    text = data.get("text") or ""
    data["text_truncated"] = len(text) > limit
    data["text"] = text[:limit]
    # Выкидываем пустые и повторяющиеся ссылки — они только едят контекст.
    # Поля ввода не трогаем никогда: безымянное поле «Логин» без подписи всё
    # равно нужно модели, иначе она не сможет ни войти, ни искать.
    seen, els = set(), []
    for e in data.get("elements", []):
        if not e.get("input"):
            if not e["text"] and not e["href"]:
                continue
            key = (e["tag"], e["text"], e["href"])
            if key in seen:
                continue
            seen.add(key)
        els.append(e)
    data["elements"] = els
    return data


async def click(ref: str) -> None:
    p = await page()
    await pace.before_request()
    await p.click(f'[data-kp-ref="{ref}"]')
    await settle(p)


async def fill(ref: str, value: str, submit: bool = False) -> None:
    p = await page()
    loc = p.locator(f'[data-kp-ref="{ref}"]')
    await loc.click()
    await loc.fill(value)
    if submit:
        # Отправка поиска — обращение к серверу, набор текста в поле — нет.
        await pace.before_request()
        await loc.press("Enter")
    await settle(p)


async def document_text() -> str:
    """Полный текст открытого документа: сначала пробуем контейнер текста К+,
    иначе отдаём body целиком."""
    p = await page()
    candidates = [
        "#document-page", ".document-page", "#doc_content", ".doc-content",
        "[class*='documentText']", "[class*='doc-text']", "main", "article",
    ]
    for sel in candidates:
        try:
            loc = p.locator(sel).first
            if await loc.count() and (t := (await loc.inner_text()).strip()) and len(t) > 500:
                return t
        except Exception:
            continue
    return (await p.inner_text("body")).strip()
