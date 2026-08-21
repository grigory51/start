#!/usr/bin/env bash

set -u

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    bold=$'\033[1m'
    cyan=$'\033[36m'
    green=$'\033[32m'
    yellow=$'\033[33m'
    red=$'\033[31m'
    dim=$'\033[2m'
    reset=$'\033[0m'
else
    bold=""
    cyan=""
    green=""
    yellow=""
    red=""
    dim=""
    reset=""
fi

elapsed_seconds() {
    awk -F '[-:]' '
        NF == 2 { print $1 * 60 + $2 }
        NF == 3 { print $1 * 3600 + $2 * 60 + $3 }
        NF == 4 { print $1 * 86400 + $2 * 3600 + $3 * 60 + $4 }
    ' <<< "$1"
}

age_color() {
    local seconds
    seconds="$(elapsed_seconds "$1")"
    if ((seconds < 3600)); then
        printf '%s' "$green"
    elif ((seconds < 21600)); then
        printf '%s' "$yellow"
    else
        printf '%s' "$red"
    fi
}

process_cwd() {
    local pid="$1"
    local cwd=""

    if [[ -L "/proc/$pid/cwd" ]]; then
        cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
    elif command -v lsof >/dev/null 2>&1; then
        cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
    fi
    printf '%s' "${cwd:-?}"
}

collect_process_tree() {
    local pid="$1"
    local depth="$2"
    local inherited_mcp="$3"
    local process
    local process_pid
    local parent_pid
    local group_pid
    local cpu
    local memory
    local elapsed
    local state
    local command
    local process_kind="$inherited_mcp"
    local children
    local child

    process="$(ps -p "$pid" -o pid=,ppid=,pgid=,%cpu=,%mem=,etime=,state=,command= 2>/dev/null)" || return
    read -r process_pid parent_pid group_pid cpu memory elapsed state command <<< "$process"
    if [[ "$command" =~ [mM][cC][pP] || "$command" =~ [nN][oO][dD][eE]_[rR][eE][pP][lL] ]]; then
        process_kind="mcp"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$depth" "$process_pid" "$parent_pid" "$group_pid" "$cpu" "$memory" \
        "$elapsed" "$state" "$process_kind" "$command"

    children="$(pgrep -P "$pid" 2>/dev/null || true)"
    for child in $children; do
        collect_process_tree "$child" "$((depth + 1))" "$process_kind"
    done
}

print_process_tree() {
    local tree_file="$1"
    local filter="$2"
    local depth
    local process_pid
    local parent_pid
    local group_pid
    local cpu
    local memory
    local elapsed
    local state
    local process_kind
    local command
    local tree
    local elapsed_color

    while IFS=$'\t' read -r depth process_pid parent_pid group_pid cpu memory elapsed state process_kind command; do
        [[ "$process_kind" == "$filter" ]] || continue
        tree=""
        if ((depth > 0)); then
            printf -v tree '%*s↳ ' "$(((depth - 1) * 2))" ""
        fi
        elapsed_color="$(age_color "$elapsed")"
        printf '%s%-7s%s %-7s %-7s %7s %7s %s%-12s%s %-6s %s%s\n' \
            "$cyan" "$process_pid" "$reset" "$parent_pid" "$group_pid" \
            "$cpu" "$memory" "$elapsed_color" "$elapsed" "$reset" "$state" \
            "$tree" "$command"
    done < "$tree_file"
}

codex_pids="$(pgrep -ix codex 2>/dev/null || true)"
if [[ -z "$codex_pids" ]]; then
    echo "Процессы Codex не найдены."
    exit 0
fi

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/codex-process-tree.XXXXXX")"
trap 'rm -rf "$temporary_dir"' EXIT
index_file="$temporary_dir/index.tsv"
: > "$index_file"

for codex_pid in $codex_pids; do
    tree_file="$temporary_dir/$codex_pid.tsv"
    collect_process_tree "$codex_pid" 0 "process" > "$tree_file"
    [[ -s "$tree_file" ]] || continue

    stats="$(awk -F '\t' '
        { cpu += $5; memory += $6 }
        END { printf "%.2f\t%.2f\t%d", memory, cpu, NR }
    ' "$tree_file")"
    IFS=$'\t' read -r total_memory total_cpu process_count <<< "$stats"
    root_elapsed="$(awk -F '\t' 'NR == 1 { print $7 }' "$tree_file")"
    cwd="$(process_cwd "$codex_pid")"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$total_memory" "$total_cpu" "$process_count" "$root_elapsed" "$cwd" "$codex_pid" \
        >> "$index_file"
done

first_group=true
while IFS=$'\t' read -r total_memory total_cpu process_count root_elapsed cwd codex_pid; do
    if [[ "$first_group" == false ]]; then
        echo
    fi
    first_group=false
    elapsed_color="$(age_color "$root_elapsed")"
    printf '%s%sCodex PID %s%s · %s процессов · CPU %s%% · MEM %s%% · %s%s%s\n' \
        "$bold" "$elapsed_color" "$codex_pid" "$reset" "$process_count" \
        "$total_cpu" "$total_memory" "$elapsed_color" "$root_elapsed" "$reset"
    printf '%sCWD: %s%s\n' "$dim" "$cwd" "$reset"
    process_count="$(awk -F '\t' '$9 == "process" { count++ } END { print count + 0 }' "$temporary_dir/$codex_pid.tsv")"
    mcp_count="$(awk -F '\t' '$9 == "mcp" { count++ } END { print count + 0 }' "$temporary_dir/$codex_pid.tsv")"
    printf '\n%sPROCESSES (%s)%s\n' "$bold" "$process_count" "$reset"
    printf '%s%-7s %-7s %-7s %7s %7s %-12s %-6s %s%s\n' \
        "$bold" "PID" "PPID" "PGID" "CPU %" "MEM %" "ELAPSED" "STATE" "COMMAND" "$reset"
    printf '%s\n' '────────────────────────────────────────────────────────────────────────────────'
    print_process_tree "$temporary_dir/$codex_pid.tsv" "process"
    if ((mcp_count > 0)); then
        printf '\n%sMCP SERVERS (%s)%s\n' "$bold" "$mcp_count" "$reset"
        printf '%s%-7s %-7s %-7s %7s %7s %-12s %-6s %s%s\n' \
            "$bold" "PID" "PPID" "PGID" "CPU %" "MEM %" "ELAPSED" "STATE" "COMMAND" "$reset"
        printf '%s\n' '────────────────────────────────────────────────────────────────────────────────'
        print_process_tree "$temporary_dir/$codex_pid.tsv" "mcp"
    fi
done < <(sort -t $'\t' -k1,1nr "$index_file")

printf '\n%sВозраст: %sзелёный <1ч%s · %sжёлтый 1–6ч%s · %sкрасный ≥6ч%s\n' \
    "$dim" "$green" "$reset$dim" "$yellow" "$reset$dim" "$red" "$reset"
