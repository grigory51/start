.PHONY: up install dry force

# up — полная синхронизация: обновить сабмодули и разложить symlink'и.
up:
	git submodule update --init --recursive
	./install.py

# install — только symlink'и (сабмодули не трогаем).
install:
	./install.py

# dry — показать план без изменений.
dry:
	./install.py --dry-run

# force — перезаписать чужие файлы/симлинки (с бэкапом .bak).
force:
	./install.py --force
