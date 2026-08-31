"""Точка входа сайдкара для PyInstaller.

Отдельный файл нужен потому, что `python -m kplus_mcp.http_api` внутри
собранного бинарника не работает: модуля для -m там уже нет.
"""
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from kplus_mcp.http_api import main
    main()
