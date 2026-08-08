# iOS (Swift, SwiftUI, UIKit)

> Стек-специфика iOS/iPadOS. Общие принципы — в [SKILL.md](../SKILL.md).

## Контекст проекта

- Сначала определи deployment target, Swift language mode, Xcode version, workspace/project, scheme, SPM/CocoaPods/Tuist и существующую архитектуру.
- Deployment target — граница решения. Новые API используй только с корректным `@available` и рабочим fallback, если проект поддерживает старые версии.
- Не мигрируй архитектуру, persistence или build system попутно. MVVM, TCA, coordinators и DI применяй только когда они уже являются языком проекта или явно нужны задаче.

## Swift

- Следуй Swift API Design Guidelines: ясные call sites, value semantics по умолчанию, минимальная область видимости.
- Swift 6 concurrency должна быть чистой: actor isolation и `Sendable` выражают реальный ownership, а не заглушаются `nonisolated(unsafe)` или `@unchecked Sendable`.
- Предпочитай structured concurrency. У каждой `Task` должны быть понятны owner, cancellation и lifetime; `Task.detached` — редкое исключение.
- UI-bound код и состояние — `@MainActor`; CPU/IO работа не выполняется на main thread.
- Не использовать force unwrap и `try!`, кроме доказуемых инвариантов и тестовых данных. Ошибки не терять и не сводить к `print`.
- Не тащить Combine в новый код, если async sequences и observation решают задачу проще; существующий Combine не переписывать без причины.

## SwiftUI и UIKit

- Ownership состояния должен быть явным: локальное состояние принадлежит view, внешнее передаётся через binding/model/environment по паттерну проекта.
- View описывает UI, а не хранит networking, persistence и длинные imperative workflows в `body` или event closures.
- Не дробить view на файлы и типы ради размера. Выносить компонент, когда у него есть самостоятельная ответственность, reuse или изоляция обновлений.
- Предпочитай системные navigation, presentation, controls, materials и gestures. Не имитируй веб-интерфейс на iOS.
- UIKit bridge оправдан отсутствующим SwiftUI API, требованием проекта или измеримой проблемой; bridge должен иметь узкую границу.
- Обязательны Dynamic Type, VoiceOver, semantic colors, Dark Mode, Reduce Motion, safe areas, keyboard handling и touch targets не меньше 44×44 pt.
- iPad — не растянутый iPhone: учитывать size classes, split view, multitasking, keyboard и pointer там, где это относится к продукту.

## Данные, сеть и безопасность

- Используй существующий persistence layer. Для SwiftData/Core Data заранее учитывать model evolution, migration, relationships, uniqueness и background work.
- Network layer должен поддерживать cancellation, typed decoding, явные HTTP/error semantics и test seams. Не дублировать transport models в UI.
- Секреты и credentials — Keychain, не `UserDefaults`, plist, исходники или логи.
- Запрашивай только необходимые permissions, с корректными usage descriptions и обработкой denied/restricted states.
- Не логируй персональные данные, токены и содержимое Keychain. Учитывай privacy manifest и Required Reason APIs.

## Тесты и проверка

- Следуй тестовому стеку проекта: Swift Testing для unit/integration, если он принят; XCTest/XCUITest там, где они нужны или уже используются.
- Тестировать observable behavior, state transitions, cancellation, ошибки и migrations. Избегать implementation-detail tests и arbitrary sleeps.
- Build/test/run выполнять только после разрешения владельца. Для Codex предпочитать XcodeBuildMCP: project discovery → session defaults → одна целевая операция → точный отчёт.
- Simulator не доказывает поведение камеры, Bluetooth, push, background execution, thermal/memory pressure и signing на реальном устройстве — эти границы отмечать явно.
- Performance чинить по evidence из Instruments, MetricKit, ETTrace или memory graph, а не по предположению.

## Нельзя без явного согласования

- Менять signing team, bundle identifier, entitlements, capabilities и App Store metadata.
- Поднимать deployment target, Swift language mode или major-версию зависимости.
- Массово переписывать `.pbxproj`, генерировать новый project/workspace или менять build system.
- Добавлять стороннюю библиотеку поверх достаточного Apple framework.
