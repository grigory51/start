---
name: static-publish
description: Публикует статику (HTML-файл или папку с готовым сайтом) в Yandex Object Storage — бакет ozhegov.name, папка site/, отдаётся по https://storage.yandexcloud.net/ozhegov.name/site/.... Каждый вызов кладёт всё в свежий префикс site/<имя>/, не перетирая прежнее. Используй, когда нужно выложить/зашерить статическую страницу, HTML-отчёт, лендинг, превью или папку со статикой по публичной ссылке. Триггеры: опубликовать страницу, выложить html, залить статику в s3, static publish, share html, deploy static page, публичная ссылка на отчёт.
---

# static-publish

Заливает статику в бакет `ozhegov.name` под `site/<имя>/` и отдаёт публичные URL.

## Запуск

```bash
scripts/publish.sh <путь-к-файлу-или-папке> [имя-префикса]
```

- `<путь>` — `.html`-файл **или** папка с готовой статикой (html + css/img/js).
- `[имя-префикса]` — опционально. По умолчанию — имя файла без расширения (для
  файла) или имя папки (для папки), приведённое к slug `[a-z0-9.-]`.

Примеры:

```bash
# один файл → site/seo-kts-tech/seo-kts.tech.html
scripts/publish.sh /private/tmp/a/seo-kts.tech.html

# с явным именем префикса → site/kts-report/index.html
scripts/publish.sh /private/tmp/a/index.html kts-report

# папка целиком → site/mysite/... (все файлы внутри)
scripts/publish.sh /private/tmp/mysite
```

После заливки скрипт печатает публичный URL каждого файла, напр.:

```
  https://storage.yandexcloud.net/ozhegov.name/site/seo-kts-tech/seo-kts.tech.html
```

## Как это работает

1. **Имя префикса.** Каждый вызов → отдельная папка `site/<имя>/`, чтобы не было
   бардака и перезаписи. Имя берётся из аргумента или из источника (slug).
2. **Коллизии.** Если `site/<имя>/` уже занят в бакете (проверка через
   `yc storage s3api list-objects --prefix`), к имени дописывается короткий хеш:
   `site/<имя>-a1b2c3/`.
3. **Заливка.** Файл → `yc storage s3 cp`; папка → `yc storage s3 cp --recursive`.
4. **Абсолютные пути.** Перед заливкой `.html` грепается на `href`/`src="/..."`.
   Объект лежит под `/site/<имя>/`, поэтому абсолютный путь `/style.css` уйдёт в
   корень домена и сломается. Скрипт **предупреждает** (не правит файл) — сделай
   пути относительными вручную: `style.css`, `img/x.png`.

## Важно про URL и index.html

На бакете **не включён** website-hosting, поэтому `…/site/<имя>/` (со слешем на
конце) **не отрендерит** `index.html` — нужен полный путь до файла
(`…/site/<имя>/index.html`). Имя файла в URL = его оригинальное имя на диске.

## Требования

- Установлен и настроен `yc` (Yandex Cloud CLI) с доступом на запись в бакет
  `ozhegov.name`. Проверка: `yc storage s3 cp --help`.
