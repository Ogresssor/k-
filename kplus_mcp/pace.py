"""Ограничитель темпа обращений к КонсультантПлюс.

Смысл простой: программа должна работать в том же ритме и объёме, что и живой
человек за той же задачей. Не потому, что нужно кого-то обмануть, а потому что
аккаунты блокируют именно за нагрузку — за всплеск обращений, которого у
человека быть не может.

Считаются только обращения к серверу К+: переходы, клики, отправка поиска.
Чтение уже открытой страницы сервер не трогает и в счёт не идёт.
"""
from __future__ import annotations

import asyncio
import time

from . import config
from .errors import log_event


class PaceLimit(RuntimeError):
    """Исчерпан разумный объём обращений. Не ошибка, а сработавший тормоз."""


_last_request: float = 0.0
_task_count: int = 0
_hour_stamps: list[float] = []


def start_task() -> None:
    """Новый запрос пользователя — счётчик страниц на задачу обнуляется."""
    global _task_count
    _task_count = 0


async def before_request() -> None:
    """Вызывается перед каждым обращением к серверу К+."""
    global _last_request, _task_count

    now = time.monotonic()

    # 1. Часовой бюджет: выкидываем всё старше часа и считаем остаток.
    cutoff = now - 3600
    _hour_stamps[:] = [t for t in _hour_stamps if t > cutoff]
    if len(_hour_stamps) >= config.MAX_PAGES_PER_HOUR:
        log_event(f"Часовой лимит обращений исчерпан ({config.MAX_PAGES_PER_HOUR})")
        raise PaceLimit(
            f"За последний час открыто {len(_hour_stamps)} страниц К+ — это предел, "
            "заданный, чтобы не создавать нагрузку на сервер. "
            "Сделайте перерыв и вернитесь позже."
        )

    # 2. Бюджет на один запрос пользователя.
    if _task_count >= config.MAX_PAGES_PER_TASK:
        log_event(f"Лимит страниц на задачу исчерпан ({config.MAX_PAGES_PER_TASK})")
        raise PaceLimit(
            f"На этот запрос уже открыто {_task_count} страниц К+ — предел на одну "
            "задачу. Сформулируйте вопрос уже: назовите конкретную статью или "
            "сузьте период практики."
        )

    # 3. Пауза между обращениями: человек читает страницу, а не листает её мгновенно.
    waited = now - _last_request
    if _last_request and waited < config.MIN_INTERVAL_S:
        await asyncio.sleep(config.MIN_INTERVAL_S - waited)

    _last_request = time.monotonic()
    _task_count += 1
    _hour_stamps.append(_last_request)


def stats() -> dict[str, int]:
    cutoff = time.monotonic() - 3600
    return {
        "страниц_за_эту_задачу": _task_count,
        "предел_на_задачу": config.MAX_PAGES_PER_TASK,
        "страниц_за_час": len([t for t in _hour_stamps if t > cutoff]),
        "предел_в_час": config.MAX_PAGES_PER_HOUR,
    }
