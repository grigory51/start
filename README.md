# claude-agents

Персональный, переносимый между проектами набор агентов для [Claude Code](https://code.claude.com), кодирующий мой стиль, идеи и подходы. Один репозиторий → подключается на любой машине через symlink в `~/.claude/`.

## Идея

Корневой агент **architect** запускается как сессия (`claude --agent architect`) и оркестрирует специализированных worker-агентов по циклу **design → implement → review → fix**. Все агенты разделяют общий навык `my-principles` — единый «мозг стиля».

```
        claude --agent architect   (КОРНЕВОЙ агент сессии)
                    │
       проектирует, ведёт TodoView-план, делегирует:
                    │
   ┌──────────┬─────┴──────┬──────────────┐
programmer  reviewer    designer      tech-writer
 (код)     (аудит)     (UI/UX)       (доки)
                    │
        цикл review→fix пока чисто (макс 3 итерации)
                    │
            финальный отчёт пользователю
```

> **Почему именно так.** Subagent не может вызывать другой subagent. НО агент, запущенный как **корневая сессия** через `claude --agent`, спавнить subagent'ов МОЖЕТ — через `tools: Agent(...)`. Поэтому оркестрация нативна и не требует workflow-скриптов.

## Состав

| Агент | Роль | Доступ |
|---|---|---|
| `architect` | Проектирует и оркеструет. Код руками не пишет. | спавн worker'ов + read-only + Bash |
| `programmer` | Реализует/правит код по блюпринту. | Read/Write/Edit/Bash/Grep/Glob |
| `reviewer` | Read-only аудит диффа (баги, безопасность, стиль). | Read/Grep/Glob/Bash |
| `designer` | UI/UX, frontend. | Read/Write/Edit/Bash/Grep/Glob |
| `tech-writer` | README, доки, докстринги, комменты. | Read/Write/Edit/Grep/Glob |

Навык `skills/my-principles/` — личные принципы/стиль, грузится во всех агентов через `skills:` frontmatter.

## Установка

```bash
git clone <repo-url> ~/PersonalWorkspace/claude-agents
cd ~/PersonalWorkspace/claude-agents
./install.sh
```

`install.sh` создаёт **per-item symlink'и** в `~/.claude/`:
- `~/.claude/agents/<agent>.md` → файлы этого репо
- `~/.claude/skills/my-principles` → навык этого репо
- `~/.claude/hooks/notify.sh` → скрипт нотификаций (см. раздел «Нотификации»)

Per-item (а не symlink всей папки) — чтобы не конфликтовать с plugin-агентами и прочим в `~/.claude/`.

Флаги:
- `./install.sh --dry-run` — показать действия без изменений
- `./install.sh --force` — перезаписать существующие НЕ-symlink файлы (с бэкапом `.bak`)
- `CLAUDE_HOME=/custom/path ./install.sh` — другой каталог Claude

## Использование

```bash
# запустить оркестратора на задаче
claude --agent architect

# затем в сессии описать задачу, напр.:
#   "Добавь endpoint /health с тестом"
# architect спроектирует, делегирует programmer, прогонит reviewer, отчитается.
```

Worker-агентов можно звать и вручную в обычной сессии: `@programmer ...`, `@reviewer ...`.

Проверить, что агенты подключены: внутри сессии `/agents`.

## Нотификации (когда агент ждёт тебя)

macOS-баннер прилетает, когда:
- агент **ждёт ввода** (простой сессии, `idle_prompt`);
- агент **просит разрешение** на действие (`permission_prompt`);
- главный агент **закончил ход** (`Stop`) — удобно для долгих architect-циклов.

Канал — macOS Notification Center (`osascript`), **без звука**. Не пересекается с `agentPushNotifEnabled` (это mobile push на телефон — отдельный канал).

**Подключение** (раз на машину):

1. `./install.sh` линкует `hooks/notify.sh` в `~/.claude/hooks/`.
2. Зарегистрировать события в `~/.claude/settings.json` (файл НЕ в этом репо — правится вручную). В существующий объект `hooks` добавить ключи:

```json
"Notification": [
  { "matcher": "idle_prompt|permission_prompt",
    "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/notify.sh\"", "timeout": 5 } ] }
],
"Stop": [
  { "hooks": [ { "type": "command", "command": "bash \"$HOME/.claude/hooks/notify.sh\"", "timeout": 5 } ] }
]
```

> Не перезаписывай весь `hooks` — **добавь** эти два ключа к тем, что уже есть.

**Настройка:**
- Убрать уведомление о завершении хода → удали ключ `"Stop"`.
- Добавить звук → в `hooks/notify.sh` допиши в `osascript` `... sound name "Glass"`.
- Альтернатива без скрипта → встроенный `preferredNotifChannel` в `~/.claude/settings.json` (`"auto"` — баннер в Ghostty/Kitty/iTerm2; `"terminal_bell"` — звонок). Покрывает done+permission, но без гибкости этого хука.

## Кастомизация

- **Свой стиль** → правь `skills/my-principles/SKILL.md` (общий для всех).
- **Тюнинг конкретного агента** → правь тело его `.md` в `agents/`.
- **Новый агент** → добавь `agents/<name>.md`, при необходимости впиши его в `tools: Agent(...)` архитектора, перезапусти `./install.sh`.

Изменения попадают сразу (symlink) — `install.sh` повторно нужен только для новых файлов.

## Обновление на другой машине

```bash
cd ~/PersonalWorkspace/claude-agents && git pull && ./install.sh
```
