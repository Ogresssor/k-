#!/bin/bash
# Двойной щелчок — и из этой папки собирается настоящее приложение KPlus.app.
#
# Нужно один раз. Приложение остаётся здесь же, в этой папке: ничего
# никуда не устанавливается, в системные и домашние папки программа не
# лезет. Папку можно перенести куда угодно или удалить целиком.
cd "$(dirname "$0")" || exit 1

chmod +x ./*.command ./kplus ./run_server.sh ./tools/*.sh 2>/dev/null || true
xattr -dr com.apple.quarantine . 2>/dev/null || true
clear

B=$'\033[1m'; DIM=$'\033[2m'; OK=$'\033[32m'; WARN=$'\033[33m'; N=$'\033[0m'

pause_and_exit() {
  echo
  read -r -p "Нажмите Enter, чтобы закрыть"
  exit "${1:-1}"
}

step() { echo; echo "${B}$1${N}"; }

echo "${B}Сборка приложения К+${N}"
echo "${DIM}Первый раз это занимает 20–40 минут: качаются инструменты сборки."
echo "Дальше можно заниматься своими делами, окно закрывать не нужно.${N}"

# --- 1. Python и зависимости уже подготовил «Настроить» -------------------------

if [ ! -x ./.venv/bin/python ]; then
  echo
  echo "${WARN}Сначала запустите «Настроить.command» — он рядом, в этой же папке.${N}"
  echo "Он поставит Python и компоненты, без них собирать нечего."
  pause_and_exit 1
fi
echo
echo "→ Python готов: $(./.venv/bin/python -V 2>&1)"

# --- 2. Rust: язык, на котором написана оболочка окна ---------------------------

step "Шаг 1 из 4. Инструменты сборки"

if ! command -v cargo >/dev/null 2>&1; then
  # Официальный установщик Rust. Ставится в домашнюю папку пользователя,
  # прав администратора не просит, систему не трогает.
  echo "  Устанавливаю Rust (около 300 МБ, только в первый раз)…"
  if ! curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path; then
    echo
    echo "${WARN}Не удалось установить Rust.${N} Обычно причина — нет интернета"
    echo "или сеть блокирует sh.rustup.rs."
    pause_and_exit 1
  fi
fi

# rustup ставит cargo сюда; в свежем окне Терминала PATH об этом ещё не знает.
export PATH="$HOME/.cargo/bin:$PATH"

if ! command -v cargo >/dev/null 2>&1; then
  echo "${WARN}Rust установился, но cargo не нашёлся. Перезапустите сборку.${N}"
  pause_and_exit 1
fi
echo "  Rust: $(rustc --version 2>&1)"

# Инструмент сборки Tauri. Ставим через cargo, чтобы не тянуть ещё и Node.
if ! cargo tauri --version >/dev/null 2>&1; then
  echo "  Устанавливаю сборщик Tauri (это самая долгая часть, 10–20 минут)…"
  if ! cargo install tauri-cli --version "^2" --locked; then
    echo
    echo "${WARN}Не удалось установить сборщик Tauri.${N}"
    pause_and_exit 1
  fi
fi
echo "  Tauri: $(cargo tauri --version 2>&1 | tail -1)"

# --- 3. Агент одним файлом ------------------------------------------------------

step "Шаг 2 из 4. Упаковываю агента в один файл"
echo "${DIM}  Весь Python со всеми библиотеками ляжет внутрь программы.${N}"

if ! KPLUS_PYTHON="$(pwd)/.venv/bin/python" bash tools/build_sidecar.sh; then
  echo
  echo "${WARN}Не удалось собрать агента.${N} Подробности выше."
  pause_and_exit 1
fi

# --- 4. Иконка и само приложение ------------------------------------------------

step "Шаг 3 из 4. Иконка"
cargo tauri icon src-tauri/icons/source.png >/dev/null 2>&1 \
  && echo "  Готово." \
  || { echo "${WARN}Не удалось сделать иконки.${N}"; pause_and_exit 1; }

step "Шаг 4 из 4. Собираю приложение"
echo "${DIM}  Первая сборка компилирует оболочку целиком — 10–20 минут.${N}"
if ! cargo tauri build --bundles app; then
  echo
  echo "${WARN}Сборка не удалась.${N} Текст ошибки выше — пришлите его разработчику."
  pause_and_exit 1
fi

APP="$(find src-tauri/target/release/bundle/macos -maxdepth 1 -name '*.app' | head -1)"
if [ -z "$APP" ]; then
  echo "${WARN}Приложение не нашлось после сборки.${N}"
  pause_and_exit 1
fi

# --- 5. Приложение остаётся в этой же папке -------------------------------------
#
# В «Программы» ничего не копируем. Программа переносимая: приложение и все
# его данные лежат в одной папке, которую можно двигать и удалять целиком.

NAME="$(basename "$APP")"
echo
echo "→ Кладу приложение рядом, в эту же папку"
rm -rf "./$NAME"
cp -R "$APP" "./$NAME"
xattr -dr com.apple.quarantine "./$NAME" 2>/dev/null || true

# Промежуточные файлы сборки весят гигабайты и больше не нужны.
echo "→ Убираю мусор после сборки"
rm -rf src-tauri/target build src-tauri/binaries

HERE="$(pwd)"
clear
cat <<TXT
${OK}Приложение собрано.${N}

  ${B}$NAME${N} лежит в этой же папке:
  $HERE

  Запускайте двойным щелчком по нему. Терминал больше не нужен.
  Готовые документы появятся в папке «Документы» рядом с приложением.

${DIM}Папку можно перенести куда угодно — приложение носит свои данные
с собой и ничего не пишет ни в системные, ни в домашние папки.
Удалить программу — просто удалить эту папку.${N}

TXT

read -r -p "Открыть приложение сейчас? (д/н) [д]: " ans
case "${ans:-д}" in
  д|Д|y|Y|да) open "./$NAME" ;;
  *) : ;;
esac
