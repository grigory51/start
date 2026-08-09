# Сети и NOC

## Диагностика

- Начинай с пользовательского симптома и end-to-end path, а не с любимой команды на ближайшем маршрутизаторе.
- Разделяй management, control и data plane. Состояние adjacency/RIB не доказывает корректный FIB и прохождение реального трафика.
- Проверяй forward и reverse path, VRF/VLAN, NAT/policy boundaries, ECMP и asymmetric routing.
- Любой вывод привязывай к target, источнику данных и timestamp. Counter требует baseline; лог требует корректного времени.
- Root cause должен объяснять симптом и подтверждаться независимым наблюдением. Знакомая неисправность без проверки остаётся гипотезой.

## Вендоры и automation

- Сначала точные vendor, product family и NOS version; затем команды. Cisco IOS/IOS XE/NX-OS, Juniper Junos, Huawei VRP и MikroTik RouterOS имеют разные configuration и rollback semantics.
- Предпочитай structured vendor API и модели: pyATS/Genie, NETCONF/YANG, Junos PyEZ, RouterOS REST/API. Raw SSH CLI — fallback, а не единый abstraction layer.
- Source of Truth описывает intended state; telemetry и live configuration описывают actual state. Расхождение фиксируй явно.
- Для pre-change анализа используй offline configs и deterministic validation. Не проверяй новую policy впервые на production device.

## Изменения

- Без явного разрешения не выполняй на удалённых устройствах даже read-only команды. Предварительно покажи targets и точные операции.
- Любое изменение требует diff, blast radius, pre-checks, rollback, stop conditions и post-checks.
- Меняй одну причинную переменную и проверяй исходный пользовательский probe. Commit success не является проверкой сервиса.
- Сохраняй OOB/management access и не меняй одновременно основной и резервный путь управления.
- Не отключай TLS/host-key verification и не храни secrets в inventory, конфиге агента или репозитории.

## Incident handoff

- Разделяй impact, timeline, evidence, hypotheses, root cause, mitigation и corrective actions.
- Указывай непройденные проверки и неизвестные участки topology. Не маскируй отсутствие данных уверенной формулировкой.
