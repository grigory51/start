# Netbook cheatsheet

Managed by the `start` repo (Ansible role `netbook`). This file is regenerated on every
provision. Re-run provisioning:

    bash ~/start/scripts/provision.sh netbook      # or via: make manage -> Commands -> Provision: netbook

## Wi-Fi
    nmtui                                   # TUI: Activate a connection -> pick SSID -> password
    nmcli device wifi list
    nmcli device wifi connect "<SSID>" --ask

## Terminal multiplexer (tmux) — sessions survive disconnects, gives scrollback + mouse
    tmux                 # start
    tmux a               # reattach last session
    Ctrl-b  [            # scrollback mode (arrows/PgUp, q to exit)
    Ctrl-b  % | "        # split pane vertical | horizontal
    Ctrl-b  <arrows>     # move between panes
    Ctrl-b  d            # detach
Mouse: wheel scrolls, drag selects (mouse enabled).

## Files & search
    mc                   # Midnight Commander (file manager, mouse via gpm)
    Ctrl-R               # fuzzy history search (fzf)
    Ctrl-T               # fuzzy file picker (fzf)
    rg <pattern>         # ripgrep (fast grep)
    fdfind <name>        # fd (fast find)
    batcat <file>        # bat (cat with syntax highlight)
    ncdu                 # disk usage browser

## Network debug (sbin is in PATH via ~/.bashrc)
    ip a ; ip r ; ss -tulpn
    tcpdump -ni <iface> 'port 67 or port 68'
    mtr <host> ; nmap <host> ; ethtool <iface> ; iperf3 -c <host>

## 3G / cellular modem
    mmcli -L             # list modems (ModemManager)
    mmcli -m 0           # modem details
    nmtui                # add a mobile-broadband connection

## Remote access
    mosh user@host       # SSH that survives IP changes / high latency

## Console tweaks
    sudo setfont Uni2-TerminusBold16     # bigger font with Cyrillic
    fbterm                               # nicer framebuffer terminal (needs 'video' group)
    sudo loadkeys us                     # fix keyboard layout (e.g. '@' typing)

## Editors
    micro <file>         # simple, intuitive editor
    vim <file>

## What is installed
Terminal: tmux mosh mc fzf ripgrep fd-find bat htop ncdu bash-completion micro fbterm gpm
Network:  network-manager tcpdump iproute2 mtr-tiny nmap ethtool iperf3 usb-modeswitch modemmanager
Tooling:  git make python3-venv python3-pip  (so `make` works via scripts/run.sh without uv)
Locale/console: en_US.UTF-8 + ru_RU.UTF-8, Terminus console font (Cyrillic on tty)
