# Tooling and MCP selection

Подключай инструмент только под реальный источник данных. Пустой MCP без inventory и credentials создаёт ошибки старта и не усиливает диагностику.

## Выбор инструмента

| Задача | Предпочтение | Ограничение |
|---|---|---|
| Source of Truth/IPAM/DCIM | NetBox API или read-only NetBox MCP | Token только read-only; live state отдельно |
| Cisco state/test automation | pyATS + Genie | Лучшее покрытие Cisco; third-party support проверять на конкретной NOS |
| Juniper | Junos PyEZ/NETCONF; Juniper Junos MCP | MCP умеет commit, поэтому write tools требуют отдельного разрешения |
| Huawei | NETCONF/YANG через `ncclient`, controller API | Capability и commit semantics зависят от family/version |
| MikroTik | RouterOS REST/API over TLS; SSH fallback | Изменения immediate; отдельный least-privilege user |
| Универсальный SSH | Netmiko, при fleet orchestration — Nornir | Raw CLI слабее structured API; не маскировать vendor differences |
| Offline config validation | Batfish, vendor parser, deterministic checks | Сначала проверить поддержку точной platform и syntax |
| Packet capture | `tcpdump` для capture, `tshark`/Wireshark для анализа | Capture на production может влиять на CPU/storage |
| Metrics/logs | Prometheus/Grafana, Loki/Graylog, vendor controller | Проверять sampling, scrape interval и timestamp semantics |
| Лаборатория | Containerlab/CML/EVE-NG/vendor virtual images | Лицензии образов и отличия virtual/hardware dataplane |

## Требования к MCP

Перед подключением MCP для production network проверь:

- явный allowlist устройств и операций;
- read-only mode по умолчанию либо возможность полностью убрать write tools;
- credentials вне репозитория и prompt context;
- host-key/TLS verification;
- timeouts, connection pooling и ограничение параллелизма;
- audit log с target, operation, actor и timestamp;
- dry-run/diff/commit-confirmed или checkpoint для mutations;
- отсутствие generic shell/SSH execution без command allowlist;
- понятное поведение при partial failure на нескольких устройствах.

Не подключай агрегатор с десятками write-capable tools ради одного read-only запроса.

## Изученные проекты

- NetClaw: https://github.com/automateyournetwork/netclaw — сильные playbooks, но тесно связан с OpenClaw и большим набором MCP.
- pyATS MCP: https://github.com/automateyournetwork/pyATS_MCP — Cisco/Genie-oriented device access, проект помечен experimental.
- Junos MCP: https://github.com/Juniper/junos-mcp-server — vendor-native Junos/PyEZ, поддерживает operational и configuration tools.
- MikroTik MCP: https://github.com/jeff-nasseri/mikrotik-mcp — RouterOS-specific MCP.
- NetBox MCP: https://github.com/netboxlabs/netbox-mcp-server — небольшой read-only MCP для inventory/IPAM.
- Batfish: https://github.com/batfish/batfish — offline analysis конфигураций и pre-change validation.

## Локальная диагностика на macOS

Используй только после разрешения, если запрос затрагивает реальную сеть:

- `networkQuality` — пользовательская оценка uplink/downlink responsiveness;
- `scutil --dns` и `dscacheutil -q host -a name <name>` — resolver state;
- `route -n get <ip>` и `netstat -rn` — локальная маршрутизация;
- `ifconfig`, `arp -an`, `ndp -an` — interfaces и neighbors;
- `ping`, `traceroute`, `dig`, `nc` — направленные probes;
- `tcpdump -i <interface> ...` — минимальный BPF-filter и ограниченный capture.

Не устанавливай новый диагностический пакет, пока встроенный инструмент решает задачу.
