.PHONY: up manage

# up — полная синхронизация: обновить сабмодули и разложить symlink'и.
up:
	uv run claude-agents up

# manage — TUI: просмотр агентов, включение/выключение скилов.
manage:
	uv run claude-agents manage
