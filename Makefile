.PHONY: up claude files manage seed settings

# Флаги для start пробрасываются после `--`, напр.:
#   make up -- --force
#   make claude -- --dry-run
# Всё, что не имя цели, попадает в ARGS и уходит в команду; лишние goals гасятся
# no-op правилом ниже, чтобы make не ругался «No rule to make target».
ARGS := $(filter-out up claude files manage seed settings,$(MAKECMDGOALS))

# START — обёртка запуска CLI: сама выбирает uv, а без него поднимает локальный .venv
# (см. scripts/run.sh). Чтобы `make …` работал и на машинах без uv (напр. Debian-нетбук).
START := ./scripts/run.sh

# up — полная синхронизация обоих доменов: Claude + Files.
up:
	$(START) up $(ARGS)

# claude — только домен Claude (~/.claude): сабмодули + seed + symlink'и + settings.
claude:
	$(START) up --only claude $(ARGS)

# files — только домен Files ($HOME): симлинки [[dotfiles]] (без сабмодулей/seed/settings).
files:
	$(START) up --only files $(ARGS)

# manage — TUI: просмотр агентов/плагинов, включение/выключение скилов.
manage:
	$(START) manage $(ARGS)

# seed — пересобрать plugin seed (.seed/) + merge settings.
seed:
	$(START) seed $(ARGS)

# settings — показать diff managed-ключей settings.json (без записи).
settings:
	$(START) settings --dry-run $(ARGS)

# Проглотить проброшенные флаги (goals после `--`), чтобы make не искал под них цель.
ifneq ($(ARGS),)
$(ARGS):
	@:
endif
