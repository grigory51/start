"""Генерация bash-дополнения для CLI `start` из единого источника.

Списки берутся прямо из кода — без ручного дублирования:
  • подкоманды (включая алиасы) — интроспекция парсера argparse (cli/__main__.py);
  • разделы TUI — M_TARGETS (cli/sections.py).

Файл `scripts/start-completion.bash` — производный артефакт: пересобирается командой
`start completion`. `start completion --check` сверяет его с текущим CLI (для pre-commit/CI).
"""

from __future__ import annotations

import argparse

from .config import REPO_DIR
from .sections import M_TARGETS

COMPLETION_PATH = REPO_DIR / "scripts" / "start-completion.bash"

# Структура функции фиксированная; подставляются только два списка слов. Двойные
# фигурные скобки — экранирование для str.format (в выходном bash это одинарные).
_TEMPLATE = """\
# start-completion.bash — bash-автодополнение для CLI `start`.
#
# СГЕНЕРИРОВАН автоматически: `start completion`. Руками не править — источник:
# парсер argparse (cli/__main__.py) и M_TARGETS (cli/sections.py).
#
# Установка: `make up` симлинкует файл в
# ~/.local/share/bash-completion/completions/start (bash-completion@2 подхватит его
# по имени команды). Без bash-completion — `source` этот файл в ~/.bashrc.

_start_completion() {{
    local cur subcmds sections
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    subcmds="{subcmds}"
    sections="{sections}"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$subcmds" -- "$cur") )
        return
    fi
    case "${{COMP_WORDS[1]}}" in
        m|manage)
            [ "$COMP_CWORD" -eq 2 ] && COMPREPLY=( $(compgen -W "$sections" -- "$cur") )
            ;;
    esac
}}
complete -F _start_completion start
"""


def generate() -> str:
    """Собрать текст completion-скрипта из текущего CLI."""
    from .__main__ import build_parser  # ленивый импорт — избегаем циклической зависимости

    parser = build_parser()
    sub_action = next(a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction))
    subcmds = " ".join(sub_action.choices)   # включает алиасы (m) и completion
    sections = " ".join(M_TARGETS)
    return _TEMPLATE.format(subcmds=subcmds, sections=sections)


def write(*, check: bool = False) -> int:
    """Сгенерировать completion. check=True — не писать, только сверить.

    Возвращает rc: 0 — записан/в синхроне; 1 — при check устарел.
    """
    content = generate()
    current = COMPLETION_PATH.read_text() if COMPLETION_PATH.exists() else ""
    if check:
        if content != current:
            print(f"! {COMPLETION_PATH.name} устарел — обнови: start completion")
            return 1
        print(f"= {COMPLETION_PATH.name} в синхроне с CLI.")
        return 0
    if content == current:
        print(f"= {COMPLETION_PATH.name} без изменений.")
        return 0
    COMPLETION_PATH.write_text(content)
    print(f"+ {COMPLETION_PATH} обновлён.")
    return 0
