#!/bin/bash
# Полное удаление К+ со всеми следами.
#
# Нынешняя версия переносимая и живёт в одной папке. Но версии до неё
# писали в пять мест за её пределами, и просто выбросить папку мало:
# остаётся профиль браузера с активным входом в К+, скачанный Chromium
# на пару сотен мегабайт и запись в настройках Claude Desktop.
#
# Скрипт сначала показывает, что нашёл, и только потом спрашивает.
cd "$(dirname "$0")" || exit 1
xattr -dr com.apple.quarantine . 2>/dev/null || true
clear

B=$'\033[1m'; DIM=$'\033[2m'; OK=$'\033[32m'; WARN=$'\033[33m'; N=$'\033[0m'

HERE="$(pwd)"
CLAUDE_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"

# Порт отладки нашего браузера. Берём из настроек, если они рядом, иначе
# значение по умолчанию: обрывать чужие браузеры на соседних портах нельзя.
CDP_PORT="$(sed -n 's/^KPLUS_CDP_PORT=//p' "$HERE/.env" 2>/dev/null | head -1)"
CDP_PORT="${CDP_PORT:-9333}"

# Места, где старые версии оставляли данные. Документы сюда не входят —
# про них спрашиваем отдельно, это работа пользователя.
LEFTOVERS=(
  "$HOME/.kplus-agent|данные и профиль браузера старых версий"
  "$HOME/Library/Logs/KPlusAgent|журнал старых версий"
)

# Общий кеш Playwright. Наша старая версия качала туда Chromium, но папка
# не наша: ею пользуется любая программа на Playwright. Поэтому она не в
# списке выше и удаляется только по отдельному согласию.
SHARED_CACHE="$HOME/Library/Caches/ms-playwright"
DOCS="$HOME/Documents/KPlusAgent"

size_of() {
  [ -e "$1" ] || return 1
  du -sh "$1" 2>/dev/null | cut -f1
}

ask() {
  local answer
  read -r -p "$1 (д/н) [н]: " answer
  case "$answer" in д|Д|y|Y|да|yes) return 0 ;; *) return 1 ;; esac
}

# Гасим только свои процессы. Обычный браузер пользователя не трогаем:
# опознаём по имени наших программ, по порту отладки и по пути к нашему
# профилю — у чужого Chrome ничего этого в командной строке нет.
kill_ours() {
  local pattern="$1" label="$2" pids
  pids="$(pgrep -f "$pattern" 2>/dev/null | grep -v -e "^$$\$" -e "^$PPID\$" || true)"
  [ -z "$pids" ] && return 1
  echo "  ${WARN}закрываю${N}: $label"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 1
  # shellcheck disable=SC2086
  kill -9 $pids 2>/dev/null || true
  return 0
}

echo "${B}Удаление К+${N}"
echo "${DIM}Сначала посмотрим, что вообще есть на этом компьютере.${N}"
echo

# --- что нашлось ---------------------------------------------------------------

echo "${B}Найдено${N}"
FOUND=0

for entry in "${LEFTOVERS[@]}"; do
  path="${entry%%|*}"; label="${entry#*|}"
  if size="$(size_of "$path")"; then
    printf "  %6s  %s\n" "$size" "$label"
    echo "          $path"
    FOUND=1
  fi
done

if [ -d "$HERE/KPlus.app" ] || [ -d "$HERE/.venv" ] || [ -d "$HERE/kplus_mcp" ]; then
  printf "  %6s  сама программа\n" "$(size_of "$HERE")"
  echo "          $HERE"
  FOUND=1
fi

if [ -f "$CLAUDE_CFG" ] && grep -q '"kplus"' "$CLAUDE_CFG" 2>/dev/null; then
  echo "          запись К+ в настройках Claude Desktop"
  FOUND=1
fi

if size="$(size_of "$SHARED_CACHE")"; then
  echo
  printf "  %6s  %sобщий кеш Playwright%s (спрошу отдельно)
" "$size" "$B" "$N"
  echo "          $SHARED_CACHE"
fi

if size="$(size_of "$DOCS")"; then
  echo
  printf "  %6s  %sваши документы%s (спрошу отдельно)\n" "$size" "$B" "$N"
  echo "          $DOCS"
fi

if [ "$FOUND" = 0 ] && [ ! -d "$DOCS" ]; then
  echo "  ${OK}Ничего не найдено — удалять нечего.${N}"
  echo
  read -r -p "Нажмите Enter, чтобы закрыть"
  exit 0
fi

# --- работающие процессы --------------------------------------------------------

echo
echo "${B}Работающие программы${N}"
RUNNING=0
pgrep -f "kplus-core|kplus-shell" > /dev/null 2>&1 && { echo "  К+ запущен"; RUNNING=1; }
pgrep -f "remote-debugging-port=$CDP_PORT([^0-9]|\$)" > /dev/null 2>&1 && { echo "  окно браузера К+ открыто"; RUNNING=1; }
pgrep -f "user-data-dir=.*kplus" > /dev/null 2>&1 && { echo "  браузер со старым профилем К+"; RUNNING=1; }
[ "$RUNNING" = 0 ] && echo "  ${DIM}ничего не запущено${N}"

echo
echo "${DIM}Ваш обычный браузер не трогаем: закрываются только окна с профилем"
echo "К+ и портом отладки. Вкладки и пароли в вашем Chrome останутся.${N}"
echo

if ! ask "Удалить программу и все её следы?"; then
  echo "Ничего не тронуто."
  read -r -p "Нажмите Enter, чтобы закрыть"
  exit 0
fi

# --- закрываем ------------------------------------------------------------------

echo
echo "${B}Закрываю программы${N}"
kill_ours "kplus-core" "агент К+" || true
kill_ours "kplus-shell" "окно К+" || true
kill_ours "remote-debugging-port=$CDP_PORT([^0-9]|\$)" "браузер К+" || true
kill_ours "user-data-dir=.*kplus" "браузер со старым профилем" || true
echo "  ${OK}готово${N}"

# --- удаляем --------------------------------------------------------------------

echo
echo "${B}Удаляю${N}"

for entry in "${LEFTOVERS[@]}"; do
  path="${entry%%|*}"; label="${entry#*|}"
  if [ -n "$path" ] && [ -e "$path" ]; then
    rm -rf "$path" && echo "  ${OK}удалено${N}: $label"
  fi
done

# Запись в настройках Claude Desktop убираем аккуратно: файл общий, там
# могут быть чужие серверы. Копию кладём рядом.
if [ -f "$CLAUDE_CFG" ] && grep -q '"kplus"' "$CLAUDE_CFG" 2>/dev/null; then
  if command -v python3 > /dev/null 2>&1; then
    cp "$CLAUDE_CFG" "$CLAUDE_CFG.before-kplus-removal"
    python3 - "$CLAUDE_CFG" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
servers = data.get("mcpServers") or {}
if servers.pop("kplus", None) is not None:
    if not servers:
        data.pop("mcpServers", None)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("  удалено: запись К+ в настройках Claude Desktop")
PY
  else
    echo "  ${WARN}пропущено${N}: запись в настройках Claude Desktop"
    echo "          уберите вручную строку «kplus» из файла:"
    echo "          $CLAUDE_CFG"
  fi
fi
# Копию конфига не трогаем молча: имя общее, её мог оставить не наш мастер.
BACKUP="$CLAUDE_CFG.backup"
if [ -f "$BACKUP" ]; then
  echo
  echo "  Рядом лежит копия настроек: $BACKUP"
  if ask "  Удалить и её?"; then
    rm -f "$BACKUP" && echo "  ${OK}удалено${N}"
  else
    echo "  оставлена"
  fi
fi

# --- общий кеш Playwright -------------------------------------------------------

if [ -d "$SHARED_CACHE" ]; then
  echo
  echo "${B}Общий кеш Playwright${N}"
  echo "  $SHARED_CACHE ($(size_of "$SHARED_CACHE"))"
  echo "${DIM}  Туда старая версия К+ качала свой Chromium. Папка общая: если"
  echo "  на этом компьютере есть другие программы на Playwright, они"
  echo "  пользуются ей же. Не уверены — оставьте, места она просит немного.${N}"
  if ask "  Удалить общий кеш?"; then
    rm -rf "$SHARED_CACHE" && echo "  ${OK}удалено${N}"
  else
    echo "  оставлен"
  fi
fi

# --- документы ------------------------------------------------------------------

if [ -n "$HOME" ] && [ -d "$DOCS" ]; then
  echo
  echo "${B}Ваши документы${N}"
  echo "  $DOCS ($(size_of "$DOCS"))"
  echo "${DIM}  Это то, что программа для вас составила. Обычно их оставляют.${N}"
  if ask "  Удалить и документы тоже?"; then
    rm -rf "$DOCS" && echo "  ${OK}удалено${N}"
  else
    echo "  оставлены"
  fi
fi

# --- сама папка -----------------------------------------------------------------

# Удалять целую папку по переменной — самое опасное место в скрипте.
# Соглашаемся только если это точно папка программы: не корень, не
# домашняя папка, и внутри лежит что-то наше.
folder_is_ours() {
  case "$HERE" in
    "" | "/" | "$HOME" | "$HOME/") return 1 ;;
    "$HOME/Desktop" | "$HOME/Downloads" | "$HOME/Documents") return 1 ;;
    "$HOME/Applications" | "/Applications") return 1 ;;
  esac
  [ -d "$HERE/KPlus.app" ] || [ -d "$HERE/kplus_mcp" ] || [ -f "$HERE/requirements.txt" ]
}

echo
if ! folder_is_ours; then
  echo "${WARN}Папку не удаляю:${N} $HERE не похожа на папку программы."
  echo "${DIM}Выбросьте её в корзину сами, если она больше не нужна.${N}"
elif ask "Удалить и саму папку программы ($HERE)?"; then
  cd "$HOME" || exit 1
  rm -rf "$HERE"
  clear
  echo
  echo "${OK}К+ удалён полностью.${N} От программы не осталось ничего."
  echo
  exit 0
fi

clear
echo
echo "${OK}Следы удалены.${N}"
echo
echo "Папка программы осталась на месте:"
echo "  $HERE"
echo "${DIM}Её можно выбросить в корзину — больше нигде ничего не хранится.${N}"
echo
read -r -p "Нажмите Enter, чтобы закрыть"
