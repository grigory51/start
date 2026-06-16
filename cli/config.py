"""config.py — чтение config.toml, обнаружение скилов, вкл/выкл через `enabled`.

Состояние вкл/выкл скилов хранится в поле `enabled` источника ([[skills]]):
  - enabled = ["*"]      — все скилы источника (по умолчанию);
  - enabled = ["a", "c"] — только перечисленные;
  - enabled = []         — ни одного (источник выключен целиком).
Отдельного списка `disabled` нет — выключение скила = удаление его имени из
`enabled` (с разворачиванием "*" в явный список).

Чтение — на tomllib. Запись (toggle) — на tomlkit, чтобы сохранить комментарии и
форматирование. TUI правит `enabled` прямо в версионном config.toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG = REPO_DIR / "config.toml"


# --- модель -------------------------------------------------------------------

@dataclass
class Skill:
    """Найденный скил: имя папки, абсолютный путь, источник, статус."""
    name: str
    path: Path
    source: str          # path источника из config.toml
    enabled: bool        # имя есть в `enabled`-списке источника
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


def _sources(doc: dict, warnings: list[str],
             key: str = "skills") -> list[tuple[str, dict]]:
    """[[key]]-источники из config.toml как [(path, entry)] в порядке файла.

    key — какую секцию читать ([[skills]] или [[agents]]). Дубль path: первый
    выигрывает, остальные в warnings.
    """
    out: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for entry in _entries(doc, CONFIG.name, warnings, key):
        p = entry["path"]
        if p in seen:
            warnings.append(f"дубль источника {p} в {CONFIG.name} — пропуск")
            continue
        seen.add(p)
        out.append((p, entry))
    return out


def _enabled_spec(entry: dict) -> list[str]:
    """`enabled`-список источника. Дефолт ["*"] (все). Терпит старый bool/строку.

    Нормализует к list[str]: ["*"] — все, [] — ни одного, иначе явные имена.
    Обратная совместимость: enabled = false → [], enabled = true/отсутствует → ["*"].
    """
    raw = entry.get("enabled", ["*"])
    if raw is True:
        return ["*"]
    if raw is False:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return ["*"]


def _select_names(spec: list[str], available: list[str]) -> set[str]:
    """Какие скилы источника включены: разворачивает "*" в все доступные имена."""
    if "*" in spec:
        return set(available)
    return set(spec)


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
    """Разобрать config.toml и обнаружить все скилы.

    Конфликты имён скилов: берётся первое вхождение, дубль попадает в warnings.
    В .skills попадают ВСЕ найденные скилы источника (для UI); enabled=False у
    тех, чьё имя не входит в `enabled`-список источника. exclude убирает скил
    совсем (даже из UI).
    """
    res = ConfigResult()
    base = _load_doc(CONFIG, res.warnings)

    if not base and not CONFIG.is_file():
        res.warnings.append(f"{CONFIG.name} не найден — скилы не линкуются")
        return res

    seen: dict[str, Skill] = {}
    for rel, entry in _sources(base, res.warnings):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            res.warnings.append(
                f"источник не найден: {rel} (нет папки; для сабмодуля — "
                f"git submodule update --init)")
            continue

        exclude = set(entry.get("exclude", []))
        symlinks = _parse_symlinks(entry, rel, res.warnings)
        available = {p.name: p for p in root.iterdir() if is_skill(p)}

        spec = _enabled_spec(entry)
        selected = _select_names(spec, list(available))
        # Имена из `enabled`, которых нет в источнике — предупреждаем.
        for n in spec:
            if n != "*" and n not in available:
                res.warnings.append(f"{rel}: скил '{n}' не найден (нет папки с SKILL.md)")

        for n in sorted(available):
            if n in exclude:
                continue
            if n in seen:
                res.warnings.append(
                    f"дубль имени скила '{n}': {rel} — пропуск "
                    f"(уже взят из {seen[n].source})")
                continue
            seen[n] = Skill(
                name=n, path=available[n], source=rel,
                enabled=n in selected,
                description=_read_description(available[n] / "SKILL.md"),
                symlinks=symlinks,
            )

    # Порядок: источники в порядке config.toml, внутри — имена по алфавиту
    # (seen заполнялся именно так). Для группировки по источнику в UI.
    res.skills = list(seen.values())
    return res


def _discover_agents() -> tuple[list[Agent], list[str]]:
    """Все агенты из [[agents]]-источников config.toml и warnings.

    Зеркало skill-цикла в load(): источники в порядке config.toml, внутри —
    имена по алфавиту. Конфликт имён: первое вхождение, дубль → warnings
    (как у скилов). Линкуются только включённые `enabled`-списком.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)

    seen: dict[str, Agent] = {}
    for rel, entry in _sources(base, warnings, key="agents"):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            warnings.append(
                f"источник агентов не найден: {rel} (нет папки; для сабмодуля — "
                f"git submodule update --init)")
            continue

        exclude = set(entry.get("exclude", []))
        available = {p.stem: p for p in root.glob("*.md")}

        spec = _enabled_spec(entry)
        selected = _select_names(spec, list(available))
        for n in spec:
            if n != "*" and n not in available:
                warnings.append(f"{rel}: агент '{n}' не найден (нет {n}.md)")

        for n in sorted(selected):
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


# --- запись (toggle enabled) --------------------------------------------------

def _write_enabled(source: str, spec: list[str]) -> None:
    """Записать `enabled = spec` в [[skills]] с path=source в версионный config.toml.

    Правит существующую запись источника (она всегда есть — источники версионные);
    комментарии и форматирование сохраняются (tomlkit). Если записи нет — создаёт.
    """
    import tomlkit

    doc = tomlkit.parse(CONFIG.read_text()) if CONFIG.is_file() else tomlkit.document()

    skills = doc.get("skills")
    if skills is None:
        skills = tomlkit.aot()
        doc["skills"] = skills

    target = None
    for tbl in skills:
        if tbl.get("path") == source:
            target = tbl
            break
    if target is None:
        target = tomlkit.table()
        target["path"] = source
        skills.append(target)

    arr = tomlkit.array()
    arr.multiline(False)
    arr.extend(spec)
    target["enabled"] = arr

    CONFIG.write_text(tomlkit.dumps(doc))


def set_skill_enabled(source: str, name: str, enabled: bool) -> None:
    """Вкл/выкл скил `name` источника `source`, правя `enabled` в config.toml.

    Берёт текущий разворот `enabled` источника, меняет членство `name`, пишет:
      - если включены ВСЕ доступные скилы источника → enabled = ["*"];
      - иначе → enabled = [отсортированный список включённых].
    """
    warnings: list[str] = []
    entry: dict = {}
    for rel, e in _sources(_load_doc(CONFIG, warnings), warnings):
        if rel == source:
            entry = e
            break
    root = (REPO_DIR / source).resolve()
    available = sorted(p.name for p in root.iterdir() if is_skill(p)) if root.is_dir() else []

    selected = _select_names(_enabled_spec(entry), available)
    if enabled:
        selected.add(name)
    else:
        selected.discard(name)

    new_spec = ["*"] if selected >= set(available) and available else sorted(selected)
    _write_enabled(source, new_spec)


def set_source_enabled(source: str, enabled: bool) -> None:
    """Вкл/выкл ВСЕ скилы источника `source` разом, правя config.toml.

    enabled=True  → enabled = ["*"] (все);
    enabled=False → enabled = []   (ни одного).
    """
    _write_enabled(source, ["*"] if enabled else [])


def source_paths() -> set[str]:
    """Все path из [[skills]] базового config.toml (для проверки дублей)."""
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    return {e["path"] for e in _entries(base, CONFIG.name, warnings, key="skills")}


def add_source(rel_path: str, *, exclude: list[str] | None = None) -> bool:
    """Добавить [[skills]] с данным path в версионный config.toml (tomlkit).

    Добавление сабмодуля — версионное изменение (как запись в .gitmodules), пишем
    в config.toml. Комментарии/форматирование
    сохраняются. Дубль path игнорируется. Регистрирует источник скилов; источники
    агентов ([[agents]]) добавляются в config.toml вручную. Новый источник
    включает все свои скилы (enabled = ["*"]).

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
             'enabled = ["*"]\n')
    if exclude:
        block += f"exclude = {tomlkit.item(exclude).as_string()}\n"

    existing = CONFIG.read_text() if CONFIG.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    CONFIG.write_text(existing + block)
    return True
