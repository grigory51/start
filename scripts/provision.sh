#!/usr/bin/env bash
# provision.sh <role> — применить Ansible-роль ЛОКАЛЬНО (-c local), без SSH.
#
# Тонкая обёртка над scripts/ansible: логика ролей — в scripts/ansible/roles/<role>,
# плейбук на роль — scripts/ansible/<role>.yml. Здесь только: убедиться, что ansible
# есть, и прогнать плейбук роли против localhost. Идемпотентно.
#
# Только Linux (запускается на самой машине). Из домена «Команды» (run.linux) или вручную:
#   bash scripts/provision.sh netbook
set -euo pipefail

[ "$(uname -s)" = Linux ] || { echo "Linux only. Current OS: $(uname -s)"; exit 1; }

ROLE="${1:-}"
[ -n "$ROLE" ] || { echo "Usage: provision.sh <role>  (e.g. netbook)"; exit 2; }

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DIR="$REPO/scripts/ansible"
if [ ! -f "$DIR/$ROLE.yml" ]; then
    echo "No playbook $DIR/$ROLE.yml. Available roles:"
    ls "$DIR"/*.yml 2>/dev/null | xargs -n1 basename | sed 's/\.yml$//' || true
    exit 2
fi

if ! command -v ansible-playbook >/dev/null 2>&1; then
    echo "==> ansible not found - installing (apt)..."
    sudo apt-get update && sudo apt-get install -y ansible
fi

# -K (--ask-become-pass) нужен для become-задач (apt/сервисы); под root не нужен.
BECOME_ARGS=(-K)
[ "$(id -u)" = 0 ] && BECOME_ARGS=()

cd "$DIR"
# -i 'localhost,' -c local — прогон против самой машины без SSH.
exec ansible-playbook -i 'localhost,' -c local "$ROLE.yml" "${BECOME_ARGS[@]}"
