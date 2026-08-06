#!/usr/bin/env bash
#
# notify.sh — macOS-уведомления для Claude Code и Codex.
#
# Менеджер передаёт платформу и событие через START_AI_PLATFORM/START_AI_EVENT.
# JSON события приходит на stdin и используется для текста и рабочей директории.
#
# Доставка: terminal-notifier (группировка per-agent), с фолбэком на osascript,
# если terminal-notifier не установлен.
#
# Группировка: каждый агент (по cwd) — своя -group. Новое уведомление того же агента
# заменяет предыдущее; уведомления разных агентов остаются независимыми.
#
# Hook всегда завершается успешно, чтобы сбой доставки не влиял на сессию.
#
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
platform="${START_AI_PLATFORM:-}"
event="${START_AI_EVENT:-}"

# Поля payload различаются между платформами, общие поля читаются первыми.
ntype=""; message=""; cwd=""
if command -v jq >/dev/null 2>&1 && [ -n "$payload" ]; then
  [ -n "$event" ] || event="$(printf '%s' "$payload" | jq -r '.hook_event_name // empty' 2>/dev/null)"
  ntype="$(printf '%s'   "$payload" | jq -r '.notification_type // empty' 2>/dev/null)"
  message="$(printf '%s' "$payload" | jq -r '.message // .tool_input.description // empty' 2>/dev/null)"
  cwd="$(printf '%s'     "$payload" | jq -r '.cwd // empty' 2>/dev/null)"
  if [ -z "$platform" ]; then
    if printf '%s' "$payload" | jq -e 'has("turn_id") or has("model")' >/dev/null 2>&1; then
      platform="codex"
    else
      platform="claude"
    fi
  fi
fi

# PermissionRequest срабатывает до auto-review и не содержит его результат.
# Уведомление поэтому всегда преждевременно и не означает, что нужен ввод пользователя.
if [ "$platform" = "codex" ] && [ "$event" = "PermissionRequest" ]; then
  exit 0
fi

case "$platform" in
  codex)
    product="Codex"
    group_prefix="codex" ;;
  claude)
    product="Claude"
    group_prefix="claude" ;;
  *)
    product="AI"
    group_prefix="ai" ;;
esac

case "$event" in
  Stop)
    title="$product закончил ход"
    [ -z "$message" ] && message="Готов к твоему вводу" ;;
  PermissionRequest)
    title="$product просит разрешение"
    [ -z "$message" ] && message="Нужно твоё разрешение" ;;
  Notification)
    case "$ntype" in
      idle_prompt)       title="$product ждёт ввода" ;;
      permission_prompt) title="$product просит разрешение" ;;
      *)                 title="$product ждёт тебя" ;;
    esac
    [ -z "$message" ] && message="Нужно твоё действие" ;;
  *)
    title="$product ждёт тебя"
    [ -z "$message" ] && message="Нужно твоё действие" ;;
esac

subtitle=""
if [ -n "$cwd" ]; then
  case "$cwd" in
    "$HOME")    subtitle="~" ;;
    "$HOME"/*)  subtitle="~${cwd#"$HOME"}" ;;
    *)          subtitle="$cwd" ;;
  esac
fi

group="$group_prefix-code"
if [ -n "$cwd" ]; then
  if command -v md5 >/dev/null 2>&1; then
    group="$group_prefix-$(printf '%s' "$cwd" | md5 -q | cut -c1-12)"
  else
    group="$group_prefix-$(printf '%s' "$cwd" | cksum | cut -d' ' -f1)"
  fi
fi

message="$(printf '%s' "$message" | cut -c1-180)"

if command -v terminal-notifier >/dev/null 2>&1; then
  args=( -title "$title" -message "$message" -group "$group" )
  [ -n "$subtitle" ] && args+=( -subtitle "$subtitle" )
  terminal-notifier "${args[@]}" >/dev/null 2>&1 || true
elif command -v osascript >/dev/null 2>&1; then
  esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
  m="$(esc "$message")"; t="$(esc "$title")"; s="$(esc "$subtitle")"
  if [ -n "$s" ]; then
    osascript -e "display notification \"$m\" with title \"$t\" subtitle \"$s\"" >/dev/null 2>&1 || true
  else
    osascript -e "display notification \"$m\" with title \"$t\"" >/dev/null 2>&1 || true
  fi
fi

exit 0
