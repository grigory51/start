.PHONY: up manage seed settings

# up — полная синхронизация: сабмодули + плагины (seed) + symlink'и + settings.
up:
	uv run claude-agents up

# manage — TUI: просмотр агентов/плагинов, включение/выключение скилов.
manage:
	uv run claude-agents manage

# seed — пересобрать plugin seed (.seed/) + merge settings.
seed:
	uv run claude-agents seed

# settings — показать diff managed-ключей settings.json (без записи).
settings:
	uv run claude-agents settings --dry-run
