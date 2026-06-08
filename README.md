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

## Кастомизация

- **Свой стиль** → правь `skills/my-principles/SKILL.md` (общий для всех).
- **Тюнинг конкретного агента** → правь тело его `.md` в `agents/`.
- **Новый агент** → добавь `agents/<name>.md`, при необходимости впиши его в `tools: Agent(...)` архитектора, перезапусти `./install.sh`.

Изменения попадают сразу (symlink) — `install.sh` повторно нужен только для новых файлов.

## Обновление на другой машине

```bash
cd ~/PersonalWorkspace/claude-agents && git pull && ./install.sh
```
