# Unity / C#

Применять к Unity-проектам. Для браузерных runtime используются отдельные правила [browser.md](browser.md).

- Версия Editor — из `ProjectSettings/ProjectVersion.txt`, пакеты — из `Packages/manifest.json` и `packages-lock.json`. Скиллы могут описывать другую версию; проверяй API по проекту. Не обновляй движок без задачи на миграцию.
- Сохраняй render pipeline (Built-in/URP/HDRP), Input System и UI Toolkit/uGUI проекта. Не добавляй DOTS, Addressables, DI или сторонний async framework без потребности.
- `Awake` — локальная инициализация; `OnEnable`/`OnDisable` — симметричные подписки; не рассчитывай на порядок `Awake` разных объектов. Физику выполняй в `FixedUpdate`; перемещение и таймеры учитывают соответствующий delta time.
- Inspector-параметры — `[SerializeField] private`; ScriptableObject подходит для разделяемых конфигураций, но runtime-изменения asset не являются системой сохранения. Сохранения используют отдельные версионированные данные.
- При перемещениях assets сохраняй `.meta`/GUID и prefab references. При переименовании сериализованных полей сохраняй данные через `FormerlySerializedAs`, когда это необходимо. Не генерируй вручную GUID существующим assets.
- Gameplay input и UI navigation должны иметь явные режимы. Проверяй мышь/клавиатуру, touch/gamepad только для целевых платформ, focus, pause, safe areas и читаемость в Game view.
- Профилируй на целевой платформе: CPU/GPU frame time, GC allocations, память, draw calls. Pooling, batching и другие оптимизации применяй по измерениям.
- Проверки: EditMode для изолированной логики, PlayMode для компонентов и сцен, целевой build для платформенных ограничений. Не выдавай просмотр кода за проверку в Editor. Запуски согласовывай с владельцем.
