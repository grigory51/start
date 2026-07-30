#!/usr/bin/env bash
#
# notify.sh — macOS-нотификация, когда Claude Code ждёт пользователя или закончил ход.
#
# Регистрируется на события Notification (idle_prompt / permission_prompt) и Stop
# в ~/.claude/settings.json (через [[hooks]] в config.toml менеджера). JSON события
# приходит на stdin.
#
# Доставка: terminal-notifier (группировка per-agent), с фолбэком на osascript,
# если terminal-notifier не установлен.
#
# Про иконку: кастомную иконку Claude дать не вышло — левую иконку определяет
# bundle отправителя, а из-под cmux terminal-notifier показывает иконку cmux
# (`-sender` игнорируется/виснет, `-contentImage` рисует лишнюю картинку справа).
# Оставлена дефолтная иконка терминала.
#
# Группировка: каждый агент (по cwd) — своя -group. Новое уведомление того же агента
# ЗАМЕНЯЕТ предыдущее (не плодит дубли), разные агенты висят отдельно — видно, кто ждёт.
# Чтобы уведомления НЕ исчезали сами (висели как очередь, пока не кликнешь):
#   System Settings → Notifications → Claude → стиль «Alerts» (а не «Banners»).
#
# Всегда exit 0 — hook не должен ломать сессию.
#
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

# --- разбор полей события (jq если есть) ---
event=""; ntype=""; message=""; cwd=""
if command -v jq >/dev/null 2>&1 && [ -n "$payload" ]; then
  event="$(printf '%s'   "$payload" | jq -r '.hook_event_name // empty' 2>/dev/null)"
  ntype="$(printf '%s'   "$payload" | jq -r '.notification_type // empty' 2>/dev/null)"
  message="$(printf '%s' "$payload" | jq -r '.message // empty' 2>/dev/null)"
  cwd="$(printf '%s'     "$payload" | jq -r '.cwd // empty' 2>/dev/null)"
fi

# --- заголовок/текст по событию ---
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

# --- подзаголовок: путь агента от ~ (или полный, если вне ~) ---
subtitle=""
if [ -n "$cwd" ]; then
  case "$cwd" in
    "$HOME")    subtitle="~" ;;
    "$HOME"/*)  subtitle="~${cwd#"$HOME"}" ;;   # внутри ~ → ~/rel/path
    *)          subtitle="$cwd" ;;               # вне ~ → весь путь
  esac
fi

# --- группа per-agent (по cwd): новое заменяет старое того же агента ---
group="claude-code"
if [ -n "$cwd" ]; then
  if command -v md5 >/dev/null 2>&1; then
    group="claude-$(printf '%s' "$cwd" | md5 -q | cut -c1-12)"
  else
    group="claude-$(printf '%s' "$cwd" | cksum | cut -d' ' -f1)"
  fi
fi

# обрезка длинного message
message="$(printf '%s' "$message" | cut -c1-180)"

# --- доставка ---
if command -v terminal-notifier >/dev/null 2>&1; then
  args=( -title "$title" -message "$message" -group "$group" )
  [ -n "$subtitle" ] && args+=( -subtitle "$subtitle" )
  terminal-notifier "${args[@]}" >/dev/null 2>&1 || true
elif command -v osascript >/dev/null 2>&1; then
  # фолбэк: без иконки/группировки. Экранируем кавычки/бэкслеши.
  esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
  m="$(esc "$message")"; t="$(esc "$title")"; s="$(esc "$subtitle")"
  if [ -n "$s" ]; then
    osascript -e "display notification \"$m\" with title \"$t\" subtitle \"$s\"" >/dev/null 2>&1 || true
  else
    osascript -e "display notification \"$m\" with title \"$t\"" >/dev/null 2>&1 || true
  fi
fi

exit 0
