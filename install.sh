#!/usr/bin/env bash
#
# install.sh — подключает персональных агентов и навыки из этого репо
# в ~/.claude/ через per-item symlink'и (идемпотентно).
#
# Использование:
#   ./install.sh            # создать/обновить symlink'и
#   ./install.sh --dry-run  # показать, что было бы сделано, без изменений
#   ./install.sh --force    # перезаписать существующие НЕ-symlink файлы (с бэкапом)
#
set -euo pipefail

# Корень репо = папка, где лежит этот скрипт (работает из любого cwd).
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_HOME:-$HOME/.claude}"

DRY_RUN=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --force)   FORCE=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -12
      exit 0 ;;
    *) echo "Неизвестный аргумент: $arg" >&2; exit 1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
run()  { if [ "$DRY_RUN" -eq 1 ]; then say "  [dry-run] $*"; else eval "$*"; fi; }

# link_item <src-абсолютный> <dst-абсолютный>
link_item() {
  local src="$1" dst="$2"
  local name; name="$(basename "$dst")"

  if [ ! -e "$src" ]; then
    say "  ! пропуск $name — источник не найден: $src"
    return
  fi

  # Уже корректный symlink на наш источник — ничего не делаем.
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    say "  = $name уже подключён"
    return
  fi

  # Существует и это НЕ наш symlink.
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if [ "$FORCE" -eq 1 ]; then
      say "  ~ $name существует — бэкап в ${name}.bak и замена"
      run "mv \"$dst\" \"${dst}.bak\""
    else
      say "  ! $name уже существует и это не наш symlink — пропуск (используй --force)"
      return
    fi
  fi

  say "  + $name -> $src"
  run "ln -sfn \"$src\" \"$dst\""
}

say "Репо:        $REPO_DIR"
say "Назначение:  $CLAUDE_DIR"
[ "$DRY_RUN" -eq 1 ] && say "(dry-run: изменения не применяются)"
say ""

# --- Агенты: per-file symlink в ~/.claude/agents/ ---
say "Агенты -> $CLAUDE_DIR/agents/"
run "mkdir -p \"$CLAUDE_DIR/agents\""
for f in "$REPO_DIR"/agents/*.md; do
  [ -e "$f" ] || continue
  link_item "$f" "$CLAUDE_DIR/agents/$(basename "$f")"
done
say ""

# --- Навыки: per-dir symlink в ~/.claude/skills/ ---
say "Навыки -> $CLAUDE_DIR/skills/"
run "mkdir -p \"$CLAUDE_DIR/skills\""
for d in "$REPO_DIR"/skills/*/; do
  [ -d "$d" ] || continue
  d="${d%/}"
  link_item "$d" "$CLAUDE_DIR/skills/$(basename "$d")"
done
say ""

say "Готово."
say ""
say "Запуск оркестратора:  claude --agent architect"
say "Список агентов:        /agents  (внутри сессии Claude Code)"
