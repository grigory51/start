.PHONY: up claude dotfiles manage seed settings

# up — полная синхронизация обоих доменов: Claude Code + dotfiles.
up:
	uv run start up

# claude — только домен Claude Code (~/.claude): сабмодули + seed + symlink'и + settings.
claude:
	uv run start up --only claude

# dotfiles — только dotfiles ($HOME): симлинки [[dotfiles]] (без сабмодулей/seed/settings).
dotfiles:
	uv run start up --only dotfiles

# manage — TUI: просмотр агентов/плагинов, включение/выключение скилов.
manage:
	uv run start manage

# seed — пересобрать plugin seed (.seed/) + merge settings.
seed:
	uv run start seed

# settings — показать diff managed-ключей settings.json (без записи).
settings:
	uv run start settings --dry-run
