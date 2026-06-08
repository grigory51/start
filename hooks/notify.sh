#!/usr/bin/env bash
#
# notify.sh — macOS-баннер, когда Claude Code ждёт пользователя или закончил ход.
#
# Регистрируется как hook на события Notification (idle_prompt / permission_prompt)
# и Stop в ~/.claude/settings.json. Получает JSON события на stdin.
#
# Канал: macOS Notification Center через osascript, БЕЗ звука.
# Всегда завершается с кодом 0 — hook не должен ломать сессию.
#
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

# Достаём поля. jq если есть, иначе фолбэк на фиксированный текст.
event=""; ntype=""; message=""; cwd=""
if command -v jq >/dev/null 2>&1 && [ -n "$payload" ]; then
  event="$(printf '%s' "$payload"   | jq -r '.hook_event_name // empty' 2>/dev/null)"
  ntype="$(printf '%s' "$payload"   | jq -r '.notification_type // empty' 2>/dev/null)"
  message="$(printf '%s' "$payload" | jq -r '.message // empty' 2>/dev/null)"
  cwd="$(printf '%s' "$payload"     | jq -r '.cwd // empty' 2>/dev/null)"
fi

# Заголовок и текст по типу события.
title="Claude Code"
case "$event" in
  Stop)
    title="Claude закончил ход"
    [ -z "$message" ] && message="Готов к твоему вводу" ;;
  Notification|*)
    case "$ntype" in
      idle_prompt)       title="Claude ждёт ввода" ;;
      permission_prompt) title="Claude просит разрешение" ;;
      *)                 title="Claude ждёт тебя" ;;
    esac
    [ -z "$message" ] && message="Нужно твоё действие" ;;
esac

# Подзаголовок — имя проекта (basename cwd), чтобы различать окна/сессии.
subtitle=""
[ -n "$cwd" ] && subtitle="$(basename "$cwd")"

# Экранируем двойные кавычки и бэкслеши для безопасной вставки в AppleScript-строку.
esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
message="$(esc "$message")"
title="$(esc "$title")"
subtitle="$(esc "$subtitle")"

# Обрезаем длинный message (баннер всё равно урежет; держим разумно).
message="$(printf '%s' "$message" | cut -c1-180)"

if command -v osascript >/dev/null 2>&1; then
  if [ -n "$subtitle" ]; then
    osascript -e "display notification \"$message\" with title \"$title\" subtitle \"$subtitle\"" >/dev/null 2>&1 || true
  else
    osascript -e "display notification \"$message\" with title \"$title\"" >/dev/null 2>&1 || true
  fi
fi

exit 0
