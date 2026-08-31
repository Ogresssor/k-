"""Режим 2 из 3: собственный агентный цикл с любой моделью.

Нужен, когда клиент не умеет MCP — прежде всего Perplexity, а также любые
OpenAI-совместимые провайдеры и локальные модели через Ollama или LM Studio.

Два способа вызова инструментов, переключаются автоматически:
  * native — штатный function calling (OpenAI, DeepSeek, OpenRouter, Ollama);
  * text   — модель пишет действие текстом по простому протоколу. Это запасной
             путь для моделей без function calling, в том числе для Perplexity
             Sonar: у него поддержка вызова функций ограничена и меняется,
             поэтому текстовый протокол здесь надёжнее.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import config, pace, tools
from .errors import log_event, log_exception
from .prompt import load as load_prompt


@dataclass(frozen=True)
class Provider:
    base_url: str
    default_model: str
    key_env: str
    mode: str  # "native" | "text"


PROVIDERS: dict[str, Provider] = {
    # Бесплатные без карты (есть суточные лимиты) — идут первыми не случайно.
    "gemini":     Provider("https://generativelanguage.googleapis.com/v1beta/openai",
                           "gemini-2.5-flash", "GEMINI_API_KEY", "native"),
    "groq":       Provider("https://api.groq.com/openai/v1",
                           "llama-3.3-70b-versatile", "GROQ_API_KEY", "native"),
    # Бесплатно и приватно: модель крутится на самом маке, наружу ничего не уходит.
    "ollama":     Provider("http://localhost:11434/v1", "qwen2.5:14b", "OLLAMA_API_KEY", "native"),
    "lmstudio":   Provider("http://localhost:1234/v1", "local-model", "LMSTUDIO_API_KEY", "native"),
    # Платные, по факту использования.
    "openai":     Provider("https://api.openai.com/v1", "gpt-4.1", "OPENAI_API_KEY", "native"),
    "perplexity": Provider("https://api.perplexity.ai", "sonar-pro", "PERPLEXITY_API_KEY", "text"),
    "openrouter": Provider("https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4.5",
                           "OPENROUTER_API_KEY", "native"),
    "deepseek":   Provider("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY", "native"),
}

# Провайдеры, которым ключ не нужен вообще: модель работает на этом же компьютере.
LOCAL_PROVIDERS = ("ollama", "lmstudio")

MAX_STEPS = config.MAX_STEPS
MAX_SECONDS = config.MAX_SECONDS

TEXT_PROTOCOL = """
Инструменты вызываются текстом. Чтобы воспользоваться инструментом, выведи
РОВНО такой блок и ничего после него:

ДЕЙСТВИЕ: имя_инструмента
АРГУМЕНТЫ: {"параметр": "значение"}

Результат придёт следующим сообщением, после чего продолжай работу.
Когда всё выяснено и работа закончена — просто напиши финальный ответ
пользователю без блока ДЕЙСТВИЕ.

Доступные инструменты:
""".strip()


def _resolve(provider: str | None, model: str | None) -> tuple[Provider, str, str]:
    name = (provider or os.environ.get("KPLUS_PROVIDER", "gemini")).lower()
    if name not in PROVIDERS:
        raise SystemExit(f"Неизвестный провайдер: {name}. Доступны: {', '.join(PROVIDERS)}")
    p = PROVIDERS[name]
    # Свой базовый адрес — для корпоративного шлюза или прокси.
    base = os.environ.get("KPLUS_BASE_API_URL", p.base_url)
    key = os.environ.get(p.key_env) or os.environ.get("KPLUS_API_KEY") or "no-key"
    if key == "no-key" and name not in LOCAL_PROVIDERS:
        raise SystemExit(
            f"Не задан ключ для «{name}». Запустите «Настроить.command» ещё раз "
            f"и укажите ключ — или впишите {p.key_env} в файл .env."
        )
    return Provider(base, p.default_model, p.key_env, os.environ.get("KPLUS_TOOL_MODE", p.mode)), \
        model or os.environ.get("KPLUS_MODEL") or p.default_model, key


def _tools_as_text() -> str:
    lines = []
    for t in tools.TOOLS:
        params = ", ".join(t.parameters.get("properties", {}).keys()) or "без аргументов"
        lines.append(f"- {t.name}({params}) — {t.description}")
    return "\n".join(lines)


_ACTION_RE = re.compile(
    r"ДЕЙСТВИЕ:\s*(?P<name>[\w_]+)\s*\n\s*АРГУМЕНТЫ:\s*(?P<args>\{.*?\})",
    re.S,
)


def _explain_http(code: int, body: str) -> str:
    """Код ответа провайдера — человеческим языком. Технические подробности
    уже записаны в журнал, пользователю их видеть незачем."""
    if code in (401, 403):
        return ("Ключ доступа к модели не принят. Запустите «Настроить.command» "
                "и введите ключ заново.")
    if code == 429:
        return ("Исчерпан лимит бесплатных запросов на сегодня. Попробуйте позже "
                "или выберите в «Настроить.command» другого провайдера.")
    if code == 404:
        return ("Выбранная модель недоступна. Запустите «Настроить.command» "
                "и выберите другую.")
    if code >= 500:
        return "У сервиса модели неполадки. Попробуйте через несколько минут."
    if "context" in body.lower() and "length" in body.lower():
        return ("Документ слишком большой для этой модели. Попробуйте запрос "
                "по конкретной статье, а не по всему закону.")
    return f"Сервис модели ответил ошибкой {code}. Подробности записаны в журнал."


def _parse_text_action(content: str) -> tuple[str, dict] | None:
    m = _ACTION_RE.search(content)
    if not m:
        return None
    try:
        args = json.loads(m.group("args"))
    except json.JSONDecodeError:
        return m.group("name"), {}
    return m.group("name"), args


class _Route:
    """Путь до модели с запасным вариантом.

    Держит по клиенту на каждый путь, ходит первым рабочим и запоминает
    его на остаток задачи. Если запомненный путь отвалился посреди работы,
    следующий запрос снова переберёт варианты.
    """

    def __init__(self, clients: list[tuple[str, httpx.AsyncClient]],
                 emit: Callable[[str, str], None]) -> None:
        self._clients = clients
        self._emit = emit
        self._chosen: int | None = None

    @property
    def label(self) -> str:
        return self._clients[self._chosen][0] if self._chosen is not None else "не выбран"

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        start = self._chosen or 0
        order = [start] + [i for i in range(len(self._clients)) if i != start]
        first_error: Exception | None = None

        for i in order:
            label, client = self._clients[i]
            try:
                response = await client.post(url, **kwargs)
            except httpx.HTTPError as exc:
                if first_error is None:
                    first_error = exc
                if self._chosen == i:
                    self._chosen = None
                log_event(f"Путь «{label}» не сработал: {type(exc).__name__}")
                continue

            if self._chosen != i:
                self._chosen = i
                log_event(f"Модель отвечает {label}")
                if i > 0:
                    self._emit("tool", f"связь с моделью восстановлена — {label}")
            return response

        raise first_error if first_error else RuntimeError("нет путей до модели")


def _explain_network(exc: Exception, prov: Provider, proxy: str | None) -> str:
    """Назвать причину, а не посоветовать «проверьте интернет».

    Сетевых причин у неудачного запроса к модели ровно три, и лечатся они
    по-разному: недоступен прокси, недоступен сам сервис, либо всё слишком
    медленно. Общая фраза не даёт понять, какая именно, — и тогда сбой
    невозможно починить, не читая журнал.
    """
    from urllib.parse import urlparse

    host = urlparse(prov.base_url).hostname or prov.base_url
    detail = str(exc).strip() or type(exc).__name__

    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout)):
        return (f"Модель на {host} не ответила вовремя. Попробуйте ещё раз."
                f"\n\nТехническая причина: {detail}")

    # Сюда попадаем, только если не сработал ни один путь: прокси уже
    # пробовался вместе с прямым соединением, и наоборот.
    if proxy:
        where = urlparse(proxy).hostname or proxy
        return (f"Не удалось связаться с моделью на {host} — ни через прокси "
                f"{where}, ни напрямую.\n\n"
                "Если у вас включён VPN, попробуйте его выключить: он может "
                "перехватывать соединение с прокси. Строка KPLUS_MODEL_PROXY "
                "в файле .env рядом с программой задаёт прокси, её можно "
                f"убрать или заменить своей.\n\nТехническая причина: {detail}")

    return (f"Не удалось соединиться с {host}.\n\n"
            "Похоже, сервис закрыт для вашей страны или сети. Нужен прокси "
            "или VPN: прокси задаётся строкой KPLUS_MODEL_PROXY в файле .env "
            f"рядом с программой.\n\nТехническая причина: {detail}")


async def run(task: str, provider: str | None = None, model: str | None = None,
              on_event: Callable[[str, str], None] | None = None) -> str:
    """Отработать задачу до конца и вернуть финальный ответ модели.

    on_event(вид, текст) — колбэк для показа прогресса: "step", "tool", "answer".
    """
    prov, model_name, key = _resolve(provider, model)
    emit = on_event or (lambda kind, text: None)

    system = load_prompt()
    if prov.mode == "text":
        system += "\n\n" + TEXT_PROTOCOL + "\n" + _tools_as_text()

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]

    # Для локальных моделей (Ollama, LM Studio) системный прокси нужно обойти,
    # иначе запрос к 127.0.0.1 уедет на корпоративный шлюз и вернёт его ошибку.
    local = any(h in prov.base_url for h in ("127.0.0.1", "localhost", "0.0.0.0"))

    # Прокси только для модели: браузер (К+) им не пользуется. Когда прокси задан,
    # системные переменные окружения игнорируем — используем ровно его.
    proxy = config.MODEL_PROXY or None

    # Сеть у каждого своя. У одного сервис закрыт по региону и прокси
    # обязателен; у другого тот же прокси недоступен, зато сервис открыт
    # напрямую; у третьего включён VPN, и прокси только мешает. Требовать
    # от пользователя правки .env под каждый случай — плохо, поэтому
    # готовим оба пути и выбираем рабочий сами.
    routes: list[tuple[str, dict[str, Any]]] = []
    if proxy:
        routes.append(("через прокси", {"timeout": 180, "proxy": proxy, "trust_env": False}))
        routes.append(("напрямую", {"timeout": 180, "trust_env": False}))
    else:
        routes.append(("напрямую", {"timeout": 180, "trust_env": not local}))

    started = time.monotonic()
    pace.start_task()
    log_event(f"Запрос ({prov.mode}, {model_name}, proxy={'да' if proxy else 'нет'}): {task[:200]}")

    async with contextlib.AsyncExitStack() as stack:
        http = _Route(
            [(label, await stack.enter_async_context(httpx.AsyncClient(**kw)))
             for label, kw in routes],
            emit,
        )
        for step in range(MAX_STEPS):
            # Предохранитель от зацикливания: агент не может молотить часами,
            # выжигая заряд, процессор и лимит запросов.
            if time.monotonic() - started > MAX_SECONDS:
                log_event(f"Прерван по времени на шаге {step}")
                return ("Работа заняла слишком много времени и была остановлена. "
                        "Попробуйте сузить запрос — например, указать конкретную "
                        "статью или период практики.")

            payload: dict[str, Any] = {"model": model_name, "messages": messages}
            if prov.mode == "native":
                payload["tools"] = tools.openai_schema()

            try:
                r = await http.post(
                    f"{prov.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=payload,
                )
            except httpx.HTTPError as e:
                log_exception("обращение к модели", e)
                return _explain_network(e, prov, proxy)

            if r.status_code >= 400:
                log_event(f"Модель вернула {r.status_code}: {r.text[:400]}", logging.ERROR)
                return _explain_http(r.status_code, r.text)

            msg = r.json()["choices"][0]["message"]
            content = msg.get("content") or ""

            # --- штатный function calling
            calls = msg.get("tool_calls") or []
            if calls:
                messages.append(msg)
                for c in calls:
                    fname = c["function"]["name"]
                    try:
                        fargs = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        fargs = {}
                    emit("tool", f"{fname} {json.dumps(fargs, ensure_ascii=False)[:160]}")
                    result = await tools.call(fname, fargs)
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                continue

            # --- текстовый протокол
            if prov.mode == "text":
                action = _parse_text_action(content)
                if action:
                    fname, fargs = action
                    emit("tool", f"{fname} {json.dumps(fargs, ensure_ascii=False)[:160]}")
                    result = await tools.call(fname, fargs)
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user",
                                     "content": f"РЕЗУЛЬТАТ {fname}:\n{result}"})
                    continue

            emit("answer", content)
            log_event(f"Готово за {step + 1} шагов, {time.monotonic() - started:.0f} с")
            return content

    return (f"Достигнут предел в {MAX_STEPS} шагов. Последнее сообщение модели:\n"
            + str(messages[-1].get("content", ""))[:2000])
