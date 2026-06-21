#!/usr/bin/env bash
#
# focus-track.sh — отмечает время каждого промпта, чтобы статусбар мог показать
# «focus» — длительность текущей непрерывной серии работы (анти-залипание).
#
# Регистрируется на событие UserPromptSubmit в ~/.claude/settings.json (через
# [[hooks]] в config.toml менеджера). JSON события приходит на stdin, но не нужен:
# время берётся из системных часов (в событии UserPromptSubmit поля времени нет).
#
# Состояние — плоский TSV "<start>\t<last>\n" в ~/.claude/state/focus.json, где
# start/last — unix-секунды начала серии и последнего промпта. Формат плоский (не
# JSON), чтобы запись не зависела от jq: hook должен писать безусловно.
#
# Логика серии: разрыв между промптами больше GAP считается перерывом (пользователь
# отошёл) — серия начинается заново. Иначе серия продолжается, обновляется только last.
# GAP должен совпадать с порогом протухания в statusline.mjs (focusElapsed).
#
# Всегда exit 0 — hook не должен ронять сессию.
#
set -uo pipefail

GAP=600  # секунд; разрыв больше → новая серия (= перерыв)

cat >/dev/null 2>&1 || true  # stdin не нужен, но вычитываем, чтобы не оборвать пайп

dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/state"
file="$dir/focus.json"
mkdir -p "$dir" 2>/dev/null || true

now="$(date +%s)"

start="$now"; last=""
if [ -r "$file" ]; then
  IFS=$'\t' read -r prev_start prev_last < "$file" 2>/dev/null || true
  last="${prev_last:-}"
  # Серия жива, только если прошлый промпт не старше GAP.
  if [ -n "$last" ] && [ $((now - last)) -le "$GAP" ]; then
    start="${prev_start:-$now}"
  fi
fi

printf '%s\t%s\n' "$start" "$now" > "$file" 2>/dev/null || true

exit 0
