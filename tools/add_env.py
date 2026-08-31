"""Вложить свой .env в готовый архив приложения, собранный на CI.

CI собирает приложение на маке и отдаёт архив KPlusAgent-portable.tar.gz.
Ключа там нет и быть не должно: CI — чужая машина. Этот скрипт кладёт ваш
.env внутрь, ничего больше не трогая.

    python tools/add_env.py ~/Downloads/KPlusAgent-portable.tar.gz

Получится KPlusAgent-mac.tar.gz — его и отправляют.

Почему не просто «распаковать и перепаковать»: права на исполнение внутри
.app обязаны уцелеть, иначе приложение не запустится. Работаем с архивом
напрямую, переписывая только оглавление, — тогда режимы файлов остаются
ровно теми, что записал мак.
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


def _mode_for(path: Path) -> int:
    return 0o755 if path.suffix == ".command" else 0o644


def repack(source: Path, env: Path, out: Path, extra: list[Path] | None = None) -> Path:
    if not source.is_file():
        raise SystemExit(f"Не нашёл архив: {source}")
    if not env.is_file():
        raise SystemExit(f"Не нашёл файл с ключом: {env}")

    env_data = env.read_bytes().replace(b"\r\n", b"\n")

    with tarfile.open(source) as src:
        members = src.getmembers()
        top = {m.name.split("/", 1)[0] for m in members}
        if len(top) != 1:
            raise SystemExit(f"Ожидал одну папку в архиве, нашёл: {sorted(top)}")
        folder = top.pop()

        with tarfile.open(out, "w:gz", format=tarfile.GNU_FORMAT, encoding="utf-8") as dst:
            executables = 0
            replaced = {f"{folder}/.env"} | {f"{folder}/{i.name}" for i in extra or []}
            for m in members:
                if m.name in replaced:
                    continue  # эти файлы кладём сами, ниже
                if m.mode & 0o111 and m.isfile():
                    executables += 1
                dst.addfile(m, src.extractfile(m) if m.isfile() else None)

            info = tarfile.TarInfo(f"{folder}/.env")
            info.size = len(env_data)
            info.mode = 0o600          # ключ читает только владелец
            info.mtime = int(time.time())
            dst.addfile(info, io.BytesIO(env_data))

            # Текстовые файлы рядом с приложением — скрипт удаления,
            # инструкция — можно обновить без пересборки .app: они не
            # компилируются и ни от чего не зависят.
            for item in extra or []:
                data = item.read_bytes().replace(b"\r\n", b"\n")
                add = tarfile.TarInfo(f"{folder}/{item.name}")
                add.size = len(data)
                add.mode = _mode_for(item)
                add.mtime = int(time.time())
                dst.addfile(add, io.BytesIO(data))

    with tarfile.open(out) as check:
        names = check.getnames()
        app = [n for n in names if "/Contents/MacOS/" in n]
        has_env = f"{folder}/.env" in names

    if not has_env:
        raise SystemExit("Ключ не попал в архив")
    if not app:
        raise SystemExit("В архиве нет приложения — проверьте, тот ли файл вы скачали")
    if not executables:
        raise SystemExit(
            "В исходном архиве нет ни одного исполняемого файла. "
            "Похоже, скачан zip от upload-artifact, а не сам tar.gz из него: "
            "zip теряет права, и приложение не запустится."
        )

    print(f"архив : {out}")
    print(f"файлов: {len(names)}")
    print(f"размер: {out.stat().st_size / 1024 / 1024:.0f} МБ")
    print(f"sha256: {hashlib.sha256(out.read_bytes()).hexdigest()}")
    print(f"ключ  : вложен, режим 600")
    print(f"исполняемых файлов внутри .app: {executables}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Вложить .env в архив приложения")
    ap.add_argument("source", help="KPlusAgent-portable.tar.gz, скачанный с CI")
    ap.add_argument("--env", default=str(ROOT / ".env"), help="файл с ключом")
    ap.add_argument("-o", "--out", default=str(ROOT / "dist" / "KPlusAgent-mac.tar.gz"))
    ap.add_argument("--with", dest="extra", nargs="*", default=[],
                    help="добавить или заменить файлы рядом с приложением")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    repack(Path(args.source), Path(args.env), out,
           [Path(f) for f in args.extra])
