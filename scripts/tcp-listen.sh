#!/usr/bin/env bash
# tcp-listen.sh — слушающие TCP-порты (lsof). Запрашивает порт:
#   пусто (Enter) — все слушатели;
#   число         — фильтр нативным `-iTCP:<port>` (точнее grep: не ловит
#                   совпадения в PID/адресах).
# Запускается из домена «Команды» (TUI сворачивается → реальный терминал,
# поэтому read и sudo-пароль работают).

read -rp "Порт (Enter — все): " port
if [ -n "$port" ]; then
    sudo lsof -nP -iTCP:"$port" -sTCP:LISTEN || echo "Ничего не слушает на порту $port."
else
    sudo lsof -nP -iTCP -sTCP:LISTEN
fi
