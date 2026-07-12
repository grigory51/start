# start

Переносимый сетап машины в одном репозитории: конфиг [Claude Code](https://code.claude.com) (агенты, навыки, плагины, hooks) **и** обычные dotfiles (`~/.vimrc`, …). Клонируешь на новый ноутбук, `make up` — и всё разложено симлинками (`~/.claude/` + `$HOME`). Команда названа под идиому `start up` — «поднять машину с нуля».

Ядро для Claude Code — персональный набор агентов, кодирующий мой стиль, идеи и подходы; dotfiles — общий слой сетапа поверх.

`start` синхронизирует два независимых **домена**:

- **[Claude](#домен-claude-claude)** (`~/.claude`) — агенты и их оркестрация, навыки, нативные плагины, hooks, `CLAUDE.md`, rules, statusline, `settings.json`.
- **[Files](#домен-files-home)** (`$HOME`) — обычные dotfiles машины (`~/.vimrc`, …), к Claude отношения не имеющие.

`make up` раскатывает оба домена; каждый можно гонять по отдельности (см. [Установка](#установка)).

## Установка

```bash
git clone <repo-url> ~/start
cd ~/start
make up
```

Всё гоняется через [`uv`](https://docs.astral.sh/uv/) — он сам поднимает venv и тянет зависимости (`textual`, `tomlkit`). Отдельно ставить ничего не нужно. `make up` обновляет сабмодули (`contrib/`), собирает plugin seed и раскладывает symlink'и в оба домена.

`make`-цели зовут не `uv` напрямую, а обёртку `scripts/run.sh`: она предпочитает `uv`, а на машинах без него (напр. Debian-нетбук) разово поднимает локальный `.venv` (`python3 -m venv` + `pip install -e .`) и гоняет CLI через него. Так `make manage`/`make files` работают и без `uv` — нужен лишь `python3` (+ `python3-venv`/`python3-pip`).

Синхронизация разбита на два **домена**: **Claude** (`~/.claude`: агенты, скилы, плагины, hooks, CLAUDE.md, rules, statusline, settings) и **Files** (`$HOME`: `[[files.dotfiles]]`). В выводе они разделены заголовками (`══ Claude ══` / `══ Files ══`); можно гонять по отдельности.

| Цель | Что |
|---|---|
| `make up` | оба домена — полная синхронизация (сабмодули + плагины seed + symlink'и + settings + dotfiles) |
| `make claude` | только домен Claude (`~/.claude`) |
| `make files` | только домен Files (`$HOME`), без сабмодулей/seed/settings |
| `make seed` | пересобрать plugin seed (`.seed/`) + merge `settings.json` (без loose-symlink'ов) |
| `make settings` | показать diff managed-ключей `settings.json` (dry-run, без записи) |
| `make manage` | TUI: домены Claude / Files / Команды (переключение — F2) |

**Флаги `start` через make** пробрасываются после `--` — иначе make перехватит их как свои опции:

```bash
make up -- --force          # start up --force
make claude -- --dry-run    # только Claude, вхолостую
make files -- --force       # только Files, с бэкапом чужих файлов
```

CLI — `uv run start <команда>` (Python 3.11+). Подкоманды:
- `up` — оба домена (флаги `--dry-run`, `--force`, `--no-submodules`, `--no-seed`, `--no-settings`, `--only claude|files`)
- `seed` — пересобрать plugin seed + merge `settings.json`, не трогая loose-symlink'и (флаг `--dry-run`)
- `settings` — merge managed-ключей в `~/.claude/settings.json` (флаги `--dry-run`, `--remove`)
- `manage` — Textual TUI, разбитый на **домены** (переключение — **F2**, norton-стиль; вложенных табов нет). **Claude** — вкладки: **Агенты** (сгруппированы по источнику, Enter — тело `.md`), **Скилы** (Enter — `SKILL.md`; Space/`t` вкл/выкл, правит `enabled` в `config.toml` и дёргает линковку; `g` — глобально; `a` — добавить git-сабмодуль), **Плагины** (`[[claude.plugins]]`, Space/`t` toggle пересобирает seed + merge settings, Enter — `plugin.json`, ⚠ — SessionStart-хуки), **MCP** (`[[claude.mcp]]`, toggle → `~/.claude.json`). **Files** — просмотр `[[files.dotfiles]]` (source/target/posthook), Enter — детали. **Команды** — `[[commands.tasks]]`: `r`/Enter запускают команду для текущей ОС (TUI на время запуска сворачивается, чтобы sudo мог спросить пароль).
- `add-submodule <url>` — `git submodule add` в `contrib/`; автодетект: если в сабмодуле есть `.claude-plugin/` — регистрируется как `[[claude.plugins]]`, иначе автодетект подпапки со скилами и запись `[[claude.skills]]` (флаги `--name`, `--skills-subdir`, `--no-install`)

Общие флаги домена(ов):
- `uv run start up --dry-run` — показать действия без изменений
- `uv run start up --force` — перезаписать чужие файлы/симлинки (с бэкапом `.bak`)
- `CLAUDE_HOME=/custom/path uv run start up` — другой каталог Claude

## Домен Claude (~/.claude)

Конфиг [Claude Code](https://code.claude.com) целиком: агенты и их оркестрация, навыки, нативные плагины (plugin seed), hooks, `CLAUDE.md`, statusline, `settings.json`. Всё раскладывается в `~/.claude`. Запуск домена по отдельности — `make claude` (или `uv run start up --only claude`).

### Агентная оркестрация

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

| Агент | Роль | Доступ |
|---|---|---|
| `architect` | Проектирует и оркеструет. Код руками не пишет. | спавн worker'ов + read-only + Bash |
| `programmer` | Реализует/правит код по блюпринту. | Read/Write/Edit/Bash/Grep/Glob |
| `reviewer` | Read-only аудит диффа (баги, безопасность, стиль). | Read/Grep/Glob/Bash |
| `designer` | UI/UX, frontend. | Read/Write/Edit/Bash/Grep/Glob |
| `tech-writer` | README, доки, докстринги, комменты. | Read/Write/Edit/Grep/Glob |

Личные принципы/стиль (`my-principles`) — единый источник в `ai/claude/rules/my-principles.md`. Общая часть грузится **always-on** в каждой top-level сессии как rule (`~/.claude/rules/`), а субагентам отдаётся через скилл `ai/skills/my-principles/` (`skills:` frontmatter) — его `SKILL.md` симлинкнут на тот же файл. Стек-специфика (`tech/*.md`) остаётся в скилле и грузится лениво.

**Запуск на задаче:**

```bash
# запустить оркестратора на задаче
claude --agent architect

# затем в сессии описать задачу, напр.:
#   "Добавь endpoint /health с тестом"
# architect спроектирует, делегирует programmer, прогонит reviewer, отчитается.
```

Worker-агентов можно звать и вручную в обычной сессии: `@programmer ...`, `@reviewer ...`. Проверить, что агенты подключены: внутри сессии `/agents`.

### Раскладка в ~/.claude

`make claude`/`make up` создаёт symlink'и в `~/.claude/`:
- `~/.claude/agents/` — **реальная папка** с per-file симлинками; источники
  (`repo/ai/claude/agents`) перечислены в `config.toml` секцией `[[claude.agents]]`.
- `~/.claude/skills/` — **реальная папка** с per-skill симлинками; источники
  (`repo/ai/claude/skills`, сабмодуль `contrib/blender-skills`) перечислены в `config.toml` секцией `[[claude.skills]]`.
- `~/.claude/hooks/*.sh` → `repo/ai/claude/hooks/*.sh` (per-file)
- `~/.claude/CLAUDE.md` → `repo/ai/claude/CLAUDE.global.md` (глобальные инструкции, always-on).
- `~/.claude/rules/` — **реальная папка** с per-entry симлинками на `repo/ai/claude/rules/*.md`;
  CC грузит все `*.md` оттуда автоматически при старте (always-on, как `CLAUDE.md`, но по темам).
  Источник фиксирован в репо (не через `config.toml`).
- *(опц.)* `~/.claude/commands/` — per-file симлинки из `[[claude.commands]]`.

Плагины (`[[claude.plugins]]`) **не симлинкуются** — они собираются в plugin seed и подключаются Claude Code нативно (см. [Плагины](#нативные-cc-плагины-plugin-seed)).

`agents` и `skills` линкуются **поэлементно**, чтобы в одну папку сходились элементы из репо и из внешних сабмодулей `contrib/` одновременно (folder-link так не умеет). `hooks` линкуется по-файлу: рядом лежат сторонние хуки (напр. caveman), папку перекрывать нельзя.

Старую схему (`~/.claude/agents` и `~/.claude/skills` как folder-symlink) `up` мигрирует автоматически: снимает линк, ставит реальную папку, раскладывает per-элемент симлинки. Элемент, выпавший из `config.toml` (или выключенный), его симлинк удаляется. Посторонние файлы/симлинки не трогает без `--force`.

### Внешние скилы и агенты: `contrib/` + `config.toml`

Внешние наборы подключаются git-сабмодулями в `contrib/`, а `config.toml` задаёт, что из них линковать. Скилы — секция `[[claude.skills]]`:

```toml
[[claude.skills]]
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

Агенты — секция `[[claude.agents]]` (аналогично скилам, но источник — папка с `*.md`-файлами; имя файла без `.md` = имя агента; `enabled` — имена без `.md`):

```toml
[[claude.agents]]
path = "agents"      # локальные агенты репо
enabled = ["*"]
```

`enabled`-список действует и у агентов: линкуются только включённые им. Конфликт имён — первое вхождение (как у скилов). Агенты плагинов идут через `[[claude.plugins]]`, а не сюда.

Добавить новый набор:

```bash
uv run start add-submodule <url>   # git submodule add + автодетект
make up
```

Под капотом `add-submodule` = `git submodule add <url> contrib/<name>` + автодетект:
- есть `.claude-plugin/` → регистрируется как `[[claude.plugins]]` (нативный CC-плагин);
- иначе → автодетект подпапки со скилами (корень / `./skills`) и запись `[[claude.skills]]`.

Можно и руками: `git submodule add ...`, затем вписать `[[claude.skills]]`/`[[claude.agents]]`/`[[claude.plugins]]` в `config.toml`, `make up`. То же доступно в TUI (`make manage`) по клавише `a`.

> На практике seo/infostyle подключены как `[[claude.plugins]]`; `[[claude.skills]]` остались только для loose-скилов (`ai/claude/skills/` + `contrib/blender-skills`), `[[claude.agents]]` — только для своих (`ai/claude/agents/`).

### Нативные CC-плагины (plugin seed)

Источник с `.claude-plugin/` (т.е. `plugin.json` + `marketplace.json`) — это полноценный плагин Claude Code, а не просто пачка скилов. Такие источники подключаются секцией `[[claude.plugins]]` и **не симлинкуются**:

```toml
[[claude.plugins]]
path = "contrib/claude-seo"   # корень плагина (каталог с .claude-plugin/)
enabled = true                # плагин атомарен → enabled это bool
# marketplace = "..."         # override, если автодетект из манифеста не сработал
# plugin = "..."
```

**Зачем нативно, а не symlink'ом.** Плагин несёт не только skills, но и hooks, MCP-серверы, slash-команды и `${CLAUDE_PLUGIN_ROOT}`. При раскладке скилов по отдельности всё это терялось (раньше приходилось хачить scripts/ через `[[claude.skills.symlinks]]`). CC умеет подключать плагин целиком сам — нужно лишь отдать ему собранный seed.

**Как это работает.** Менеджер собирает **plugin seed** в `.seed/` (в `.gitignore`) руками самого `claude` CLI, неинтерактивно:

```
CLAUDE_CODE_PLUGIN_CACHE_DIR=.seed  claude plugin marketplace add <abs path к плагину>
CLAUDE_CODE_PLUGIN_CACHE_DIR=.seed  claude plugin install <plugin>@<marketplace> --scope user
```

Затем досоздаёт симлинк `.seed/marketplaces/<mp>` → корень плагина: при чтении seed CC физически probит `$SEED/marketplaces/<name>/`, а для local `directory`-source туда контент не кладёт — без симлинка плагин не материализуется. CC читает seed через env `CLAUDE_CODE_PLUGIN_SEED_DIR` (абсолютный путь на `.seed`, пишется в `~/.claude/settings.json`): read-only, без клона, без промпта — и сам подключает skills/agents/commands/hooks/MCP плагина, резолвит `${CLAUDE_PLUGIN_ROOT}`. Так раньше терявшиеся хуки/MCP/команды (напр. у `claude-seo`) теперь работают.

> **Требуется `claude` в PATH.** Без него **весь домен Claude пропускается целиком** (ни seed, ни settings, ни symlink'и в `~/.claude`) — нет смысла раскладывать конфиг отсутствующего инструмента. Домен Files при этом отрабатывает как обычно. Появится Claude Code → повторный `up` разложит `~/.claude`.

Seed — производный артефакт: пересобирается на каждом `up`/`seed`. У seed CC отключает auto-update by design, поэтому **обновление плагина** = `make up` (git submodule update тянет новый пин → пересборка seed). Toggle плагина в TUI или `make seed` делает то же точечно.

**Пересборка не ломает бегущие сессии (double-buffer).** `.seed` — это стабильный symlink на активный буфер; реальные буферы лежат в `.seed.store/{0,1}` (оба в `.gitignore`). Сборка идёт в *неактивный* буфер, затем `.seed` **атомарно** (`os.replace` симлинка, один syscall) переключается на свежий. Живая сессия читает целый старый буфер до момента swap — раньше `rm -rf .seed` в начале пересборки оставлял путь пустым на всё время сборки, и CC ронял ошибку `Plugin directory does not exist`. Старый буфер сохраняется как альтернативный (перезапишется на следующей сборке).

#### Безопасный merge `~/.claude/settings.json` (sidecar)

`settings.json` один на scope и общий с другими настройками (permissions, model, statusLine, чужие плагины…). Менеджер дописывает в него **только свои ключи** — `enabledPlugins`, `extraKnownMarketplaces`, `env.CLAUDE_CODE_PLUGIN_SEED_DIR`, `mcpServers`, `hooks` — и трекает их в sidecar-манифесте `~/.claude/.claude-agents-managed.json`. Чужие ключи не трогаются. Merge идемпотентен, перед записью кладётся бэкап `settings.json.bak`. Выпавшее (плагин/хук/MCP убрали из конфига) удаляется по sidecar. Подкоманда `settings --remove` чистит ровно свои ключи и удаляет sidecar.

> Sidecar-файл исторически называется `.claude-agents-managed.json` (проект раньше назывался claude-agents) — имя намеренно не меняли, чтобы не потерять трекинг на уже настроенных машинах.

#### Slash-команды, MCP, hooks

Опциональные секции (сейчас в `config.toml` закомментированы, кроме `[[claude.hooks]]` для нотификаций):

- `[[claude.commands]]` — рассыпанные `*.md`-команды (как `[[claude.agents]]`): имя файла без `.md` = имя команды, symlink в `~/.claude/commands/`. Команды плагинов идут через `[[claude.plugins]]`.
- `[[claude.mcp]]` — standalone MCP для не-плагинных источников. Два режима: symlink готового `.mcp.json` (`source = "..."`) либо inline-спека (`[claude.mcp.server]` → пишется в `mcpServers` фрагмента settings).
- `[[claude.hooks]]` — авто-регистрация loose-хука на события CC (см. [Нотификации](#нотификации-когда-агент-ждёт-тебя)).

### Нотификации (когда агент ждёт тебя)

macOS-баннер прилетает, когда:
- агент **ждёт ввода** (простой сессии, `idle_prompt`);
- агент **просит разрешение** на действие (`permission_prompt`);
- главный агент **закончил ход** (`Stop`) — удобно для долгих architect-циклов.

Канал — macOS Notification Center (`osascript`), **без звука**. Не пересекается с `agentPushNotifEnabled` (это mobile push на телефон — отдельный канал).

**Подключение** (раз на машину): `make up` (или `uv run start up`).

Регистрация событий автоматическая. Секция `[[claude.hooks]]` в `config.toml` задаёт, какой скрипт на какие события CC повесить:

```toml
[[claude.hooks]]
path = "ai/claude/hooks/notify.sh"         # *.sh относительно корня репо
events = ["Stop", "Notification"]   # события CC
```

`up` симлинкует файл в `~/.claude/hooks/` и регистрирует `bash "<...>"` на эти события в `~/.claude/settings.json` через тот же [sidecar-merge](#безопасный-merge-claudesettingsjson-sidecar) — чужие записи `hooks` не трогаются.

**Настройка:**
- Убрать событие → убери его из `events` (или всю запись `[[claude.hooks]]`), `make up`.
- Добавить звук → в `ai/claude/hooks/notify.sh` допиши в `osascript` `... sound name "Glass"`.
- Альтернатива без скрипта → встроенный `preferredNotifChannel` в `~/.claude/settings.json` (`"auto"` — баннер в Ghostty/Kitty/iTerm2; `"terminal_bell"` — звонок). Покрывает done+permission, но без гибкости этого хука.

## Домен Files ($HOME)

Кроме конфига Claude Code, `start` раскатывает обычные dotfiles машины — файл/папку из репо симлинком в `$HOME`. Это часть «сетапа ноутбука», не имеющая отношения к Claude. Запуск домена по отдельности — `make files` (или `uv run start up --only files`; сабмодули/seed/settings при этом не трогаются). Секция `[[files.dotfiles]]` в `config.toml`:

```toml
[[files.dotfiles]]
source = "dotfiles/vimrc"   # путь в репо (относительно корня)
target = "~/.vimrc"         # путь в $HOME (~ и абсолютные разворачиваются)
```

`source` — файл или папка внутри `dotfiles/` (в репо без ведущей точки — точка появляется в `target`). `target` может быть вложенным (`~/.config/nvim/init.vim`) — недостающие каталоги создаются. `up` ставит симлинк той же утилитой, что и для агентов/скилов: наш корректный симлинк пропускается (идемпотентно), чужой файл на месте — бэкап в `<name>.bak` при `--force`, иначе пропуск с предупреждением.

**Завести новый dotfile** (напр. существующий `~/.vimrc`):

```bash
cp ~/.vimrc dotfiles/vimrc         # утащить содержимое в репо
# вписать [[files.dotfiles]] в config.toml
make files -- --force              # старый ~/.vimrc → ~/.vimrc.bak, на месте симлинк
```

> Прун снятых записей не делается: убрал `[[files.dotfiles]]` из конфига — симлинк в `$HOME` снимаешь вручную (безопасно итерировать `$HOME` менеджер не берётся).

### `posthook`: команда после раскладки записи

У записи `[[files.dotfiles]]` есть опциональное поле `posthook` — shell-команда, которую `up` выполняет после раскладки записи. Само поле `target` тоже опционально: запись может быть **только posthook** (без симлинка). В окружении команды доступны `SOURCE` (абсолютный путь `source` в репо) и `TARGET` (развёрнутый `target` или пустая строка). Команда должна быть **идемпотентной** — она гоняется на каждом `up`; это доверенный shell из версионного `config.toml`.

**Пример — iTerm2.** iTerm умеет читать и писать свои настройки из произвольной папки («Load preferences from a custom folder»). Кладём папку с `com.googlecode.iterm2.plist` в репо (`dotfiles/iterm/`) и через `posthook` указываем iTerm на неё — симлинк в `$HOME` не нужен:

```toml
[[files.dotfiles]]
source = "dotfiles/iterm"   # custom-папка iTerm (НЕ симлинкуется в $HOME)
posthook = 'defaults write com.googlecode.iterm2 PrefsCustomFolder -string "$SOURCE" && defaults write com.googlecode.iterm2 LoadPrefsFromCustomFolder -bool true'
```

**Двусторонний sync.** Чтобы правки в iTerm сохранялись обратно в папку репо, включи в iTerm сохранение: Settings → General → Preferences → «Save changes … when iTerm2 quits». После этого `git diff dotfiles/iterm/` показывает изменения настроек (плист бинарный — диффы непрозрачны, но версионируются). Завести с нуля: `cp ~/Library/Preferences/com.googlecode.iterm2.plist dotfiles/iterm/`, затем `make files`.

### Машина без Claude Code

Если на машине нет Claude Code (нет каталога `~/.claude` / бинаря `claude`), гоняй только домен Files:

```bash
make files            # = uv run start up --only files
```

Раскатываются только dotfiles/posthook'и — `~/.claude`, сабмодули, plugin seed и `settings.json` не трогаются. Полный `make up` на такой машине тоже безопасен: если `claude` нет в PATH, **весь домен Claude пропускается целиком** (с сообщением), отрабатывает только Files. То есть `make files` здесь — про запуск строго одного домена, а `make up` сам сведётся к Files, пока нет Claude Code. Точечно включить/выключить отдельные плагины/скилы/MCP под конкретную машину — через `config.local.toml` (`[local.plugins]`, `[local.skills]`, `[local.mcp]`; формат плоский) или клавишей `t` в `make manage`.

## Домен Команды (Commands)

Разовые действия на машине, запускаемые из TUI по требованию (не про провижининг — `up` их не трогает). Секция `[[commands.tasks]]` в `config.toml`, у каждой команды — варианты запуска по ОС:

```toml
[[commands.tasks]]
name = "flush-dns"
title = "Сбросить DNS-кеш"
description = "Очистить кеш DNS-резолвера операционной системы."
sudo = true                 # пометка «нужен root» (сам sudo — часть команды)
[commands.tasks.run]
darwin = "sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder"
# linux = "..."             # закладка под другие ОС (ключи по sys.platform)
```

На текущей ОС берётся `run[sys.platform]` (`darwin`/`linux`/`win32`); нет варианта под неё — команда помечается недоступной и не запускается. В TUI: домен **Команды** (F2), `r`/Enter — запуск. На время запуска TUI сворачивается (`app.suspend`), команда получает реальный терминал — поэтому `sudo` может спросить пароль; после — статус результата. Запуск идёт с `cwd` = корень репо и env `REPO` (абсолютный путь), так что команда может ссылаться на скрипты репо: `run.darwin = 'bash "$REPO/scripts/…"'`.

**Пример linux-only команды — `Provision: netbook`.** Обустраивает headless-Debian нетбук: терминальные утилиты (`tmux`/`mosh`/`mc`/`fzf`/`gpm`), Wi-Fi-менеджер (NetworkManager + `nmtui`), инструменты дебага сети и 3G-модема, локали и dotfiles. Логика — в **Ansible-роли** `scripts/ansible/roles/netbook`; обёртка `scripts/provision.sh <role>` прогоняет плейбук роли **локально** (`ansible-playbook -i 'localhost,' -c local <role>.yml`, ставит `ansible` через apt при отсутствии). Команда задаёт `run.linux = 'bash "$REPO/scripts/provision.sh" netbook'` → на macOS недоступна (💡 off), выкатывается на самом нетбуке. Новые роли складываешь в `scripts/ansible/` (роль + `<role>.yml`) и гоняешь той же обёрткой из домена «Команды».

**Пример со скриптом — PXE-загрузка (`pxe-netboot`).** Ставит старый ноутбук по сети, когда у него ещё нет доступа в интернет (карантин-VLAN, только до Mac). Скрипт `scripts/pxe/pxe-netboot.sh` поднимает контейнер [netboot.xyz](https://netboot.xyz) (`scripts/pxe/docker-compose.yml`: локальное меню + HTTP-раздача предскачанных ассетов) и нативный `dnsmasq` на хосте в режиме **proxyDHCP + TFTP** — целевая машина грузится из локального меню, интернет ей не нужен. Роли разведены из-за OrbStack (Linux-VM без bridged-L2 к `en0`): `dnsmasq` — на хосте (broadcast), контейнер — HTTP через published-порты (включить «Expose ports to LAN» в OrbStack). Требует OrbStack/Docker и `brew install dnsmasq`; дистрибутивы предскачиваются разово через webUI netboot.xyz (`localhost:3000`).

## Кастомизация

- **Свой стиль** → правь `ai/claude/rules/my-principles.md` (единый источник; `ai/skills/my-principles/SKILL.md` симлинкнут на него). Стек-специфика — `ai/skills/my-principles/tech/*.md`.
- **Тюнинг конкретного агента** → правь тело его `.md` в `ai/claude/agents/`.
- **Новый агент** → добавь `ai/claude/agents/<name>.md`, при необходимости впиши его в `tools: Agent(...)` архитектора, `make up`.
- **Внешний набор** → `uv run start add-submodule <url>` (или `git submodule add <url> contrib/<name>` + `[[claude.skills]]`/`[[claude.agents]]`/`[[claude.plugins]]` в `config.toml`), `make up` (см. [Установка](#установка)).

Изменения скилов/агентов попадают сразу (symlink) — `make up` повторно нужен только для новых файлов/скилов или правок `config.toml` (для локальных правок без сети: `uv run start up --no-submodules`). Плагины — производный seed: после правки `[[claude.plugins]]` или обновления пина нужен `make seed` (или `make up`), затем **перезапуск `claude`** (seed читается на старте).

## Обновление на другой машине

```bash
cd ~/start && git pull && make up
```

На машине без Claude Code — только домен Files (см. [Машина без Claude Code](#машина-без-claude-code)):

```bash
cd ~/start && git pull && make files
```
