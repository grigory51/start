# Vendor reference

Всегда сверяй команды с точной platform и версией NOS. Одинаковый бренд не означает одинаковые syntax, feature set или commit semantics.

## Быстрая матрица диагностики

| Задача | Cisco IOS/IOS XE/NX-OS | Juniper Junos | Huawei VRP | MikroTik RouterOS v7 |
|---|---|---|---|---|
| Интерфейсы | `show ip interface brief`, `show interfaces` | `show interfaces terse`, `show interfaces extensive` | `display ip interface brief`, `display interface` | `/interface print detail`, `/interface ethernet monitor <id> once` |
| Ошибки/дропы | `show interfaces counters errors` или interface detail | `show interfaces extensive` | `display interface` | `/interface print stats-detail` |
| Соседи | `show cdp neighbors detail`, `show lldp neighbors detail` | `show lldp neighbors detail` | `display lldp neighbor brief` | `/ip neighbor print detail` |
| MAC/ARP/ND | `show mac address-table`, `show ip arp`, `show ipv6 neighbors` | `show ethernet-switching table`, `show arp`, `show ipv6 neighbors` | `display mac-address`, `display arp`, `display ipv6 neighbors` | `/interface bridge host print`, `/ip arp print`, `/ipv6 neighbor print` |
| Маршруты | `show ip route`, `show forwarding`/platform FIB command | `show route`, `show route forwarding-table` | `display ip routing-table`, platform FIB command | `/ip route print detail`, `/routing/route print detail` |
| BGP | `show bgp ... summary` или `show ip bgp summary` | `show bgp summary`, `show bgp neighbor` | `display bgp peer`, `display bgp routing-table` | `/routing bgp session print detail`, `/routing route print where bgp` |
| OSPF | `show ip ospf neighbor`, `show ip ospf interface` | `show ospf neighbor`, `show ospf interface` | `display ospf peer`, `display ospf interface` | `/routing ospf neighbor print detail`, `/routing ospf interface-template print detail` |
| STP/LAG | `show spanning-tree`, `show etherchannel summary`/`show port-channel summary` | `show spanning-tree bridge`, `show lacp interfaces` | `display stp brief`, `display eth-trunk` | `/interface bridge port print detail`, `/interface bonding monitor <id> once` |
| Логи/ресурсы | `show logging`, `show processes`, platform health | `show log messages`, `show chassis routing-engine` | `display logbuffer`, `display cpu-usage`, `display memory-usage` | `/log print`, `/system resource print`, `/system health print` |
| Конфигурация | `show running-config` / NX-OS checkpoint | `show configuration`, `show configuration | compare` | `display current-configuration` | `/export terse hide-sensitive` |

Команды в таблице являются стартовыми точками. Некоторые команды отсутствуют или отличаются на отдельных product families.

## Cisco

- Различай IOS, IOS XE, IOS XR и NX-OS; не переноси syntax между ними автоматически.
- Используй pyATS/Genie для structured parsing, feature snapshots и before/after diff, когда platform поддерживается.
- Перед изменением проверь archive/checkpoint/rollback возможности конкретной platform. `copy running-config startup-config` не является rollback.
- Для NX-OS учитывай VDC, VRF context, vPC consistency и separation control/data plane.
- Для IOS XR используй candidate configuration и commit model, а не IOS-подобный immediate workflow.

Официальная отправная точка: https://developer.cisco.com/docs/pyats/

## Juniper

- Junos имеет candidate configuration, `compare`, `commit check`, `commit confirmed` и rollback. Используй их вместо последовательности немедленных CLI-изменений.
- Учитывай routing-instances, logical systems, interface units и policy term ordering.
- Для automation предпочитай NETCONF/Junos XML API и PyEZ; CLI parsing оставляй fallback.
- Перед commit проверяй diff, commit check и подтверждённый rollback timer.

Официальная отправная точка: https://www.juniper.net/documentation/product/us/en/junos-pyez

## Huawei

- Сначала установи family и software train: CloudEngine, NetEngine, S/AR и разные поколения VRP заметно различаются.
- Используй `display`, а не Cisco `show`. Не предполагай единый commit model: на части систем команды применяются сразу, на других используется two-stage configuration/`commit`.
- Для automation предпочитай NETCONF/YANG через SSH и проверяй advertised capabilities. Candidate, writable-running и rollback доступны не одинаково на всех платформах.
- Учитывай VPN-instance, Eth-Trunk, stack/iStack/CSS и vendor-specific route-policy semantics.

Официальная отправная точка: https://info.support.huawei.com/enterprise/en/doc/EDOC1100366585/877aa431/netconf-configuration

## MikroTik

- Всегда различай RouterOS v6 и v7: routing/BGP/OSPF hierarchy и вывод значительно изменились.
- Изменения применяются немедленно. Для интерактивной рискованной работы используй Safe Mode и заранее подготовленный способ восстановления management access.
- Для чтения предпочитай RouterOS REST/API с ограниченным пользователем и TLS; SSH CLI используй для операций, которых нет в API.
- Учитывай порядок firewall/NAT/mangle rules, connection tracking, FastTrack, routing tables/rules и bridge VLAN filtering.
- Экспортируй только с `hide-sensitive`; полный export всё равно проверяй на секреты перед сохранением.

Официальная отправная точка: https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST%2BAPI

## Межвендорные ловушки

- `administratively up`, protocol state и line protocol называются по-разному.
- RIB, FIB и advertised/received routes нельзя смешивать в один показатель.
- Route preference/distance имеет разные шкалы и defaults.
- Policy evaluation, implicit deny, prefix bounds и community syntax различаются.
- LAG hash, STP flavor, MLAG/vPC stacking и failure semantics зависят от platform.
- Default MTU может означать L3 MTU, frame size или payload size.
- Commit success не доказывает convergence или end-to-end forwarding.
