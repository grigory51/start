#!/usr/bin/env bash
# pxe-netboot.sh — локальный PXE-сервер для установки машины БЕЗ интернета на ней.
#
# Поднимает контейнер netboot.xyz (HTTP/меню/ассеты, docker-compose рядом) и нативный
# dnsmasq на хосте в режиме proxyDHCP + TFTP. Целевая машина в том же L2-сегменте грузится
# по сети, получает локальное меню netboot.xyz и ставится из предскачанных ассетов —
# интернет ей не нужен.
#
# Запускается из домена «Команды» TUI (uv run start manage → F2 «Команды»): env REPO —
# корень репо, cwd — тоже репо. Можно и вручную: bash scripts/pxe-netboot.sh
#
# Роли под OrbStack (Linux-VM без bridged-L2 к en0): dnsmasq — нативно на хосте (broadcast
# proxyDHCP по физическому интерфейсу), контейнер — unicast HTTP через published-порты
# (нужно включить «Expose ports to LAN» в OrbStack). См. scripts/pxe/docker-compose.yml.
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
COMPOSE_FILE="$REPO/scripts/pxe/docker-compose.yml"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/start-pxe"
CONFIG_DIR="$CACHE/config"
NGINX_PORT=8080          # host-порт nginx контейнера (см. docker-compose.yml)
WEBUI_URL="http://localhost:3000"

die() { echo "Ошибка: $*" >&2; exit 1; }

# --- 1. Preflight: внешние зависимости ---------------------------------------
command -v docker  >/dev/null || die "нет docker (нужен OrbStack/Docker). Поставь OrbStack: https://orbstack.dev"
command -v dnsmasq >/dev/null || die "нет dnsmasq. Поставь: brew install dnsmasq"
command -v curl    >/dev/null || die "нет curl."
docker compose version >/dev/null 2>&1 || die "нет 'docker compose' (v2)."

# --- 2. Контейнер netboot.xyz ------------------------------------------------
echo "==> Поднимаю контейнер netboot.xyz…"
docker compose -f "$COMPOSE_FILE" up -d

echo -n "==> Жду webUI ($WEBUI_URL) "
for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "$WEBUI_URL" 2>/dev/null; then ok=1; break; fi
    echo -n "."; sleep 1
done
echo
[ "${ok:-}" = 1 ] || echo "  ! webUI пока не ответил — контейнер мог ещё стартовать; продолжаю."

# --- 3. Проверка предскачанных загрузчиков -----------------------------------
# dnsmasq отдаёт по TFTP локально-сконфигуренные iPXE-бинарники, сгенерированные
# контейнером в config-томе. Нет их → нужен разовый шаг локальной настройки в webUI.
KPXE_PATH="$(find "$CONFIG_DIR" -name 'netboot.xyz.kpxe' 2>/dev/null | head -n1 || true)"
if [ -z "$KPXE_PATH" ]; then
    cat <<EOF

  ! Локальные загрузчики (netboot.xyz.kpxe/.efi) в $CONFIG_DIR не найдены.
    Разовая настройка (у Mac есть интернет):
      1. Открой webUI:  $WEBUI_URL
      2. Включи локальные загрузочные файлы (Local boot files / Local assets).
      3. Скачай нужные дистрибутивы — они лягут в $CACHE/assets и будут раздаваться офлайн.
    Затем запусти команду снова.
EOF
    exit 1
fi
TFTP_ROOT="$(dirname "$KPXE_PATH")"
echo "==> TFTP-root: $TFTP_ROOT"

# --- 4. Выбор сетевого интерфейса --------------------------------------------
# Кандидаты — интерфейсы с IPv4, кроме loopback/виртуальных (utun/awdl/bridge/…).
IFACES=()
for i in $(ifconfig -l); do
    case "$i" in
        lo*|gif*|stf*|utun*|awdl*|llw*|bridge*|anpi*|ap1) continue ;;
    esac
    ip="$(ipconfig getifaddr "$i" 2>/dev/null || true)"
    if [ -n "$ip" ]; then IFACES+=("$i"); fi
done
[ "${#IFACES[@]}" -gt 0 ] || die "нет активного интерфейса с IPv4."
if [ "${#IFACES[@]}" -eq 1 ]; then
    IFACE="${IFACES[0]}"
else
    echo "Выбери интерфейс в сегмент с целевой машиной:"
    select IFACE in "${IFACES[@]}"; do
        if [ -n "${IFACE:-}" ]; then break; fi
    done
fi

# Вычисляем network-адрес подсети интерфейса для --dhcp-range=<net>,proxy.
IP="$(ipconfig getifaddr "$IFACE")"
MASK_HEX="$(ifconfig "$IFACE" | awk '/inet /{print $4; exit}')"   # напр. 0xffffff00
IFS=. read -r a b c d <<<"$IP"
m=$((MASK_HEX))
NET="$(( a & ((m>>24)&255) )).$(( b & ((m>>16)&255) )).$(( c & ((m>>8)&255) )).$(( d & (m&255) ))"

# --- 5. Инструктаж перед стартом ---------------------------------------------
cat <<EOF

==> Готово к запуску PXE:
      интерфейс : $IFACE  (IP $IP, подсеть $NET)
      TFTP      : dnsmasq на хосте, root=$TFTP_ROOT
      HTTP/меню : контейнер netboot.xyz на <$IP>:$NGINX_PORT  (нужно «Expose ports to LAN» в OrbStack)
    На целевой машине: загрузка по сети (F12 / Boot menu → PXE/Network) в этом сегменте.
    Замечания:
      • Secure Boot может блокировать неподписанный iPXE — отключи в UEFI, если не грузится.
      • macOS Internet Sharing поднимает bootpd на udp/67 → конфликт: выключи его.
    Ctrl-C — остановить dnsmasq (контейнер и кеш остаются).

EOF

# --- 6. dnsmasq: proxyDHCP + TFTP (foreground, эфемерно) ---------------------
trap 'echo; echo "==> dnsmasq остановлен. Контейнер netboot.xyz оставлен (docker compose -f \"$COMPOSE_FILE\" down — чтобы погасить)."' INT TERM EXIT

    # Без --bind-interfaces: на macOS он бы забиндил unicast-адрес интерфейса, а
    # DHCP-DISCOVER летит broadcast'ом на 255.255.255.255 — на такой сокет не приходит
    # (dnsmasq не видит запрос). Wildcard-бинд (default) + --interface ловит broadcast.
sudo dnsmasq --no-daemon --port=0 --log-dhcp \
    --interface="$IFACE" \
    --dhcp-range="$NET,proxy" \
    --enable-tftp --tftp-root="$TFTP_ROOT" \
    --pxe-service=x86PC,"PXE (BIOS)",netboot.xyz.kpxe \
    --pxe-service=X86-64_EFI,"PXE (UEFI)",netboot.xyz.efi \
    --pxe-service=BC_EFI,"PXE (UEFI)",netboot.xyz.efi
