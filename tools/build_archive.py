"""Сборка архива для мака. Один источник правды вместо ручных команд.

    python tools/build_archive.py                # без .env — для передачи
    python tools/build_archive.py --with-env     # с ключом, только для себя

Права на исполнение проставляются внутри архива, поэтому chmod на маке
не нужен. Собирается на любой ОС: содержимое от платформы не зависит.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "KPlusAgent"

EXEC_NAMES = {"kplus", "run_server.sh"}
SKIP_NAMES = {".venv", "__pycache__", ".git", ".python", ".DS_Store",
              "browser-install.log", "dist"}
SKIP_SUFFIX = {".bak", ".pyc", ".log"}

CONTENTS = [
    "kplus_mcp", "templates", "tests", "tools", "ui", "src-tauri",
    ".env.example", ".gitignore", "requirements.txt",
    "README.md", "ИНСТРУКЦИЯ.md", "PLAYBOOK.md",
    "kplus", "run_server.sh",
    "Настроить.command", "Собрать приложение.command",
    "Спросить К+.command", "Отчёт об ошибке.command",
]


def keep(path: Path) -> bool:
    return path.name not in SKIP_NAMES and path.suffix not in SKIP_SUFFIX


def mode_for(path: Path) -> int:
    if path.name == ".env":
        return 0o600          # ключ читает только владелец
    if path.name in EXEC_NAMES or path.suffix == ".command":
        return 0o755
    return 0o644


# Всё, что на маке читает bash или python, должно быть с переносами LF.
# Один \r в начале «Настроить.command» — и запуск падает с невнятным
# «bash: $'\r': command not found». Собирать архив можно на любой ОС,
# поэтому нормализуем на всякий случай прямо здесь.
TEXT_SUFFIX = {".py", ".sh", ".command", ".md", ".txt", ".json", ".toml",
               ".yml", ".html", ".rs", ".example"}
TEXT_NAMES = {"kplus", ".env", ".env.example", ".gitignore"}


def normalize(path: Path, data: bytes) -> bytes:
    if path.suffix in TEXT_SUFFIX or path.name in TEXT_NAMES:
        return data.replace(b"\r\n", b"\n")
    return data


def add(tar: tarfile.TarFile, path: Path, arc: str) -> None:
    if not keep(path):
        return
    if path.is_dir():
        info = tarfile.TarInfo(arc)
        info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
        tar.addfile(info)
        for child in sorted(path.iterdir()):
            add(tar, child, f"{arc}/{child.name}")
        return
    data = normalize(path, path.read_bytes())
    info = tarfile.TarInfo(arc)
    info.size, info.mode, info.mtime = len(data), mode_for(path), int(time.time())
    tar.addfile(info, io.BytesIO(data))


def build(with_env: bool, out: Path) -> Path:
    contents = list(CONTENTS)
    if with_env:
        contents.insert(0, ".env")

    out.parent.mkdir(parents=True, exist_ok=True)
    missing = []
    with tarfile.open(out, "w:gz", format=tarfile.GNU_FORMAT, encoding="utf-8") as tar:
        top = tarfile.TarInfo(TOP)
        top.type, top.mode, top.mtime = tarfile.DIRTYPE, 0o755, int(time.time())
        tar.addfile(top)
        for name in contents:
            path = ROOT / name
            if not path.exists():
                missing.append(name)
                continue
            add(tar, path, f"{TOP}/{name}")

    if missing:
        print("!! не найдено:", ", ".join(missing), file=sys.stderr)

    with tarfile.open(out) as tar:
        members = tar.getmembers()
        has_env = any(m.name == f"{TOP}/.env" for m in members)
    if has_env != with_env:
        raise SystemExit("Состав архива не совпал с ожидаемым по .env")

    # Проверяем то, что реально уехало в архив, а не то, что мы намеревались.
    with tarfile.open(out) as tar:
        crlf = [m.name for m in tar.getmembers()
                if m.isfile() and (Path(m.name).suffix in TEXT_SUFFIX
                                   or Path(m.name).name in TEXT_NAMES)
                and b"\r\n" in tar.extractfile(m).read()]
    if crlf:
        raise SystemExit("В архиве остались файлы с CRLF: " + ", ".join(crlf))

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"архив : {out}")
    print(f"файлов: {len(members)}")
    print(f"размер: {out.stat().st_size / 1024:.0f} КБ")
    print(f"sha256: {digest}")
    print(f".env  : {'включён' if has_env else 'исключён'}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Собрать архив KPlusAgent для macOS")
    ap.add_argument("--with-env", action="store_true",
                    help="положить .env с ключом внутрь (только для личной копии)")
    ap.add_argument("-o", "--out", default=str(ROOT / "dist" / "KPlusAgent-mac.tar.gz"))
    args = ap.parse_args()
    build(args.with_env, Path(args.out))
