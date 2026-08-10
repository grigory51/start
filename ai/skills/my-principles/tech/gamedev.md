# Browser gamedev (2D и 3D)

> Стек-специфика браузерных игр. Общие правила TypeScript и UI — в [frontend.md](frontend.md).

## Выбор runtime

- Сохраняй engine существующего проекта. Миграция допустима только при доказанном ограничении.
- Новая gameplay-heavy 2D-игра: Phaser. Renderer-heavy 2D без полноценного engine: PixiJS.
- Новая 3D-игра с прямым loop/scene control: Three.js. React Three Fiber — только когда React уже владеет продуктом и declarative scene действительно выгодна.
- Babylon.js или PlayCanvas выбирай, когда нужны их готовые editor, animation, physics, WebXR или командный content workflow. Не строй свой engine поверх Three.js, если готовый engine уже покрывает требования.
- WebGPU включай через runtime с WebGL2 fallback. Raw WebGPU и собственный renderer требуют отдельной задачи.

## Архитектура

- Simulation state живёт вне scene graph, Phaser scenes и React components. Renderer отображает состояние и возвращает input actions.
- Physical input (`keyboard`, `pointer`, `touch`, `gamepad`) преобразуется в семантические actions в одном месте.
- Gameplay update order явный. Physics и детерминированная simulation используют fixed timestep; render интерполирует состояние.
- Save хранит версионированные сериализуемые данные, а не mesh, sprite, body или framework objects.
- Asset manifest — публичный контракт. Gameplay не знает случайных путей к файлам.

## Инструменты

- Browser playtest: Playwright, screenshots, console/page/network errors и игровой test bridge. Для canvas/WebGL одних DOM assertions недостаточно.
- GPU/render diagnosis: browser Performance tooling, SpectorJS для WebGL, engine diagnostics (`renderer.info` и аналоги).
- 3D pipeline: Blender → GLB/glTF → glTF Transform; Meshopt/Draco для geometry и KTX2/Basis для textures только после измерения decode/load trade-off.
- Physics: встроенная простая physics engine проекта; Rapier 2D/3D для серьёзной rigid-body/collision simulation. Не добавляй WASM physics ради AABB overlap.
- Live tuning допустим через существующий debug UI или `lil-gui`, но debug tooling не попадает в production bundle.

## Gameplay и feel

- Сначала playable vertical slice: input → action → feedback → objective → fail/retry. Затем content, meta и polish.
- Camera, acceleration, jump/turn response, hitstop, shake, VFX и audio привязаны к gameplay events и имеют тюнингуемые пределы.
- Seeded RNG и test-only state hooks обязательны там, где без них playtest flaky или состояние трудно воспроизвести.
- Pause/focus/visibility state останавливает нужные подсистемы согласованно; resume не создаёт delta spike.

## Производительность и browser lifecycle

- Бюджеты задаются для конкретных target devices: frame time, long tasks, draw calls, geometry, texture/GPU memory, bundle и load time. Универсальных чисел нет.
- Ограничивай DPR на дорогих сценах; переиспользуй geometry/material/texture; применяй pooling, instancing, culling и LOD по измерениям.
- Явно освобождай GPU, physics, audio и event resources при смене сцен и teardown.
- Обрабатывай resize, orientation, context loss, autoplay/audio unlock, visibility и input focus.

## Anti-patterns

- Gameplay rules внутри render callbacks или React state updates.
- Frame-dependent movement и physics через переменный delta без ограничений.
- ECS, event bus или собственный engine до появления реальной потребности.
- Canvas-only text-heavy UI, generic dashboard HUD и overlay, закрывающий playfield.
- Placeholder assets и постэффекты вместо art direction и читаемого gameplay.
- Оптимизация без одинакового baseline-сценария до и после.
