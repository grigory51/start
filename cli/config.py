"""config.py — чтение config.toml, обнаружение скилов, список выключенных.

Чтение (merge базы и local-overlay) — на tomllib. Запись (toggle `disabled`) —
на tomlkit, чтобы сохранить комментарии и форматирование версионного файла.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG = REPO_DIR / "config.toml"
CONFIG_LOCAL = REPO_DIR / "config.local.toml"  # gitignored, переопределяет CONFIG


# --- модель -------------------------------------------------------------------

@dataclass
class Skill:
    """Найденный скил: имя папки, абсолютный путь, источник, статус."""
    name: str
    path: Path
    source: str          # path источника из config.toml
    enabled: bool        # не в disabled и источник включён
    description: str = ""
    # Доп. symlink'и из [[skills.symlinks]] источника: [{source, destination}].
    # source — путь относительно корня репо; destination — имя/путь внутри
    # папки-зеркала скила. Применяются к каждому скилу источника.
    symlinks: list[dict] = field(default_factory=list)


@dataclass
class Agent:
    """Найденный агент: имя файла без .md, путь, источник, описание из frontmatter."""
    name: str
    path: Path
    source: str = ""     # path источника из config.toml ([[agents]])
    description: str = ""


@dataclass
class ConfigResult:
    """Результат разбора конфига для дальнейшей линковки и UI."""
    skills: list[Skill] = field(default_factory=list)
    disabled: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    @property
    def enabled_skills(self) -> list[Skill]:
        return [s for s in self.skills if s.enabled]


def is_skill(p: Path) -> bool:
    return p.is_dir() and (p / "SKILL.md").is_file()


def _frontmatter_field(text: str, key: str) -> str:
    """Достать одно поле верхнего уровня из YAML-frontmatter (--- ... ---).

    Без зависимости от yaml: ищем строку `key: value` в первом блоке между
    разделителями ---. Поддерживает простые однострочные значения в кавычках.
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    block = text[3:end]
    prefix = f"{key}:"
    for line in block.splitlines():
        s = line.strip()
        if s.startswith(prefix):
            val = s[len(prefix):].strip()
            if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                val = val[1:-1]
            return val
    return ""


def _read_description(path: Path, key: str = "description") -> str:
    """Прочитать поле description из frontmatter файла. '' при любой ошибке."""
    try:
        # хватает первых ~4 КБ — frontmatter всегда в начале
        head = path.read_text(errors="replace")[:4096]
    except OSError:
        return ""
    return _frontmatter_field(head, key)


# --- чтение -------------------------------------------------------------------

def _load_doc(path: Path, warnings: list[str]) -> dict:
    """Прочитать TOML-файл. {} если файла нет/ошибка разбора."""
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        warnings.append(f"ошибка разбора {path.name}: {e}")
        return {}


def _entries(doc: dict, fname: str, warnings: list[str], key: str = "skills") -> list[dict]:
    """[[key]] из документа, отфильтрованные по наличию path (key: skills/agents)."""
    out: list[dict] = []
    for entry in doc.get(key, []):
        if not entry.get("path"):
            warnings.append(f"[[{key}]] без path в {fname} — пропуск")
            continue
        out.append(entry)
    return out


def _merged_sources(base: dict, local: dict, warnings: list[str],
                    key: str = "skills") -> list[tuple[str, dict]]:
    """База ⊕ overlay по `path`. local с тем же path переопределяет целиком.

    key — какую секцию мёрджить ([[skills]] или [[agents]]). enabled = false
    выключает источник. Возвращает [(path, entry)] в порядке: база (позиция
    сохраняется), затем новые local-источники.
    """
    base_e = _entries(base, CONFIG.name, warnings, key)
    local_e = _entries(local, CONFIG_LOCAL.name, warnings, key)

    merged: dict[str, dict] = {}
    order: list[str] = []
    for entry in base_e:
        p = entry["path"]
        if p not in merged:
            order.append(p)
        merged[p] = entry
    for entry in local_e:
        p = entry["path"]
        if p not in merged:
            order.append(p)
        merged[p] = entry  # local выигрывает целиком

    result: list[tuple[str, dict]] = []
    for p in order:
        entry = merged[p]
        if entry.get("enabled", True) is False:
            warnings.append(f"источник {p} выключен (enabled = false)")
            continue
        result.append((p, entry))
    return result


def _parse_symlinks(entry: dict, rel: str, warnings: list[str]) -> list[dict]:
    """Разобрать [[skills.symlinks]] источника: список {source, destination}.

    nested array-of-tables → entry["symlinks"] = list[dict]. Каждая запись обязана
    иметь непустые source/destination (строки). source — путь относительно корня
    репо; destination — имя/путь внутри папки-зеркала скила (нормализуется strip).
    Битые записи пропускаются с warning.
    """
    out: list[dict] = []
    raw = entry.get("symlinks", [])
    if not isinstance(raw, list):
        warnings.append(f"{rel}: [[skills.symlinks]] не список — игнорирую")
        return out
    for sl in raw:
        src = (sl.get("source") or "").strip() if isinstance(sl, dict) else ""
        dst = (sl.get("destination") or "").strip().strip("/") if isinstance(sl, dict) else ""
        if not src or not dst:
            warnings.append(f"{rel}: [[skills.symlinks]] без source/destination — пропуск")
            continue
        out.append({"source": src, "destination": dst})
    return out


def load() -> ConfigResult:
    """Разобрать config.toml (+ local overlay) и обнаружить все скилы.

    Конфликты имён скилов: берётся первое вхождение, дубль попадает в warnings.
    disabled-скилы остаются в .skills, но с enabled=False (для отображения в UI).
    """
    res = ConfigResult()
    base = _load_doc(CONFIG, res.warnings)
    local = _load_doc(CONFIG_LOCAL, res.warnings)

    if not base and not CONFIG.is_file():
        res.warnings.append(f"{CONFIG.name} не найден — скилы не линкуются")
        return res
    if local:
        res.warnings.append(f"применён overlay {CONFIG_LOCAL.name}")

    res.disabled = set(base.get("disabled", [])) | set(local.get("disabled", []))

    seen: dict[str, Skill] = {}
    for rel, entry in _merged_sources(base, local, res.warnings):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            res.warnings.append(
                f"источник не найден: {rel} (нет папки; для сабмодуля — "
                f"git submodule update --init)")
            continue

        include = entry.get("include", "*")
        exclude = set(entry.get("exclude", []))
        symlinks = _parse_symlinks(entry, rel, res.warnings)
        available = {p.name: p for p in root.iterdir() if is_skill(p)}

        names = sorted(available) if include == "*" else list(include)
        for n in names:
            if n not in available:
                res.warnings.append(f"{rel}: скил '{n}' не найден (нет папки с SKILL.md)")
                continue
            if n in exclude:
                continue
            if n in seen:
                res.warnings.append(
                    f"дубль имени скила '{n}': {rel} — пропуск "
                    f"(уже взят из {seen[n].source})")
                continue
            seen[n] = Skill(
                name=n, path=available[n], source=rel,
                enabled=n not in res.disabled,
                description=_read_description(available[n] / "SKILL.md"),
                symlinks=symlinks,
            )

    # Порядок: источники в порядке config.toml, внутри — имена по алфавиту
    # (seen заполнялся именно так). Для группировки по источнику в UI.
    res.skills = list(seen.values())
    return res


def _discover_agents() -> tuple[list[Agent], list[str]]:
    """Все агенты из [[agents]]-источников config.toml (+ overlay) и warnings.

    Зеркало skill-цикла в load(): источники в порядке config.toml, внутри —
    имена по алфавиту. Конфликт имён: первое вхождение, дубль → warnings
    (как у скилов). Поштучного disable у агентов нет — все найденные линкуются.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    local = _load_doc(CONFIG_LOCAL, warnings)

    seen: dict[str, Agent] = {}
    for rel, entry in _merged_sources(base, local, warnings, key="agents"):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            warnings.append(
                f"источник агентов не найден: {rel} (нет папки; для сабмодуля — "
                f"git submodule update --init)")
            continue

        include = entry.get("include", "*")
        exclude = set(entry.get("exclude", []))
        available = {p.stem: p for p in root.glob("*.md")}

        names = sorted(available) if include == "*" else list(include)
        for n in names:
            if n not in available:
                warnings.append(f"{rel}: агент '{n}' не найден (нет {n}.md)")
                continue
            if n in exclude:
                continue
            if n in seen:
                warnings.append(
                    f"дубль имени агента '{n}': {rel} — пропуск "
                    f"(уже взят из {seen[n].source})")
                continue
            seen[n] = Agent(name=n, path=available[n], source=rel,
                            description=_read_description(available[n]))
    return list(seen.values()), warnings


def load_agents() -> list[Agent]:
    """Все агенты из [[agents]]-источников. Warnings глушатся (для TUI)."""
    agents, _ = _discover_agents()
    return agents


# --- запись (toggle disabled) -------------------------------------------------

_LOCAL_HEADER = (
    "# config.local.toml — машино-/пользователь-специфичный overlay над config.toml.\n"
    "# В .gitignore. `disabled` здесь объединяется с базовым; редактируется TUI\n"
    "# (`uv run claude-agents manage`). Версионный config.toml TUI не трогает.\n\n"
)


def set_disabled(name: str, disabled: bool) -> None:
    """Добавить/убрать скил из `disabled` в config.local.toml (не в config.toml).

    Версионный config.toml остаётся нетронутым — toggle пишет только в local
    overlay (gitignored), его `disabled` объединяется с базовым при load().
    Файл создаётся при первом toggle, существующие комментарии/[[skills]]
    сохраняются (tomlkit).

    disabled=True  → добавить имя в список (если ещё нет).
    disabled=False → убрать имя из списка.
    """
    import tomlkit

    if CONFIG_LOCAL.is_file():
        doc = tomlkit.parse(CONFIG_LOCAL.read_text())
    else:
        doc = tomlkit.parse(_LOCAL_HEADER)

    arr = doc.get("disabled")
    if arr is None:
        arr = tomlkit.array()
        arr.multiline(False)
        doc["disabled"] = arr

    current = list(arr)
    if disabled and name not in current:
        arr.append(name)
    elif not disabled and name in current:
        # tomlkit array: пересобираем без удаляемого имени.
        for i, v in enumerate(list(arr)):
            if v == name:
                arr.pop(i)
                break

    CONFIG_LOCAL.write_text(tomlkit.dumps(doc))


def source_paths() -> set[str]:
    """Все path из [[skills]] базового config.toml (для проверки дублей)."""
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    return {e["path"] for e in _entries(base, CONFIG.name, warnings, key="skills")}


def add_source(rel_path: str, *, include="*", exclude: list[str] | None = None) -> bool:
    """Добавить [[skills]] с данным path в версионный config.toml (tomlkit).

    Пишет в базовый config.toml, а не в overlay: добавление сабмодуля —
    версионное изменение (как запись в .gitmodules). Комментарии/форматирование
    сохраняются. Дубль path игнорируется. Регистрирует источник скилов; источники
    агентов ([[agents]]) добавляются в config.toml вручную.

    Возвращает True, если источник добавлен; False — если path уже есть.
    """
    import tomlkit

    if rel_path in source_paths():
        return False

    # Рендерим новый [[skills]] как текст и дописываем в конец файла. tomlkit
    # при append в AoT кладёт отбивку внутрь header'а ([[skills]] + пустая строка),
    # что ломает выравнивание; текстовый append даёт ровно тот же стиль, что в base.
    block = ("\n[[skills]]\n"
             f'path = "{rel_path}"\n'
             f"include = {tomlkit.item(include).as_string()}\n")
    if exclude:
        block += f"exclude = {tomlkit.item(exclude).as_string()}\n"

    existing = CONFIG.read_text() if CONFIG.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    CONFIG.write_text(existing + block)
    return True
