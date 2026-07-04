.PHONY: up claude dotfiles manage seed settings

# Флаги для start пробрасываются после `--`, напр.:
#   make up -- --force
#   make claude -- --dry-run
# Всё, что не имя цели, попадает в ARGS и уходит в команду; лишние goals гасятся
# no-op правилом ниже, чтобы make не ругался «No rule to make target».
ARGS := $(filter-out up claude dotfiles manage seed settings,$(MAKECMDGOALS))

# up — полная синхронизация обоих доменов: Claude Code + dotfiles.
up:
	uv run start up $(ARGS)

# claude — только домен Claude Code (~/.claude): сабмодули + seed + symlink'и + settings.
claude:
	uv run start up --only claude $(ARGS)

# dotfiles — только dotfiles ($HOME): симлинки [[dotfiles]] (без сабмодулей/seed/settings).
dotfiles:
	uv run start up --only dotfiles $(ARGS)

# manage — TUI: просмотр агентов/плагинов, включение/выключение скилов.
manage:
	uv run start manage $(ARGS)

# seed — пересобрать plugin seed (.seed/) + merge settings.
seed:
	uv run start seed $(ARGS)

# settings — показать diff managed-ключей settings.json (без записи).
settings:
	uv run start settings --dry-run $(ARGS)

# Проглотить проброшенные флаги (goals после `--`), чтобы make не искал под них цель.
ifneq ($(ARGS),)
$(ARGS):
	@:
endif
