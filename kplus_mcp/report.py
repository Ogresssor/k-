"""Сборка отчёта о неполадке для пересылки разработчику."""
from __future__ import annotations

import subprocess
import sys

from .errors import build_report

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def main() -> None:
    try:
        path = build_report()
    except Exception as e:
        print(f"Не удалось собрать отчёт: {e}")
        return

    print("Готово. Файл отчёта лежит на рабочем столе:\n")
    print(f"    {path.name}\n")
    print("Перешлите его тому, кто выдал вам программу.")
    print("Ключи доступа и пароли из отчёта удалены.")
    try:
        subprocess.run(["open", "-R", str(path)], check=False)
    except OSError:
        pass


if __name__ == "__main__":
    main()
