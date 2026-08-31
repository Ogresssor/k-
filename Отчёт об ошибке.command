#!/bin/bash
# Собирает файл с описанием проблемы, который можно переслать разработчику.
cd "$(dirname "$0")" || exit 1

# macOS помечает всё скачанное из интернета «карантином», и тогда двойной
# щелчок по файлу молча не срабатывает — с Sequoia даже правая кнопка
# «Открыть» больше не помогает. Снимаем метку со своей папки и возвращаем
# право на запуск: обе операции касаются только файлов самой программы.
chmod +x ./*.command ./kplus ./run_server.sh 2>/dev/null || true
xattr -dr com.apple.quarantine . 2>/dev/null || true
clear
echo "Собираю сведения о неполадке…"
echo

if [ -d .venv ]; then
  ./.venv/bin/python -m kplus_mcp.report
else
  python3 -m kplus_mcp.report 2>/dev/null || {
    echo "Программа ещё не установлена — запустите «Настроить.command»."
  }
fi

echo
read -r -p "Нажмите Enter, чтобы закрыть"
