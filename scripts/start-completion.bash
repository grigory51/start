# start-completion.bash — bash-автодополнение для CLI `start`.
#
# СГЕНЕРИРОВАН автоматически: `start completion`. Руками не править — источник:
# парсер argparse (cli/__main__.py) и M_TARGETS (cli/sections.py).
#
# Установка: `make up` симлинкует файл в
# ~/.local/share/bash-completion/completions/start (bash-completion@2 подхватит его
# по имени команды). Без bash-completion — `source` этот файл в ~/.bashrc.

_start_completion() {
    local cur subcmds sections
    cur="${COMP_WORDS[COMP_CWORD]}"
    subcmds="up settings seed manage m add-submodule completion"
    sections="claude agents skills plugins mcp files commands scripts"

    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$subcmds" -- "$cur") )
        return
    fi
    case "${COMP_WORDS[1]}" in
        m|manage)
            [ "$COMP_CWORD" -eq 2 ] && COMPREPLY=( $(compgen -W "$sections" -- "$cur") )
            ;;
    esac
}
complete -F _start_completion start
