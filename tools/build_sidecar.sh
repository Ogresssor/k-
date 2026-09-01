#!/usr/bin/env bash
# Собрать агент в один исполняемый файл, который Tauri положит внутрь .app.
#
# После этого у пользователя не остаётся ни pip, ни venv, ни установки
# Python: всё уже внутри программы и подписано вместе с ней.
set -euo pipefail
cd "$(dirname "$0")/.."

# Tauri ищет сайдкар по имени с суффиксом целевой платформы.
TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
OUT="src-tauri/binaries"

# На машине пользователя это ./.venv, на CI — системный python3.
PY="${KPLUS_PYTHON:-python3}"

"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt pyinstaller

# Отметка сборки: по ней видно, что именно запущено у пользователя.
STAMP="$(git rev-parse --short HEAD 2>/dev/null || echo неизвестно)"
git diff --quiet 2>/dev/null || STAMP="$STAMP+правки"
STAMP="$STAMP от $(date +%Y-%m-%d)"
printf '"""Отметка сборки, вписана при упаковке."""
VERSION = "%s"
' "$STAMP" \n  > kplus_mcp/_build.py
echo "  отметка сборки: $STAMP"

rm -rf build "$OUT"
mkdir -p "$OUT"

"$PY" -m PyInstaller \
  --noconfirm --clean --onefile \
  --name kplus-core \
  --distpath "$OUT" \
  --workpath build/pyinstaller \
  --specpath build \
  --collect-all playwright \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  tools/sidecar_entry.py

mv "$OUT/kplus-core" "$OUT/kplus-core-$TRIPLE"
chmod +x "$OUT/kplus-core-$TRIPLE"
echo "готово: $OUT/kplus-core-$TRIPLE"
ls -lh "$OUT"
