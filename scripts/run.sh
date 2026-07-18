#!/usr/bin/env bash
# run.sh — запустить CLI (`start`), сам разобравшись с рантаймом:
#   1) uv (предпочтительно) — поднимает venv и зависимости из pyproject сам;
#   2) fallback без uv — локальный .venv (python3 -m venv + pip install -e .), затем start.
# Нужен, чтобы `make …` работал и на машинах без uv (напр. Debian-нетбук).
set -euo pipefail

# Резолвим путь скрипта сквозь симлинки (напр. ~/.local/bin/start -> scripts/run.sh),
# чтобы REPO указывал на репозиторий, а не на каталог симлинка.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [ "${SOURCE#/}" = "$SOURCE" ] && SOURCE="$DIR/$SOURCE"
done
REPO="$(cd -P "$(dirname "$SOURCE")/.." && pwd)"
cd "$REPO"

# 1) uv — если есть, он сам всё поднимет.
if command -v uv >/dev/null 2>&1; then
    exec uv run start "$@"
fi

# 2) fallback: локальный .venv.
VENV="$REPO/.venv"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
    command -v python3 >/dev/null 2>&1 || { echo "Neither uv nor python3 - install python3." >&2; exit 1; }
    echo "==> uv not found - creating local .venv (one-time)..." >&2
    if ! python3 -m venv "$VENV" 2>/dev/null; then
        echo "Failed to create venv. On Debian: sudo apt install python3-venv python3-pip" >&2
        exit 1
    fi
fi

# Зависимости ставим (pip install -e .) только когда venv свежий или менялся pyproject —
# по стемп-файлу с mtime. Так не дёргаем pip на каждый запуск и не хардкодим имена
# зависимостей (источник — pyproject). Стемп ставится ТОЛЬКО после успеха, поэтому
# прерванная/офлайн-установка с прошлого раза сама доедет на следующем запуске.
STAMP="$VENV/.installed"
if [ ! -f "$STAMP" ] || [ "$REPO/pyproject.toml" -nt "$STAMP" ]; then
    echo "==> Installing dependencies into .venv..." >&2
    "$PY" -m pip install -q --upgrade pip
    if "$PY" -m pip install -q -e "$REPO"; then
        touch "$STAMP"
    else
        echo "Failed to install dependencies (no internet? see error below):" >&2
        "$PY" -m pip install -e "$REPO" || true   # повтор без -q — показать причину
        exit 1
    fi
fi
exec "$PY" -m cli "$@"
