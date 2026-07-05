---
name: start-add-plugin
description: Как добавить в этот репозиторий (start) новый нативный плагин Claude Code из git-репозитория — одной командой, без переизучения кодовой базы. Описывает штатный путь `uv run start add-submodule <url>`, автодетект `.claude-plugin/`, сборку seed, проверку и типовые грабли (LFS, имя плагина, включение/выключение). Используй, когда пользователь просит «поставить/добавить плагин», «подключить skills-репу как плагин», «add plugin», даёт ссылку на GitHub-репозиторий с `.claude-plugin/`.
---

# add-plugin — добавление нативного плагина Claude Code

Плагины в этом репо — git-сабмодули в `contrib/`, зарегистрированные как `[[plugins]]`
в версионном `config.toml`. Claude Code подключается к ним не симлинками, а через
**plugin seed** (`.seed/`), который пересобирается на каждом `up`. Механику менять
не нужно — есть штатная команда.

## Основной путь (одна команда)

```bash
uv run start add-submodule <git-url>
```

Что делает автоматически:

1. `git submodule add <url> contrib/<name>` (имя = basename URL без `.git`).
2. **Автодетект `.claude-plugin/`** в корне сабмодуля → регистрирует источник как
   `[[plugins]]` (а не `[[skills]]`) через `config.add_plugin_source`.
3. `run_up(skip_submodules=True)` — пересобирает seed (`marketplace add` + `install`
   каждого включённого плагина) и merge `~/.claude/settings.json`.
4. Печатает итоговый ref, напр. `✓ contrib/<name> подключён как нативный плагин
   (<plugin>@<marketplace>), плагин зарегистрирован`.

Ручных правок `config.toml`, `.gitmodules`, `settings.json` не требуется.

### Флаги

- `--name <имя>` — переопределить имя папки в `contrib/`.
- `--no-install` — только зарегистрировать, symlink'и/seed не раскатывать (сам
  потом `uv run start up` или `make up`).
- `--skills-subdir` — для **skills**-репозиториев (без `.claude-plugin/`); для
  плагинов не нужен.

## После добавления

- **Перезапустить `claude`** — seed читается только на старте сессии.
- Проверить: `/plugin` внутри сессии (или `/agents`, `/help` для команд плагина).

## Проверка и грабли

- **Имя плагина.** Ref = `<plugins[0].name>@<name>` из
  `<repo>/.claude-plugin/marketplace.json`. Свериться заранее:
  ```bash
  curl -s "https://raw.githubusercontent.com/<owner>/<repo>/main/.claude-plugin/marketplace.json" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['name'], [p['name'] for p in d.get('plugins',[])])"
  ```
- **Один плагin на marketplace.** Seed берёт только `plugins[0]`. Если в marketplace
  несколько плагинов и нужен не первый — задать `plugin`/`marketplace` вручную в
  записи `[[plugins]]` (они переопределяют манифест).
- **Манифест без имени.** Если `add-submodule` пишет «не удалось определить
  marketplace/plugin» — дописать поля `plugin =` / `marketplace =` в `[[plugins]]`
  и `uv run start seed`.
- **Тяжёлые репы (Git LFS).** Репозитории с GIF/бинарями в LFS клонировать с
  пропуском смадж-фильтра, иначе сабмодуль тянет гигабайты:
  ```bash
  GIT_LFS_SKIP_SMUDGE=1 uv run start add-submodule <url>
  ```
- **SessionStart-хуки.** Если плагин их имеет, seed печатает `⚠ ...: SessionStart-хук
  выполнится при старте CC`. Проверить, что именно исполнится, до перезапуска.

## Включение / выключение

- Совсем/для всех машин — поле `enabled` в `[[plugins]]` (`config.toml`), затем
  `uv run start seed`. Плагин атомарен: `true`/`false` целиком.
- Только на текущей машине (без коммита) — overlay `[local.plugins]` в
  `config.local.toml` (gitignore): `"contrib/<name>" = false`.
- Интерактивно — `make manage` → вкладка «Плагины», Space/`t` toggle.

## Skills-репозиторий (без `.claude-plugin/`)

Та же команда `add-submodule` регистрирует его как `[[skills]]` с автодетектом
подпапки (`./`, `./skills`, `./contrib/*`). При неоднозначности — указать
`--skills-subdir <путь>` (`''` = корень).

## Коммит

`add-submodule` меняет `.gitmodules`, `config.toml` и добавляет сабмодуль. Итог
закоммитить — иначе на другой машине `make up` плагин не увидит.
