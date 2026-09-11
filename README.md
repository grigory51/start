# start

Персональный provisioning машины из одного git-репозитория. `start` хранит
желаемое состояние окружения и раскладывает его по установленным инструментам.

Проект управляет тремя областями:

- **AI** — общие инструкции, agents, skills, plugins, MCP, hooks и statusline для
  Claude Code и OpenAI Codex;
- **Files** — dotfiles и профили приложений в `$HOME`;
- **Commands** — локальные служебные и provisioning-скрипты, запускаемые вручную
  из TUI.

## Как это устроено

Версионный [config.toml](config.toml) описывает весь каталог. Машино-локальные
переключатели хранятся в gitignored `config.local.toml`.

AI-компонент регистрируется один раз в `[ai]`. Claude и Codex получают свои
представления из одного source: нативный формат сохраняется, а совместимая копия
строится только при необходимости. Поэтому agents, skills и plugins не нужно
поддерживать отдельно для каждого клиента.

`make up` синхронизирует только установленные клиенты, раскладывает dotfiles и не
перезаписывает чужие файлы без `--force`. Generated-файлы и runtime-состояние не
коммитятся.

## Установка

```bash
git clone <repo-url> ~/start
cd ~/start
make up
```

Нужен Python 3.11+. `uv` используется при наличии; иначе `scripts/run.sh`
создаёт локальный `.venv`.

## Основные команды

| Команда | Назначение |
|---|---|
| `make up` | синхронизировать AI и Files |
| `make ai` | синхронизировать Claude и Codex |
| `make ai:claude` | синхронизировать только Claude |
| `make ai:codex` | синхронизировать только Codex |
| `make files` | разложить только dotfiles |
| `make manage` | открыть TUI для AI, Files и Commands |

Флаги передаются после `--`:

```bash
make up -- --dry-run
make ai:codex -- --force
```

Полный интерфейс доступен через `uv run start --help`.

## AI provisioning

Общий каталог включает:

- глобальные инструкции и инженерные правила;
- переиспользуемых agents с platform-specific адаптацией;
- глобальные skills из `ai/skills` и project-local skills из `.agents/skills`;
- plugins из pinned git submodules в `contrib/`;
- MCP-серверы, hooks и statusline.

По умолчанию компонент поддерживает обе платформы. Для platform-specific source
используется `platforms = ["claude"]` или `platforms = ["codex"]`.

При `make up` ссылки `skills` в frontmatter агентов проверяются по каталогу целевой
платформы. Для навыка плагина можно указать `plugin:skill`; короткое имя должно быть
однозначным. Недоступные и неоднозначные ссылки выводятся как предупреждения:
установка роли продолжается. Эта проверка не означает автоматическую загрузку
навыка в Codex — обязательное чтение следует задавать в инструкциях агента.

Новый plugin или skills-репозиторий добавляется одной командой:

```bash
uv run start add-submodule <git-url>
```

Команда добавляет сабмодуль в `contrib/`, определяет тип source и регистрирует
его в общем AI-каталоге.

## Dotfiles и scripts

Интерактивные таблицы в разделе Commands поддерживают обновление и фильтры внутри
менеджера. Новая задача подключается через async provider и `commands.tasks.view`;
изменять UI менеджера не требуется. Локальный skill
[`start-add-interactive-task`](.agents/skills/start-add-interactive-task/SKILL.md)
содержит контракт SDK и пример добавления задачи для агента и разработчика.

`[[files.dotfiles]]` связывает файлы и каталоги из `dotfiles/` с `$HOME` либо
вызывает идемпотентный `posthook` для приложений с собственным механизмом
хранения настроек.

`[[commands.tasks]]` описывает разовые команды по операционным системам. Они не
выполняются во время `make up`: запуск происходит только вручную через
`make manage`. Здесь живут обслуживание машины, локальный Ansible provisioning,
PXE и диагностические scripts.

Machine-only MCP и их секреты объявляются через `[[ai.mcp]]` в gitignored
`config.local.toml`.

## Структура

```text
ai/         инструкции, agents, skills, hooks и statusline
.agents/    project-local skills для этого репозитория
dotfiles/   файлы и профили для пользовательского окружения
scripts/    launcher, provisioning и служебные scripts
contrib/    pinned внешние plugins и skills
cli/        менеджер, platform adapters и TUI
```

## Проверка

```bash
uv run python -m unittest discover -s tests -v
uv run start completion --check
make ai -- --dry-run
```
