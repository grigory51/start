# start

Переносимый сетап машины: единый каталог AI-компонентов для Claude Code и
OpenAI Codex, плюс обычные dotfiles и локальные служебные команды.

Главный инвариант проекта:

> Skill, agent или plugin регистрируется один раз в `[ai]` и по умолчанию
> поддерживается обоими backend-ами — `ai:claude` и `ai:codex`.

Нативный формат источника сохраняется. Если второму клиенту нужен другой формат,
`start` строит производное представление при синхронизации. Generated-файлы не
коммитятся и не меняют pinned submodules.

## Установка

```bash
git clone <repo-url> ~/start
cd ~/start
make up
```

Нужен Python 3.11+. `scripts/run.sh` использует `uv`, а при его отсутствии
создаёт локальный `.venv`.

| Цель | Что синхронизирует |
|---|---|
| `make up` | AI для установленных клиентов + Files |
| `make ai` | оба установленных AI backend-а |
| `make ai:claude` | только Claude Code |
| `make ai:codex` | только Codex |
| `make files` | только dotfiles |
| `make manage` | TUI: AI / Files / Commands |
| `make seed` | Claude plugin seed и settings |
| `make settings` | dry-run managed Claude settings |

Флаги `start` передаются через `--`:

```bash
make up -- --dry-run
make ai:codex -- --force
```

Прямой CLI:

```bash
uv run start up --only ai
uv run start up --only ai:claude
uv run start up --only ai:codex
uv run start up --only files
uv run start manage
uv run start add-submodule <git-url>
```

Если `claude` или `codex` отсутствует в `PATH`, соответствующий backend
пропускается без валидации. Остальные домены продолжают работу.

## Общий AI-каталог

Компоненты описываются только в `[ai]`:

```toml
[[ai.skills]]
path = "ai/skills"
enabled = ["*"]

[[ai.agents]]
path = "ai/agents"
enabled = ["*"]

[[ai.plugins]]
path = "contrib/claude-seo"
enabled = true

[[ai.mcp]]
name = "example"
enabled = true
[ai.mcp.server]
command = "example-mcp"
args = ["serve"]
```

Для skill/agent/plugin поле `platforms` по умолчанию равно
`["claude", "codex"]`. Явное исключение:

```toml
[[ai.plugins]]
path = "contrib/platform-specific"
enabled = true
platforms = ["claude"]
```

Platform-only компонент помечается в TUI. Если `platforms` не ограничен, но
adapter не может собрать целевое представление, backend завершается с ошибкой,
не подменяя предыдущую рабочую установку.

Машино-локальные overrides живут в gitignored `config.local.toml`:

```toml
[local.ai.skills]
"ai/skills" = ["my-principles"]

[local.ai.plugins]
"contrib/claude-seo" = false
```

Старый `[claude.*]` намеренно не поддерживается: CLI выдаёт сообщение о жёсткой
миграции на `[ai.*]`.

## Источники и adapters

### Skills

Skill — папка с `SKILL.md`. Claude получает зеркало исходника. Для Codex
создаётся нормализованная копия с frontmatter `name` и `description`; служебные
Claude-only поля убираются, а `scripts/`, `references/`, `assets/` сохраняются.

Глобальные назначения:

- Claude: `~/.claude/skills`;
- Codex: `~/.agents/skills`.

### Agents

Источник может быть Claude Markdown или Codex TOML:

- Markdown → TOML: инструкции становятся `developer_instructions`;
- TOML → Markdown: строится Claude agent с `model: inherit`;
- роли без Write/Edit получают Codex `sandbox_mode = "read-only"`;
- реализаторы и оркестраторы получают `workspace-write`;
- platform-specific model/tool metadata не копируется как ложный эквивалент.

Глобальные назначения:

- Claude: `~/.claude/agents`;
- Codex: `~/.codex/agents`.

Агенты внутри Claude plugin устанавливаются в Codex как companion agents и
включаются вместе с plugin.

### Plugins

Если source уже содержит `.claude-plugin` и `.codex-plugin`, используются оба
нативных представления. Иначе отсутствующее строится в:

```text
~/.local/share/start/generated/<platform>/plugins/<name>
```

Codex plugins регистрируются в personal marketplace
`~/.agents/plugins/marketplace.json` с обязательными policy/category полями и
устанавливаются через `codex plugin add`. Marketplace source `./plugins/<name>`
резолвится Codex как `~/plugins/<name>`; там менеджер держит owned symlink на
нативный или generated source. Чужие marketplace entries не изменяются.

При адаптации Claude plugin:

- skills нормализуются;
- slash commands превращаются в Codex skills;
- bundled agents превращаются в companion TOML;
- MCP и assets сохраняются;
- hook payload проходит через wrapper; для `apply_patch` wrapper извлекает все
  `Add/Update/Delete File` пути и передаёт их исходному file-oriented hook.

Claude продолжает использовать double-buffer plugin seed `.seed.store/{0,1}`.

## Глобальные инструкции и hooks

Единый источник глобальных инструкций — `ai/instructions/global.md`, принципы —
`ai/rules/my-principles.md`.

- Claude получает `~/.claude/CLAUDE.md` и `~/.claude/rules`;
- Codex получает сгенерированный `~/.codex/AGENTS.md`.

Loose hooks описываются как `[[ai.hooks]]`. Совместимые события регистрируются
для обоих клиентов; platform-specific hook ограничивается через `platforms`.
JSON/TOML settings мержатся sidecar-безопасно: чужие ключи не удаляются.

## HUD

HUD — один логический `[ai.statusline]` с двумя реализациями:

```toml
[ai.statusline.claude]
path = "ai/statusline/statusline.mjs"
dest = "statusline.mjs"
command = "node ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline.mjs"

[ai.statusline.codex]
items = [
  "model-with-reasoning",
  "current-dir",
  "git-branch",
  "context-remaining",
  "five-hour-limit",
  "weekly-limit",
  "fast-mode",
]
use_colors = true
```

Claude использует custom command renderer с focus, badges и вычисляемыми
цветами. Codex получает нативный TUI footer через `[tui].status_line`. Codex пока
не поддерживает внешний command-backed renderer, поэтому focus, badges,
reset countdown и session duration там не воспроизводятся.

## TUI

`make manage` открывает три домена:

- **AI** — Agents, Skills, Plugins, MCP, Status;
- **Files** — dotfiles;
- **Commands** — разовые локальные действия.

В AI-таблицах видны поддерживаемые платформы. `t` переключает локальный
`enabled`, `g` — версионный; один toggle синхронизирует оба установленных
backend-а.

## Добавление внешнего источника

```bash
uv run start add-submodule <url>
```

Автодетект ищет Claude/Codex plugin manifests, skills и соседние agents.
Регистрация всегда записывается в общий `[ai]`.

## Files и Commands

Dotfiles задаются через `[[files.dotfiles]]`:

```toml
[[files.dotfiles]]
source = "dotfiles/vimrc"
target = "~/.vimrc"
```

Опциональный `posthook` выполняется после раскладки с `SOURCE` и `TARGET` в
окружении. Чужой target сохраняется как `.bak` только при `--force`.

Разовые команды задаются через `[[commands.tasks]]`:

```toml
[[commands.tasks]]
name = "flush-dns"
title = "Сбросить DNS-кеш"
sudo = true
[commands.tasks.run]
darwin = "sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder"
```

Они запускаются только из TUI и не входят в provisioning `up`.

## Проверка

```bash
uv run python -m unittest discover -s tests -v
uv run start completion --check
make ai -- --dry-run
```
