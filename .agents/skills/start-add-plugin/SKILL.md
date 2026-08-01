---
name: start-add-plugin
description: Добавить в репозиторий start внешний AI-плагин или репозиторий skills как git-сабмодуль и подключить его к Claude Code и OpenAI Codex через общий каталог. Использовать, когда пользователь просит установить или подключить plugin/skills repository, даёт git URL либо просит обновить регистрацию в config.toml.
---

# Добавление AI-плагина или skills-репозитория

Использовать штатную команду:

```bash
uv run start add-submodule <git-url>
```

Она добавляет сабмодуль в `contrib/<name>`, определяет тип источника, регистрирует
его в `[[ai.plugins]]` или `[[ai.skills]]` и синхронизирует установленные backend-ы.

## Плагины

Источник считается плагином при наличии `.claude-plugin/plugin.json` или
`.codex-plugin/plugin.json`, включая plugin root из marketplace manifest.

- Claude-native plugin устанавливается в Claude plugin seed и адаптируется для Codex.
- Если есть оба native manifest, каждый backend использует свой.
- Для Codex-only plugin сначала использовать `--no-install`, затем добавить к
  `[[ai.plugins]]` поле `platforms = ["codex"]` и выполнить `make ai:codex`:
  обратного адаптера в Claude plugin seed нет.

После установки перезапустить соответствующий клиент. Проверить Claude через
`/plugin`, Codex — через `codex plugin list`.

## Skills-репозитории

Если plugin manifest отсутствует, команда ищет папки с `SKILL.md` в корне,
`skills/` и `contrib/*`. При нескольких кандидатах указать:

```bash
uv run start add-submodule <git-url> --skills-subdir <путь>
```

Пустой `--skills-subdir ''` означает корень сабмодуля.

## Флаги

- `--name <имя>` — переопределить имя каталога в `contrib/`.
- `--no-install` — только добавить сабмодуль и запись в `config.toml`.
- `--skills-subdir <путь>` — явно выбрать каталог skills.

Для Git LFS при необходимости отключить загрузку больших объектов:

```bash
GIT_LFS_SKIP_SMUDGE=1 uv run start add-submodule <git-url>
```

## Проверка изменений

Проверить `.gitmodules`, новую запись `[[ai.plugins]]` или `[[ai.skills]]` в
`config.toml` и итог команды. Не коммитить и не пушить без явной просьбы.
