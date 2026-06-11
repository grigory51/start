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
git clone <repo-url> ~/claude-agents
cd ~/claude-agents
make up
```

`make up` обновляет сабмодули (`contrib/`) и запускает `install.py`. Цели Makefile:

| Цель | Что |
|---|---|
| `make up` | `git submodule update --init --recursive` + `./install.py` — полная синхронизация |
| `make install` | только symlink'и (сабмодули не трогает) |
| `make dry` | план без изменений (`--dry-run`) |
| `make force` | перезаписать чужие файлы/симлинки (`--force`, бэкап `.bak`) |

`install.py` (Python 3.11+, только stdlib) создаёт symlink'и в `~/.claude/`:
- `~/.claude/agents` → `repo/agents` (вся папка одним линком)
- `~/.claude/skills/` — **реальная папка** с per-skill симлинками:
  - `repo/skills/*` — линкуются всегда;
  - `contrib/<сабмодуль>/<skill>` — по `skills.toml`.
- `~/.claude/hooks/notify.sh` → `repo/hooks/notify.sh` (per-file)

`agents` линкуется целиком — новый агент подхватывается сразу. `skills` линкуется **по-скилу**, чтобы в одну папку `~/.claude/skills/` сходились скилы из репо и из внешних сабмодулей `contrib/` одновременно (folder-link так не умеет). `hooks` линкуется по-файлу: рядом лежат сторонние хуки (напр. caveman), папку перекрывать нельзя.

Старую схему (`~/.claude/skills` как folder-symlink) `install.py` мигрирует автоматически: снимает линк, ставит реальную папку, раскладывает per-skill симлинки. Скил, выпавший из `skills.toml`, его симлинк удаляется. Посторонние файлы/симлинки не трогает без `--force`.

Флаги:
- `./install.py --dry-run` — показать действия без изменений
- `./install.py --force` — перезаписать чужие файлы/симлинки (с бэкапом `.bak`)
- `CLAUDE_HOME=/custom/path ./install.py` — другой каталог Claude

### Внешние скилы: `contrib/` + `skills.toml`

Внешние наборы скилов подключаются git-сабмодулями в `contrib/`, а `skills.toml` задаёт, что из них линковать:

```toml
[[source]]
path = "contrib/blender-skills"   # папка-источник (относительно корня репо)
include = "*"                      # "*" = все скилы, либо список ["crane-shot", ...]
# exclude = ["threejs-export"]     # что исключить поверх include
```

Скил = подкаталог с `SKILL.md`; его имя в `~/.claude/skills/` = имя папки. Конфликт имён между источниками — ошибка установки (берётся первое вхождение, остальное пропускается с предупреждением).

Добавить новый набор:

```bash
git submodule add <url> contrib/<name>
# впиши [[source]] в skills.toml
make up
```

Локальный overlay `skills.local.toml` (в `.gitignore`) переопределяет `skills.toml` без правки версионного файла — удобно на форке/конкретной машине:

```toml
# skills.local.toml
[[source]]
path = "contrib/blender-skills"
include = ["crane-shot", "turntable"]   # заменяет include/exclude из skills.toml
# enabled = false                       # или вовсе выключить источник
```

Запись с тем же `path` заменяет базовую целиком; новый `path` добавляется; `enabled = false` выключает источник.

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

1. `./install.py` линкует `hooks/notify.sh` в `~/.claude/hooks/`.
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
- **Новый агент** → добавь `agents/<name>.md`, при необходимости впиши его в `tools: Agent(...)` архитектора, `make install`.
- **Внешний набор скилов** → `git submodule add <url> contrib/<name>`, впиши `[[source]]` в `skills.toml`, `make up` (см. [Установка](#установка)).

Изменения попадают сразу (symlink) — `make install` повторно нужен только для новых файлов/скилов или правок `skills.toml`.

## Обновление на другой машине

```bash
cd ~/claude-agents && git pull && make up
```
