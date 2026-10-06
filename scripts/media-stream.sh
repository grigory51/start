#!/usr/bin/env bash
# Запускается из раздела «Команды» или вручную с путём к папке первым аргументом.
set -euo pipefail

if [ "$#" -gt 1 ]; then
    printf 'Использование: bash %s [папка]\n' "$0" >&2
    exit 2
fi

if ! command -v rclone >/dev/null 2>&1; then
    printf 'Нужен rclone: macOS — brew install rclone; Linux — пакет rclone из менеджера пакетов.\n' >&2
    exit 1
fi

# Обёртка start переходит в репозиторий; пути папок считаются от места вызова.
cd -- "${START_INVOKE_DIR:-$PWD}"

directory="${1:-}"
if [ "$#" -eq 0 ]; then
    IFS= read -erp "Папка с видео (Enter — $PWD): " directory || exit 0
fi
directory="${directory:-$PWD}"

# В интерактивном вводе shell не разворачивает домашний каталог автоматически.
case "$directory" in
    '~') directory="$HOME" ;;
    '~/'*) directory="$HOME/${directory:2}" ;;
esac

if [ ! -d "$directory" ] || [ ! -r "$directory" ] || [ ! -x "$directory" ]; then
    printf 'Папка не существует или недоступна для чтения: %s\n' "$directory" >&2
    exit 1
fi
directory="$(cd -- "$directory" && pwd -P)"

printf 'Папка: %s\n' "$directory"
printf 'На ТВ: VLC → Локальная сеть → start Media.\n'
printf 'Запускаю DLNA-раздачу; Ctrl+C — остановить.\n\n'

exec rclone serve dlna "$directory" --name 'start Media' --read-only
