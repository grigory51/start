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
class Plugin:
    """Нативный CC-плагин из [[plugins]]: каталог с .claude-plugin/.

    marketplace/plugin читаются из .claude-plugin/marketplace.json+plugin.json
    (override полями в config.toml). enabled — bool (плагин атомарен). seed-сборка
    и enabledPlugins ведутся по паре (plugin, marketplace).
    """
    name: str            # имя источника (basename path) — для UI/идентификации
    path: Path           # абсолютный корень плагина (каталог с .claude-plugin/)
    source: str          # rel-path источника из config.toml ([[plugins]])
    marketplace: str     # marketplace name (из marketplace.json или override)
    plugin: str          # plugin name (из marketplace.json/plugin.json или override)
    enabled: bool
    description: str = ""
    # Команды SessionStart-хуков плагина (из hooks/hooks.json) — для предупреждения.
    session_start_hooks: list[str] = field(default_factory=list)
    # Внешние зависимости из [[plugins.requirements]]: [{name, check, hint}].
    # check — shell-команда проверки наличия (rc 0 = есть); hint — как поставить.
    requirements: list[dict] = field(default_factory=list)

    @property
    def ref(self) -> str:
        """Идентификатор плагина для enabledPlugins: '<plugin>@<marketplace>'."""
        return f"{self.plugin}@{self.marketplace}"


@dataclass
class Command:
    """Slash-команда из [[commands]]: имя файла без .md, путь, источник."""
    name: str
    path: Path
    source: str = ""     # path источника из config.toml ([[commands]])
    description: str = ""


@dataclass
class McpServer:
    """MCP-сервер из [[mcp]].

    Два режима доставки: file (symlink готового .mcp.json) или inline-спека
    (пишется в mcpServers фрагмента settings). enabled — bool.
    """
    name: str
    enabled: bool
    source: str = ""           # rel-path .mcp.json (режим file), либо ""
    server: dict | None = None  # inline-спека {command,args,env|url,headers}


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


# --- плагины (нативные CC) ----------------------------------------------------

def _bool_enabled(entry: dict) -> bool:
    """`enabled` источника как bool. Дефолт True. Терпит ["*"]/[] (для совместимости)."""
    raw = entry.get("enabled", True)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw in ("*", "true", "1")
    if isinstance(raw, list):
        # ["*"] или непустой список → включено; [] → выключено.
        return bool(raw)
    return True


def read_plugin_manifest(path: Path) -> tuple[str, str, list[str]]:
    """Прочитать .claude-plugin плагина: (marketplace_name, plugin_name, session_start_cmds).

    marketplace_name — из marketplace.json `name`. plugin_name — из marketplace.json
    `plugins[0].name` (это install-идентификатор для `<plugin>@<mp>`; plugin.json `name`
    может отличаться и НЕ используется CC при install — fallback только если в
    marketplace.json нет plugins[]). session_start_cmds — shell-команды SessionStart-хуков
    из hooks/hooks.json (для предупреждения). Пустые строки при отсутствии/ошибке.
    """
    import json

    mp_name = plugin_name = ""
    mp_file = path / ".claude-plugin" / "marketplace.json"
    if mp_file.is_file():
        try:
            mp = json.loads(mp_file.read_text())
            mp_name = mp.get("name", "")
            plugins = mp.get("plugins", [])
            if plugins and isinstance(plugins, list):
                # plugins[0].name — это install-id (<plugin>@<mp>), приоритетный источник.
                plugin_name = plugins[0].get("name", "")
        except (json.JSONDecodeError, OSError, AttributeError):
            pass

    # Fallback на plugin.json только если marketplace не дал имя плагина.
    if not plugin_name:
        pj_file = path / ".claude-plugin" / "plugin.json"
        if pj_file.is_file():
            try:
                plugin_name = json.loads(pj_file.read_text()).get("name", "")
            except (json.JSONDecodeError, OSError):
                pass

    return mp_name, plugin_name, _scan_session_start(path)


def _scan_session_start(path: Path) -> list[str]:
    """SessionStart shell-команды из hooks/hooks.json плагина ([] при отсутствии)."""
    import json

    hooks_file = path / "hooks" / "hooks.json"
    if not hooks_file.is_file():
        return []
    try:
        data = json.loads(hooks_file.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    cmds: list[str] = []
    for group in data.get("hooks", {}).get("SessionStart", []):
        for hook in group.get("hooks", []):
            if hook.get("type") == "command" and hook.get("command"):
                cmds.append(hook["command"])
    return cmds


def _parse_requirements(entry: dict, rel: str, warnings: list[str]) -> list[dict]:
    """Разобрать [[plugins.requirements]] источника: список {name, check, hint}.

    nested array-of-tables → entry["requirements"] = list[dict]. Каждая запись обязана
    иметь непустые check (shell-команда проверки) и hint (как поставить); name
    опционален (для вывода). Битые записи пропускаются с warning.
    """
    out: list[dict] = []
    raw = entry.get("requirements", [])
    if not isinstance(raw, list):
        warnings.append(f"{rel}: [[plugins.requirements]] не список — игнорирую")
        return out
    for req in raw:
        if not isinstance(req, dict):
            continue
        check = (req.get("check") or "").strip()
        hint = (req.get("hint") or "").strip()
        name = (req.get("name") or "").strip()
        if not check or not hint:
            warnings.append(f"{rel}: [[plugins.requirements]] без check/hint — пропуск")
            continue
        out.append({"name": name, "check": check, "hint": hint})
    return out


def _discover_plugins() -> tuple[list[Plugin], list[str]]:
    """Все плагины из [[plugins]]-источников config.toml + warnings.

    path → корень плагина (каталог с .claude-plugin/). marketplace/plugin читаются
    из манифеста (override полями marketplace/plugin в записи). Дубль ref → warning.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)

    seen: dict[str, Plugin] = {}
    for rel, entry in _sources(base, warnings, key="plugins"):
        root = (REPO_DIR / rel).resolve()
        if not (root / ".claude-plugin").is_dir():
            warnings.append(
                f"источник плагина не найден или без .claude-plugin/: {rel} "
                f"(для сабмодуля — git submodule update --init)")
            continue

        mp_name, plugin_name, ss_hooks = read_plugin_manifest(root)
        mp_name = entry.get("marketplace") or mp_name
        plugin_name = entry.get("plugin") or plugin_name
        if not mp_name or not plugin_name:
            warnings.append(
                f"{rel}: не удалось определить marketplace/plugin "
                f"(укажите вручную полями marketplace/plugin) — пропуск")
            continue

        ref = f"{plugin_name}@{mp_name}"
        if ref in seen:
            warnings.append(
                f"дубль плагина '{ref}': {rel} — пропуск (уже взят из {seen[ref].source})")
            continue
        seen[ref] = Plugin(
            name=root.name, path=root, source=rel,
            marketplace=mp_name, plugin=plugin_name,
            enabled=_bool_enabled(entry),
            description=_read_description(root / ".claude-plugin" / "plugin.json"),
            session_start_hooks=ss_hooks,
            requirements=_parse_requirements(entry, rel, warnings),
        )
    return list(seen.values()), warnings


def load_plugins() -> list[Plugin]:
    """Все плагины из [[plugins]]-источников. Warnings глушатся (для TUI)."""
    plugins, _ = _discover_plugins()
    return plugins


# --- команды (slash) ----------------------------------------------------------

def _discover_commands() -> tuple[list[Command], list[str]]:
    """Все команды из [[commands]]-источников. Зеркало _discover_agents (*.md)."""
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)

    seen: dict[str, Command] = {}
    for rel, entry in _sources(base, warnings, key="commands"):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            warnings.append(f"источник команд не найден: {rel}")
            continue

        exclude = set(entry.get("exclude", []))
        available = {p.stem: p for p in root.glob("*.md")}
        spec = _enabled_spec(entry)
        selected = _select_names(spec, list(available))
        for n in spec:
            if n != "*" and n not in available:
                warnings.append(f"{rel}: команда '{n}' не найдена (нет {n}.md)")

        for n in sorted(selected):
            if n in exclude:
                continue
            if n in seen:
                warnings.append(
                    f"дубль имени команды '{n}': {rel} — пропуск "
                    f"(уже взята из {seen[n].source})")
                continue
            seen[n] = Command(name=n, path=available[n], source=rel,
                              description=_read_description(available[n]))
    return list(seen.values()), warnings


def load_commands() -> list[Command]:
    """Все команды из [[commands]]-источников. Warnings глушатся (для TUI)."""
    commands, _ = _discover_commands()
    return commands


# --- MCP-серверы --------------------------------------------------------------

def load_statusline() -> dict | None:
    """`[statusline]` из config.toml: {path, dest, command} или None.

    path — *.mjs/*.sh относительно репо; dest — имя в ~/.claude/; command — строка
    для settings.json `statusLine`. None если секции нет/неполная.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    sl = base.get("statusline")
    if not isinstance(sl, dict):
        return None
    path = (sl.get("path") or "").strip()
    command = (sl.get("command") or "").strip()
    if not path or not command:
        return None
    dest = (sl.get("dest") or Path(path).name).strip()
    return {"path": path, "dest": dest, "command": command}


def load_mcp() -> tuple[list[McpServer], list[str]]:
    """MCP-серверы из [[mcp]] config.toml + warnings.

    [[mcp]] — name-keyed (не path), поэтому отдельный ридер. Каждая запись:
    name (обяз.), enabled (bool, дефолт True), source (.mcp.json для symlink) либо
    inline [mcp.server]. Дубль name → warning.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)

    seen: dict[str, McpServer] = {}
    for entry in base.get("mcp", []):
        name = (entry.get("name") or "").strip()
        if not name:
            warnings.append(f"[[mcp]] без name в {CONFIG.name} — пропуск")
            continue
        if name in seen:
            warnings.append(f"дубль MCP '{name}' в {CONFIG.name} — пропуск")
            continue
        server = entry.get("server")
        seen[name] = McpServer(
            name=name,
            enabled=_bool_enabled(entry),
            source=(entry.get("source") or "").strip(),
            server=server if isinstance(server, dict) else None,
        )
    return list(seen.values()), warnings


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


# --- запись (плагины) ---------------------------------------------------------

def plugin_source_paths() -> set[str]:
    """Все path из [[plugins]] базового config.toml (для проверки дублей)."""
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    return {e["path"] for e in _entries(base, CONFIG.name, warnings, key="plugins")}


def add_plugin_source(rel_path: str) -> bool:
    """Добавить [[plugins]] с данным path в версионный config.toml.

    Текстовый append (как add_source) — сохраняет стиль файла. Дубль path → False.
    Новый плагин включён (enabled = true).
    """
    if rel_path in plugin_source_paths():
        return False

    block = ("\n[[plugins]]\n"
             f'path = "{rel_path}"\n'
             'enabled = true\n')
    existing = CONFIG.read_text() if CONFIG.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    CONFIG.write_text(existing + block)
    return True


def set_plugin_enabled(source: str, enabled: bool) -> None:
    """Вкл/выкл плагин-источник `source` (path), правя `enabled = bool` в config.toml.

    Плагин атомарен → enabled — простой bool. Правит существующую [[plugins]]-запись
    (источники версионные); комментарии/форматирование сохраняются (tomlkit).
    """
    import tomlkit

    doc = tomlkit.parse(CONFIG.read_text()) if CONFIG.is_file() else tomlkit.document()
    plugins = doc.get("plugins")
    if plugins is None:
        plugins = tomlkit.aot()
        doc["plugins"] = plugins

    target = None
    for tbl in plugins:
        if tbl.get("path") == source:
            target = tbl
            break
    if target is None:
        target = tomlkit.table()
        target["path"] = source
        plugins.append(target)

    target["enabled"] = enabled
    CONFIG.write_text(tomlkit.dumps(doc))
