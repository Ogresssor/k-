"""Локальный HTTP-фасад. Он же — сайдкар для оболочки на Tauri.

Слушает только 127.0.0.1: снаружи не доступно, наружу не публикуется,
хостинг не нужен.

    python -m kplus_mcp.http_api                # http://127.0.0.1:8787
    python -m kplus_mcp.http_api --port 51234   # порт выбирает оболочка

Кроме /ask есть /ask/stream: тот же запрос, но ответ идёт событиями по
мере работы. Без него интерфейс молчал бы минутами — агент ходит по К+
медленно нарочно, чтобы не выглядеть роботом.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from . import browser, config, tools
from .agent import LOCAL_PROVIDERS, PROVIDERS, run

HOST = os.environ.get("KPLUS_HTTP_HOST", "127.0.0.1")
PORT = int(os.environ.get("KPLUS_HTTP_PORT", "8787"))


async def list_tools(_: Request) -> JSONResponse:
    return JSONResponse(tools.openai_schema())


async def call_tool(request: Request) -> JSONResponse:
    body = await request.json()
    name = body.get("name") or body.get("tool")
    if not name:
        return JSONResponse({"error": "нужно поле name"}, status_code=400)
    result = await tools.call(name, body.get("arguments") or body.get("args") or {})
    return JSONResponse({"result": result})


async def ask(request: Request) -> JSONResponse:
    """Полный цикл: запрос на русском — готовый ответ. Может идти минуты."""
    body = await request.json()
    task = body.get("task") or body.get("query")
    if not task:
        return JSONResponse({"error": "нужно поле task"}, status_code=400)
    answer = await run(task, provider=body.get("provider"), model=body.get("model"))
    return JSONResponse({"answer": answer})


async def ask_stream(request: Request) -> StreamingResponse:
    """То же самое, но событиями (SSE): интерфейс показывает ход работы.

    Агент отдаёт прогресс через синхронный колбэк, а отправлять нужно из
    асинхронного генератора — поэтому между ними очередь.
    """
    body = await request.json()
    task = (body.get("task") or body.get("query") or "").strip()

    async def events():
        if not task:
            yield _sse("error", "Пустой запрос")
            return
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_event(kind: str, text: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, text))

        job = asyncio.create_task(
            run(task, provider=body.get("provider"),
                model=body.get("model"), on_event=on_event)
        )
        while True:
            pending = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({pending, job},
                                         return_when=asyncio.FIRST_COMPLETED)
            if pending in done:
                kind, text = pending.result()
                yield _sse(kind, text)
                continue
            pending.cancel()
            # Задача кончилась — досылаем то, что осталось в очереди.
            while not queue.empty():
                kind, text = queue.get_nowait()
                yield _sse(kind, text)
            try:
                yield _sse("answer", job.result())
            except Exception as exc:
                from .errors import explain
                yield _sse("error", explain(exc))
            return

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


def _sse(kind: str, text: str) -> str:
    return "data: " + json.dumps({"kind": kind, "text": text},
                                 ensure_ascii=False) + chr(10) + chr(10)


def _model_ready() -> tuple[str, bool]:
    """Какой моделью думаем и есть ли к ней ключ.

    Сам ключ наружу не отдаём никогда — только «есть/нет». Проверка нужна
    потому, что без ключа приложение выглядит совершенно исправным и
    ломается лишь на первом же вопросе.
    """
    name = (os.environ.get("KPLUS_PROVIDER") or "gemini").lower()
    provider = PROVIDERS.get(name)
    if provider is None:
        return name, False
    if name in LOCAL_PROVIDERS:
        return name, True
    return name, bool(os.environ.get(provider.key_env, "").strip())


async def close_session(_: Request) -> JSONResponse:
    """Закрыть окно К+ и освободить сеанс.

    Нужно именно закрывать: пока окно живо, К+ считает сеанс занятым, и
    вход с другого устройства будет выглядеть как второй пользователь.
    """
    await browser.shutdown()
    browser.quit_browser()
    return JSONResponse({"ok": True, "session_open": False})


async def health(_: Request) -> JSONResponse:
    """Оболочка дёргает это, пока не ответит, — так она понимает, что готово."""
    provider, has_key = _model_ready()
    return JSONResponse({
        "ok": True,
        "tools": [t.name for t in tools.TOOLS],
        "browser": config.browser_executable(),
        "browsers": config.installed_browsers(),
        "out_dir": str(config.OUT_DIR),
        "base_dir": str(config.BASE_DIR),
        "provider": provider,
        "model_key": has_key,
        # Открыто ли сейчас окно К+. Пока открыто — сеанс занят, и второй
        # вход в К+ где угодно ещё будет считаться вторым пользователем.
        "session_open": browser._cdp_alive(),
    })


app = Starlette(routes=[
    Route("/health", health),
    Route("/tools", list_tools),
    Route("/call", call_tool, methods=["POST"]),
    Route("/ask", ask, methods=["POST"]),
    Route("/ask/stream", ask_stream, methods=["POST"]),
    Route("/session/close", close_session, methods=["POST"]),
])


def main() -> None:
    ap = argparse.ArgumentParser(prog="kplus-core", description="Локальный фасад К+")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default=HOST)
    args = ap.parse_args()
    print(f"Фасад К+ на http://{args.host}:{args.port}  (только локально)", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
