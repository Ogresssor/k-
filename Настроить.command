#!/bin/bash
# Двойной щелчок по этому файлу настраивает агента.
cd "$(dirname "$0")" || exit 1

# macOS помечает всё скачанное из интернета «карантином», и тогда двойной
# щелчок по файлу молча не срабатывает — с Sequoia даже правая кнопка
# «Открыть» больше не помогает. Снимаем метку со своей папки и возвращаем
# право на запуск: обе операции касаются только файлов самой программы.
chmod +x ./*.command ./kplus ./run_server.sh 2>/dev/null || true
xattr -dr com.apple.quarantine . 2>/dev/null || true
clear

MIN_MAJOR=3
MIN_MINOR=11
LOCAL_PY="./.python/bin/python3"

# KPLUS_UNATTENDED=1 — запуск без человека (проверка на CI). Скрипт тот же,
# просто вместо вопросов он падает с ошибкой: молчаливого ожидания ввода в
# автоматическом прогоне быть не должно.
pause_and_exit() {
  echo
  if [ -z "${KPLUS_UNATTENDED:-}" ]; then
    read -r -p "Нажмите Enter, чтобы закрыть"
  fi
  exit "${1:-1}"
}

# Годится ли этот интерпретатор: нужная версия и модуль venv на месте.
python_ok() {
  "$1" -c "import sys, venv; sys.exit(0 if sys.version_info >= ($MIN_MAJOR, $MIN_MINOR) else 1)" \
    >/dev/null 2>&1
}

# Ищем уже установленный Python. Заглушку из /usr/bin трогаем только если
# инструменты разработчика Apple реально установлены, иначе она вызовет
# лишнее окно установки.
find_system_python() {
  local cand
  if [ -x "$LOCAL_PY" ] && python_ok "$LOCAL_PY"; then echo "$LOCAL_PY"; return 0; fi
  for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [ -x "$cand" ] && python_ok "$cand"; then echo "$cand"; return 0; fi
  done
  for cand in /Library/Frameworks/Python.framework/Versions/3.*/bin/python3; do
    if [ -x "$cand" ] && python_ok "$cand"; then echo "$cand"; return 0; fi
  done
  if xcode-select -p >/dev/null 2>&1; then
    cand="$(command -v python3 2>/dev/null)"
    if [ -n "$cand" ] && python_ok "$cand"; then echo "$cand"; return 0; fi
  fi
  return 1
}

# Если Python в системе нет — приносим автономную сборку прямо в папку
# программы. Это НЕ установка в macOS: просто распакованный архив в ./.python,
# пароль не нужен, система не меняется. Удаляется вместе с папкой программы.
bootstrap_python() {
  local arch triple api asset tmp
  case "$(uname -m)" in
    arm64) triple="aarch64-apple-darwin" ;;
    x86_64) triple="x86_64-apple-darwin" ;;
    *) echo "Неизвестный процессор $(uname -m)."; return 1 ;;
  esac

  echo "→ Загружаю Python для программы (около 30 МБ, только в первый раз)"

  # Берём последнюю сборку python-build-standalone, не привязываясь к версии:
  # спрашиваем у GitHub ссылку на подходящий архив.
  api="https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
  asset="$(curl -fsSL "$api" 2>/dev/null \
    | grep -o "https://[^\"]*cpython-3\.[0-9.]*+[0-9]*-${triple}-install_only\.tar\.gz" \
    | head -n 1)"

  if [ -z "$asset" ]; then
    echo "  Не удалось узнать адрес загрузки Python (нет интернета или сеть фильтрует GitHub)."
    return 1
  fi

  tmp="$(mktemp -d)"
  if ! curl -fSL --progress-bar "$asset" -o "$tmp/python.tar.gz"; then
    echo "  Не удалось скачать Python. Проверьте интернет."
    rm -rf "$tmp"; return 1
  fi

  rm -rf ./.python
  # В архиве всё лежит в папке python/ — распаковываем и получаем ./.python
  if ! tar -xzf "$tmp/python.tar.gz" -C "$tmp"; then
    echo "  Архив Python повреждён, попробуйте запустить ещё раз."
    rm -rf "$tmp"; return 1
  fi
  mv "$tmp/python" ./.python
  rm -rf "$tmp"

  [ -x "$LOCAL_PY" ] && python_ok "$LOCAL_PY"
}

echo "Подготовка… это займёт несколько минут при первом запуске."
echo

PY="$(find_system_python)"

if [ -z "$PY" ]; then
  # Python в системе нет — приносим свой, без участия пользователя.
  if bootstrap_python && python_ok "$LOCAL_PY"; then
    PY="$LOCAL_PY"
  else
    echo
    echo "Не удалось подготовить Python автоматически."
    echo "Чаще всего причина — нет интернета или корпоративная сеть блокирует GitHub."
    echo "Проверьте подключение и запустите «Настроить» ещё раз."
    pause_and_exit 1
  fi
fi

echo "→ Python готов: $("$PY" -V 2>&1)"

if [ ! -d .venv ]; then
  echo "→ Создаю рабочее окружение"
  if ! "$PY" -m venv .venv; then
    echo
    echo "Не удалось создать рабочее окружение. Запустите «Настроить» ещё раз."
    pause_and_exit 1
  fi
fi

echo "→ Устанавливаю компоненты"
./.venv/bin/python -m pip install --quiet --upgrade pip
if ! ./.venv/bin/python -m pip install --quiet -r requirements.txt; then
  echo
  echo "Не удалось скачать компоненты программы."
  echo "Проверьте интернет и запустите «Настроить» ещё раз."
  echo "Если вы в корпоративной сети, мешать может её фильтр."
  pause_and_exit 1
fi

echo "→ Ищу браузер для работы с К+"
export KPLUS_BROWSER_CHANNEL=""
export KPLUS_BROWSER_PATH=""

# Ищем установленный браузер на движке Chromium. Агент управляет им по порту
# отладки, поэтому годится любой такой браузер и качать ничего не нужно.
# Программа работает в отдельном профиле, ваши вкладки и сессии не трогает.
if [ -d "/Applications/Google Chrome.app" ]; then
  echo "  Нашёл установленный Google Chrome — буду использовать его."
  export KPLUS_BROWSER_CHANNEL="chrome"
elif [ -d "/Applications/Microsoft Edge.app" ]; then
  echo "  Нашёл установленный Microsoft Edge — буду использовать его."
  export KPLUS_BROWSER_CHANNEL="msedge"
elif [ -x "/Applications/Comet.app/Contents/MacOS/Comet" ]; then
  echo "  Нашёл браузер Comet — буду использовать его."
  export KPLUS_BROWSER_PATH="/Applications/Comet.app/Contents/MacOS/Comet"
elif [ -x "/Applications/Arc.app/Contents/MacOS/Arc" ]; then
  echo "  Нашёл браузер Arc — буду использовать его."
  export KPLUS_BROWSER_PATH="/Applications/Arc.app/Contents/MacOS/Arc"
elif [ -x "/Applications/Yandex.app/Contents/MacOS/Yandex" ]; then
  # Яндекс.Браузер идёт последним: его режим Protect с собственным DNS
  # на свежем профиле иногда рвёт соединение с интранет-порталами К+.
  echo "  Нашёл Яндекс Браузер — буду использовать его."
  export KPLUS_BROWSER_PATH="/Applications/Yandex.app/Contents/MacOS/Yandex"
else
  # Своего Chromium мы больше не качаем: агент управляет браузером по порту
  # отладки, и ему нужна настоящая установленная программа, а не сборка в
  # кеше Playwright. Заодно уходит загрузка на 150 МБ с cdn.playwright.dev,
  # которую чаще всего и резала сеть.
  echo
  echo "Не нашёл ни одного браузера на движке Chromium."
  echo "Программе нужен Google Chrome — серверы Google обычно доступны."
  echo
  echo "  1. Сейчас откроется страница google.com/chrome"
  echo "  2. Нажмите «Скачать Chrome», откройте загруженный файл,"
  echo "     перетащите значок Chrome в «Программы»."
  echo "  3. Запустите «Настроить» снова."
  echo
  if [ -z "${KPLUS_UNATTENDED:-}" ]; then
    read -r -p "Открыть страницу загрузки Chrome? (д/н) [д]: " ans
    case "${ans:-д}" in
      д|Д|y|Y|да) open "https://www.google.com/chrome/" ;;
      *) echo "Адрес: https://www.google.com/chrome/" ;;
    esac
  fi
  pause_and_exit 1
fi

if [ -n "${KPLUS_UNATTENDED:-}" ]; then
  echo "→ Автономный режим: мастер настройки пропущен"
  {
    [ -n "$KPLUS_BROWSER_CHANNEL" ] && echo "KPLUS_BROWSER_CHANNEL=$KPLUS_BROWSER_CHANNEL"
    [ -n "$KPLUS_BROWSER_PATH" ] && echo "KPLUS_BROWSER_PATH=$KPLUS_BROWSER_PATH"
  } >> .env
  echo "Готово."
  exit 0
fi

clear
exec ./.venv/bin/python -m kplus_mcp.setup_wizard
