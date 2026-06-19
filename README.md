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

Всё гоняется через [`uv`](https://docs.astral.sh/uv/) — он сам поднимает venv и тянет зависимости (`textual`, `tomlkit`). Отдельно ставить ничего не нужно. `make up` обновляет сабмодули (`contrib/`), собирает plugin seed и раскладывает symlink'и. Цели Makefile:

| Цель | Что |
|---|---|
| `make up` | сабмодули + плагины (seed) + symlink'и + merge `settings.json` — полная синхронизация |
| `make seed` | пересобрать plugin seed (`.seed/`) + merge `settings.json` (без loose-symlink'ов) |
| `make settings` | показать diff managed-ключей `settings.json` (dry-run, без записи) |
| `make manage` | TUI: агенты, скилы, плагины |

CLI — `uv run claude-agents <команда>` (Python 3.11+). Подкоманды:
- `up` — сабмодули + плагины (seed) + symlink'и + settings (флаги `--dry-run`, `--force`, `--no-submodules`, `--no-seed`, `--no-settings`)
- `seed` — пересобрать plugin seed + merge `settings.json`, не трогая loose-symlink'и (флаг `--dry-run`)
- `settings` — merge managed-ключей в `~/.claude/settings.json` (флаги `--dry-run`, `--remove`)
- `manage` — Textual TUI с тремя вкладками. **Агенты**: сгруппированы по источнику, Enter открывает модалку с телом `.md`. **Скилы**: сгруппированы по источнику, Enter открывает `SKILL.md`, Space/`t` включает/выключает (правит поле `enabled` источника прямо в `config.toml` и тут же дёргает линковку), `a` — добавить git-сабмодуль (см. ниже). **Плагины**: список `[[plugins]]`, Space/`t` toggle (пересобирает seed + merge settings), Enter открывает `plugin.json`, ⚠ помечает плагины с SessionStart-хуками.
- `add-submodule <url>` — `git submodule add` в `contrib/`; автодетект: если в сабмодуле есть `.claude-plugin/` — регистрируется как `[[plugins]]`, иначе автодетект подпапки со скилами и запись `[[skills]]` (флаги `--name`, `--skills-subdir`, `--no-install`)

Создаёт symlink'и в `~/.claude/`:
- `~/.claude/agents/` — **реальная папка** с per-file симлинками; источники
  (`repo/agents`) перечислены в `config.toml` секцией `[[agents]]`.
- `~/.claude/skills/` — **реальная папка** с per-skill симлинками; источники
  (`repo/skills`, сабмодуль `contrib/blender-skills`) перечислены в `config.toml` секцией `[[skills]]`.
- `~/.claude/hooks/*.sh` → `repo/hooks/*.sh` (per-file)
- *(опц.)* `~/.claude/commands/` — per-file симлинки из `[[commands]]`.

Плагины (`[[plugins]]`) **не симлинкуются** — они собираются в plugin seed и подключаются Claude Code нативно (см. [Плагины](#нативные-cc-плагины-plugin-seed)).

`agents` и `skills` линкуются **поэлементно**, чтобы в одну папку сходились элементы из репо и из внешних сабмодулей `contrib/` одновременно (folder-link так не умеет). `hooks` линкуется по-файлу: рядом лежат сторонние хуки (напр. caveman), папку перекрывать нельзя.

Старую схему (`~/.claude/agents` и `~/.claude/skills` как folder-symlink) `up` мигрирует автоматически: снимает линк, ставит реальную папку, раскладывает per-элемент симлинки. Элемент, выпавший из `config.toml` (или выключенный), его симлинк удаляется. Посторонние файлы/симлинки не трогает без `--force`.

Флаги:
- `uv run claude-agents up --dry-run` — показать действия без изменений
- `uv run claude-agents up --force` — перезаписать чужие файлы/симлинки (с бэкапом `.bak`)
- `CLAUDE_HOME=/custom/path uv run claude-agents up` — другой каталог Claude

### Внешние скилы и агенты: `contrib/` + `config.toml`

Внешние наборы подключаются git-сабмодулями в `contrib/`, а `config.toml` задаёт, что из них линковать. Скилы — секция `[[skills]]`:

```toml
[[skills]]
path = "contrib/blender-skills"   # папка-источник (относительно корня репо)
enabled = ["image-to-3d", "multi-image-to-3d"]   # какие скилы включить
# exclude = ["threejs-export"]    # что вообще не показывать (поверх enabled)
```

Поле `enabled` — это и есть состояние вкл/выкл:
- `["*"]` — все скилы источника (значение по умолчанию, если поля нет);
- `["a", "c"]` — только перечисленные (имена папок);
- `[]` — ни одного (источник выключен целиком).

Отдельного списка `disabled` нет: выключить скил = убрать его имя из `enabled` (`"*"` при этом разворачивается в явный список). Именно это поле правит TUI при toggle — **прямо в версионном `config.toml`** (`config.local.toml` нет).

Скил = подкаталог с `SKILL.md`; его имя в `~/.claude/skills/` = имя папки. Конфликт имён между источниками — берётся первое вхождение, остальное пропускается с предупреждением.

Агенты — секция `[[agents]]` (аналогично скилам, но источник — папка с `*.md`-файлами; имя файла без `.md` = имя агента; `enabled` — имена без `.md`):

```toml
[[agents]]
path = "agents"      # локальные агенты репо
enabled = ["*"]
```

`enabled`-список действует и у агентов: линкуются только включённые им. Конфликт имён — первое вхождение (как у скилов). Агенты плагинов идут через `[[plugins]]`, а не сюда.

Добавить новый набор:

```bash
uv run claude-agents add-submodule <url>   # git submodule add + автодетект
make up
```

Под капотом `add-submodule` = `git submodule add <url> contrib/<name>` + автодетект:
- есть `.claude-plugin/` → регистрируется как `[[plugins]]` (нативный CC-плагин);
- иначе → автодетект подпапки со скилами (корень / `./skills`) и запись `[[skills]]`.

Можно и руками: `git submodule add ...`, затем вписать `[[skills]]`/`[[agents]]`/`[[plugins]]` в `config.toml`, `make up`. То же доступно в TUI (`make manage`) по клавише `a`.

> На практике seo/infostyle подключены как `[[plugins]]`; `[[skills]]` остались только для loose-скилов (`skills/` + `contrib/blender-skills`), `[[agents]]` — только для своих (`agents/`).

## Нативные CC-плагины (plugin seed)

Источник с `.claude-plugin/` (т.е. `plugin.json` + `marketplace.json`) — это полноценный плагин Claude Code, а не просто пачка скилов. Такие источники подключаются секцией `[[plugins]]` и **не симлинкуются**:

```toml
[[plugins]]
path = "contrib/claude-seo"   # корень плагина (каталог с .claude-plugin/)
enabled = true                # плагин атомарен → enabled это bool
# marketplace = "..."         # override, если автодетект из манифеста не сработал
# plugin = "..."
```

**Зачем нативно, а не symlink'ом.** Плагин несёт не только skills, но и hooks, MCP-серверы, slash-команды и `${CLAUDE_PLUGIN_ROOT}`. При раскладке скилов по отдельности всё это терялось (раньше приходилось хачить scripts/ через `[[skills.symlinks]]`). CC умеет подключать плагин целиком сам — нужно лишь отдать ему собранный seed.

**Как это работает.** Менеджер собирает **plugin seed** в `.seed/` (в `.gitignore`) руками самого `claude` CLI, неинтерактивно:

```
CLAUDE_CODE_PLUGIN_CACHE_DIR=.seed  claude plugin marketplace add <abs path к плагину>
CLAUDE_CODE_PLUGIN_CACHE_DIR=.seed  claude plugin install <plugin>@<marketplace> --scope user
```

Затем досоздаёт симлинк `.seed/marketplaces/<mp>` → корень плагина: при чтении seed CC физически probит `$SEED/marketplaces/<name>/`, а для local `directory`-source туда контент не кладёт — без симлинка плагин не материализуется. CC читает seed через env `CLAUDE_CODE_PLUGIN_SEED_DIR` (абсолютный путь на `.seed`, пишется в `~/.claude/settings.json`): read-only, без клона, без промпта — и сам подключает skills/agents/commands/hooks/MCP плагина, резолвит `${CLAUDE_PLUGIN_ROOT}`. Так раньше терявшиеся хуки/MCP/команды (напр. у `claude-seo`) теперь работают.

> **Требуется `claude` в PATH.** Без него фаза плагинов пропускается с предупреждением (остальная, loose-часть всё равно отрабатывает).

Seed — производный артефакт: полностью пересобирается на каждом `up`/`seed`. У seed CC отключает auto-update by design, поэтому **обновление плагина** = `make up` (git submodule update тянет новый пин → пересборка seed). Toggle плагина в TUI или `make seed` делает то же точечно.

### Безопасный merge `~/.claude/settings.json` (sidecar)

`settings.json` один на scope и общий с другими настройками (permissions, model, statusLine, чужие плагины…). Менеджер дописывает в него **только свои ключи** — `enabledPlugins`, `extraKnownMarketplaces`, `env.CLAUDE_CODE_PLUGIN_SEED_DIR`, `mcpServers`, `hooks` — и трекает их в sidecar-манифесте `~/.claude/.claude-agents-managed.json`. Чужие ключи не трогаются. Merge идемпотентен, перед записью кладётся бэкап `settings.json.bak`. Выпавшее (плагин/хук/MCP убрали из конфига) удаляется по sidecar. Подкоманда `settings --remove` чистит ровно свои ключи и удаляет sidecar.

### Slash-команды, MCP, hooks

Опциональные секции (сейчас в `config.toml` закомментированы, кроме `[[hooks]]` для нотификаций):

- `[[commands]]` — рассыпанные `*.md`-команды (как `[[agents]]`): имя файла без `.md` = имя команды, symlink в `~/.claude/commands/`. Команды плагинов идут через `[[plugins]]`.
- `[[mcp]]` — standalone MCP для не-плагинных источников. Два режима: symlink готового `.mcp.json` (`source = "..."`) либо inline-спека (`[mcp.server]` → пишется в `mcpServers` фрагмента settings).
- `[[hooks]]` — авто-регистрация loose-хука на события CC (см. ниже).

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

**Подключение** (раз на машину): `make up` (или `uv run claude-agents up`).

Регистрация событий теперь автоматическая. Секция `[[hooks]]` в `config.toml` задаёт, какой скрипт на какие события CC повесить:

```toml
[[hooks]]
path = "hooks/notify.sh"            # *.sh относительно корня репо
events = ["Stop", "Notification"]   # события CC
```

`up` симлинкует файл в `~/.claude/hooks/` и регистрирует `bash "<...>"` на эти события в `~/.claude/settings.json` через тот же [sidecar-merge](#безопасный-merge-claudesettingsjson-sidecar) — чужие записи `hooks` не трогаются.

**Настройка:**
- Убрать событие → убери его из `events` (или всю запись `[[hooks]]`), `make up`.
- Добавить звук → в `hooks/notify.sh` допиши в `osascript` `... sound name "Glass"`.
- Альтернатива без скрипта → встроенный `preferredNotifChannel` в `~/.claude/settings.json` (`"auto"` — баннер в Ghostty/Kitty/iTerm2; `"terminal_bell"` — звонок). Покрывает done+permission, но без гибкости этого хука.

## Кастомизация

- **Свой стиль** → правь `skills/my-principles/SKILL.md` (общий для всех).
- **Тюнинг конкретного агента** → правь тело его `.md` в `agents/`.
- **Новый агент** → добавь `agents/<name>.md`, при необходимости впиши его в `tools: Agent(...)` архитектора, `make up`.
- **Внешний набор** → `uv run claude-agents add-submodule <url>` (или `git submodule add <url> contrib/<name>` + `[[skills]]`/`[[agents]]`/`[[plugins]]` в `config.toml`), `make up` (см. [Установка](#установка)).

Изменения скилов/агентов попадают сразу (symlink) — `make up` повторно нужен только для новых файлов/скилов или правок `config.toml` (для локальных правок без сети: `uv run claude-agents up --no-submodules`). Плагины — производный seed: после правки `[[plugins]]` или обновления пина нужен `make seed` (или `make up`), затем **перезапуск `claude`** (seed читается на старте).

## Обновление на другой машине

```bash
cd ~/claude-agents && git pull && make up
```
