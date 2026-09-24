# start-completion.bash — bash-автодополнение для CLI `start`.
#
# СГЕНЕРИРОВАН автоматически: `start completion`. Руками не править — источник:
# парсер argparse (cli/__main__.py) и M_TARGETS (cli/sections.py).
#
# Установка: `make up` симлинкует файл в
# ~/.local/share/bash-completion/completions/start (bash-completion@2 подхватит его
# по имени команды). Без bash-completion — `source` этот файл в ~/.bashrc.

_start_completion() {
    local cur subcmds index
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    subcmds="up settings seed tab:ai tab:agents tab:skills tab:plugins tab:mcp tab:status tab:files tab:commands add-submodule completion"

    # Bash может разбивать tab:skills на отдельные слова по двоеточию.
    if [ "${COMP_WORDS[1]}" = tab ] && [ "${COMP_WORDS[2]}" = : ] && [ "$COMP_CWORD" -le 3 ]; then
        cur="tab:${COMP_WORDS[3]}"
    elif [ "$COMP_CWORD" -ne 1 ]; then
        return
    fi
    if [ "$COMP_CWORD" -ge 1 ]; then
        COMPREPLY=( $(compgen -W "$subcmds" -- "$cur") )
        if [[ "$COMP_WORDBREAKS" == *:* && "$cur" == *:* ]]; then
            for index in "${!COMPREPLY[@]}"; do
                COMPREPLY[$index]="${COMPREPLY[$index]#*:}"
            done
        fi
        return
    fi

}
complete -F _start_completion start
