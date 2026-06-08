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

# link_tree <src-папка-абсолютная> <dst-абсолютный>
# Линкует ВСЮ папку одним symlink'ом (~/.claude/agents -> repo/agents).
# Безопасно мигрирует со старой per-file схемы: если dst — реальная директория,
# содержащая ТОЛЬКО symlink'и в наш репо (или пустая), удаляет её и ставит folder-link.
# Если внутри есть посторонние файлы — не трогает без --force.
link_tree() {
  local src="$1" dst="$2"
  local name; name="$(basename "$dst")"

  if [ ! -d "$src" ]; then
    say "  ! пропуск $name — источник не найден: $src"
    return
  fi

  # Уже корректный folder-symlink на наш источник.
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    say "  = $name/ уже подключён (папка)"
    return
  fi

  # Реальная директория (вероятно старая per-file схема) — проверим содержимое.
  if [ -d "$dst" ] && [ ! -L "$dst" ]; then
    local foreign=0 entry tgt
    shopt -s nullglob dotglob
    for entry in "$dst"/*; do
      if [ -L "$entry" ]; then
        tgt="$(readlink "$entry")"
        case "$tgt" in
          "$REPO_DIR"/*) : ;;        # наш symlink — ок к удалению
          *) foreign=1 ;;             # чужой symlink
        esac
      else
        foreign=1                     # реальный файл/папка — посторонний
      fi
    done
    shopt -u nullglob dotglob

    if [ "$foreign" -eq 0 ]; then
      say "  ~ $name/ — миграция per-file → folder-symlink (старые наши линки удаляются)"
      run "rm -rf \"$dst\""
    elif [ "$FORCE" -eq 1 ]; then
      say "  ~ $name/ содержит посторонние файлы — бэкап в ${name}.bak"
      run "mv \"$dst\" \"${dst}.bak\""
    else
      say "  ! $name/ содержит посторонние файлы — пропуск (используй --force)"
      return
    fi
  elif [ -e "$dst" ] || [ -L "$dst" ]; then
    # Не наш symlink или обычный файл на месте папки.
    if [ "$FORCE" -eq 1 ]; then
      say "  ~ $name существует — бэкап в ${name}.bak"
      run "mv \"$dst\" \"${dst}.bak\""
    else
      say "  ! $name существует и это не наш folder-symlink — пропуск (используй --force)"
      return
    fi
  fi

  say "  + $name/ -> $src"
  run "ln -sfn \"$src\" \"$dst\""
}

say "Репо:        $REPO_DIR"
say "Назначение:  $CLAUDE_DIR"
[ "$DRY_RUN" -eq 1 ] && say "(dry-run: изменения не применяются)"
say ""

# --- Агенты: folder-symlink ~/.claude/agents -> repo/agents ---
# Вся папка одним линком: новые агенты подхватываются без повторного install.sh.
say "Агенты -> $CLAUDE_DIR/agents"
run "mkdir -p \"$CLAUDE_DIR\""
link_tree "$REPO_DIR/agents" "$CLAUDE_DIR/agents"
say ""

# --- Навыки: folder-symlink ~/.claude/skills -> repo/skills ---
say "Навыки -> $CLAUDE_DIR/skills"
run "mkdir -p \"$CLAUDE_DIR\""
link_tree "$REPO_DIR/skills" "$CLAUDE_DIR/skills"
say ""

# --- Hooks: per-file symlink в ~/.claude/hooks/ ---
# Папку НЕ линкуем: тут лежат сторонние хуки (caveman .js и пр.), не из репо.
say "Hooks -> $CLAUDE_DIR/hooks/"
run "mkdir -p \"$CLAUDE_DIR/hooks\""
for f in "$REPO_DIR"/hooks/*.sh; do
  [ -e "$f" ] || continue
  link_item "$f" "$CLAUDE_DIR/hooks/$(basename "$f")"
done
say ""

say "Готово."
say ""
say "Запуск оркестратора:  claude --agent architect"
say "Список агентов:        /agents  (внутри сессии Claude Code)"
say ""
say "Нотификации: hooks/notify.sh подключён в ~/.claude/hooks/, но событийные"
say "хуки регистрируются в ~/.claude/settings.json вручную (см. README, раздел"
say "«Нотификации»). settings.json не в этом репо — не перезаписываем его."
