#!/usr/bin/env bash
# publish.sh — залить статику (файл или папку) в бакет ozhegov.name под site/<имя>/
# и вывести публичные URL. Каждый вызов кладёт всё в свежий префикс site/<имя>/,
# не перетирая чужое: если префикс занят — дописывает короткий хеш.
#
# Использование:
#   publish.sh <путь-к-файлу-или-папке> [имя-префикса]
#
#   <путь>          — .html-файл либо папка с готовой статикой.
#   [имя-префикса]  — опционально. По умолчанию: имя файла без расширения (файл)
#                     или имя папки (папка), приведённое к slug.
#
# Итог: site/<имя>/<оригинальные имена файлов> в бакете; печатает URL каждого.
set -euo pipefail

BUCKET="ozhegov.name"
PREFIX_ROOT="site"
PUBLIC_BASE="https://storage.yandexcloud.net/${BUCKET}/${PREFIX_ROOT}"

SRC="${1:?нужен путь к файлу или папке}"
NAME_ARG="${2:-}"

if [[ ! -e "$SRC" ]]; then
  echo "ошибка: не найден путь '$SRC'" >&2
  exit 1
fi

# slug: нижний регистр, не-[a-z0-9.-] → '-', схлопнуть и обрезать дефисы.
slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9.-]+/-/g; s/-+/-/g; s/^-+//; s/-+$//'
}

# Базовое имя префикса: из аргумента, иначе из источника.
if [[ -n "$NAME_ARG" ]]; then
  BASE="$(slugify "$NAME_ARG")"
elif [[ -d "$SRC" ]]; then
  BASE="$(slugify "$(basename "$SRC")")"
else
  fname="$(basename "$SRC")"
  BASE="$(slugify "${fname%.*}")"   # имя файла без последнего расширения
fi
[[ -n "$BASE" ]] || BASE="page"

# Занят ли префикс site/<имя>/ в бакете (есть хоть один ключ).
prefix_taken() {
  local p="$1"
  local out
  out="$(yc storage s3api list-objects \
           --bucket "$BUCKET" --prefix "${PREFIX_ROOT}/${p}/" --max-keys 1 2>/dev/null || true)"
  # Пустой ответ или без "key" → свободно.
  grep -q '"key"' <<<"$out"
}

# Подобрать свободное имя: BASE, иначе BASE-<хеш>.
NAME="$BASE"
if prefix_taken "$NAME"; then
  h="$(printf '%s' "${SRC}$(date +%s)" | shasum | cut -c1-6)"
  NAME="${BASE}-${h}"
  echo "префикс site/${BASE}/ занят → использую site/${NAME}/" >&2
fi

DEST="s3://${BUCKET}/${PREFIX_ROOT}/${NAME}/"

# Предупредить про абсолютные пути в HTML (на storage.yandexcloud.net сломаются:
# объект лежит под /site/<имя>/, а href="/style.css" уйдёт в корень домена).
warn_abs_paths() {
  local f="$1"
  local hits
  hits="$(grep -nEi '(href|src)[[:space:]]*=[[:space:]]*"/[^/]' "$f" 2>/dev/null || true)"
  if [[ -n "$hits" ]]; then
    echo "⚠ в $f есть абсолютные пути (href/src=\"/...\") — на storage они сломаются," >&2
    echo "  сделай их относительными (\"style.css\", \"img/x.png\"):" >&2
    sed 's/^/    /' <<<"$hits" >&2
  fi
}

UPLOADED=()   # ключи в бакете для печати URL

if [[ -d "$SRC" ]]; then
  while IFS= read -r -d '' f; do
    [[ "$f" == *.html ]] && warn_abs_paths "$f"
  done < <(find "$SRC" -type f -print0)

  echo "заливаю папку $SRC → $DEST"
  yc storage s3 cp --recursive "$SRC" "$DEST"

  while IFS= read -r -d '' f; do
    rel="${f#"$SRC"/}"
    UPLOADED+=("$rel")
  done < <(find "$SRC" -type f -print0)
else
  [[ "$SRC" == *.html ]] && warn_abs_paths "$SRC"
  fname="$(basename "$SRC")"
  echo "заливаю файл $SRC → ${DEST}${fname}"
  yc storage s3 cp "$SRC" "${DEST}${fname}"
  UPLOADED+=("$fname")
fi

echo
echo "готово. публичные URL:"
for rel in "${UPLOADED[@]}"; do
  echo "  ${PUBLIC_BASE}/${NAME}/${rel}"
done
