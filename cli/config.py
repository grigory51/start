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

import sys
import json
import re
import tomllib
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import tomlkit

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG = REPO_DIR / "config.toml"
# Локальный overlay (gitignore): машино-специфичные переопределения `enabled`.
# Структура: [local.<section>] <key> = <enabled>. См. _load_local / _effective_*.
CONFIG_LOCAL = REPO_DIR / "config.local.toml"


class FileValueError(ValueError):
    """Ошибка ссылки на файл; сообщение не содержит значение секрета."""


def _file_values(value: Any, root: Path, *, read: bool = False) -> Any:
    """Нормализовать пути или прочитать строковые значения ссылок $file."""
    if isinstance(value, list):
        return [_file_values(item, root, read=read) for item in value]
    if not isinstance(value, dict):
        return value
    if "$file" not in value:
        return {key: _file_values(item, root, read=read) for key, item in value.items()}
    if set(value) != {"$file"} or not isinstance(value["$file"], str) or not value["$file"].strip():
        raise FileValueError("Ссылка $file требует единственный непустой строковый путь")
    path = root / Path(value["$file"]).expanduser()
    if not read:
        return {"$file": str(path.absolute())}
    try:
        content = path.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError):
        raise FileValueError(f"Не удалось прочитать файл значения: {path}") from None
    if not content.strip():
        raise FileValueError(f"Файл значения пуст: {path}")
    return content


@dataclass
class ConfigContext:
    """Каталог с разрешёнными путями и источником проектных изменений."""
    document: dict
    local: dict
    root: Path
    project_keys: dict[str, set[str]] = field(default_factory=dict)
    project_document: dict = field(default_factory=dict)


def _merge_tables(base: dict, overlay: dict) -> dict:
    result = deepcopy(base)
    for key, value in overlay.items():
        if (isinstance(value, dict) and isinstance(result.get(key), dict)
                and "$file" not in value and "$file" not in result[key]):
            result[key] = _merge_tables(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _strict_document(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Не удалось прочитать {path}: {error}") from error


def _catalog_entries(ai: dict, section: str) -> list[dict]:
    entries = ai.get(section, [])
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError(f"ai.{section} должен быть массивом таблиц")
    key = "name" if section == "mcp" else "path"
    seen: set[str] = set()
    for entry in entries:
        name = entry.get(key)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"ai.{section} требует {key}")
        if name in seen:
            raise ValueError(f"Дубль ai.{section}: {name}")
        seen.add(name)
    return entries


def _local_sections(document: dict) -> dict:
    local = document.get("local", {})
    if not isinstance(local, dict) or set(local) - {"ai"}:
        raise ValueError("Поддерживается только local.ai")
    ai = local.get("ai", {})
    if not isinstance(ai, dict) or set(ai) - {"skills", "agents", "plugins", "mcp", "hooks"}:
        raise ValueError("Неизвестная секция local.ai")
    if any(not isinstance(value, dict) for value in ai.values()):
        raise ValueError("local.ai содержит таблицы переопределений")
    return ai


def _apply_catalog_local(ai: dict, local: dict, aliases: dict[str, dict[str, str]],
                         root: Path, touched: dict[str, set[str]] | None = None) -> None:
    for section, overrides in local.items():
        key = "name" if section == "mcp" else "path"
        entries = {entry[key]: entry for entry in _catalog_entries(ai, section)}
        for name, enabled in overrides.items():
            resolved = name if section == "mcp" else aliases.get(section, {}).get(
                name, str((root / name).resolve()))
            if resolved not in entries:
                raise ValueError(f"local.ai.{section}: неизвестный источник {name}")
            _validate_enabled(section, enabled)
            entries[resolved]["enabled"] = deepcopy(enabled)
            if touched is not None:
                touched.setdefault(section, set()).add(resolved)


def _validate_enabled(section: str, enabled: object) -> None:
    if section in {"skills", "agents"}:
        if enabled is False:
            return
        if not isinstance(enabled, list) or any(not isinstance(v, str) for v in enabled):
            raise ValueError(f"ai.{section}.enabled требует список имён или false")
    elif not isinstance(enabled, bool):
        raise ValueError(f"ai.{section}.enabled требует bool")


def _normalize_catalog(document: dict, root: Path) -> dict[str, dict[str, str]]:
    aliases: dict[str, dict[str, str]] = {}
    ai = document.setdefault("ai", {})
    if not isinstance(ai, dict):
        raise ValueError("ai должен быть таблицей")
    for section in ("skills", "agents", "plugins", "hooks"):
        aliases[section] = {}
        for entry in _catalog_entries(ai, section):
            raw = entry["path"]
            entry["path"] = str((root / raw).resolve())
            aliases[section][raw] = entry["path"]
        _catalog_entries(ai, section)
    _catalog_entries(ai, "mcp")
    for entry in ai.get("mcp", []):
        if "server" in entry:
            entry["server"] = _file_values(entry["server"], root)
    return aliases


def global_context() -> ConfigContext:
    """Глобальный каталог с применёнными machine-only MCP и local.ai."""
    document = _strict_document(CONFIG)
    local_document = _strict_document(CONFIG_LOCAL)
    ai = document.setdefault("ai", {})
    if not isinstance(ai, dict):
        raise ValueError("ai должен быть таблицей")
    machine_ai = local_document.get("ai", {})
    if not isinstance(machine_ai, dict):
        raise ValueError("ai должен быть таблицей")
    local_mcp = _catalog_entries(machine_ai, "mcp")
    if local_mcp:
        ai["mcp"] = [*_catalog_entries(ai, "mcp"), *deepcopy(local_mcp)]
    aliases = _normalize_catalog(document, REPO_DIR)
    _apply_catalog_local(ai, _local_sections(local_document), aliases, REPO_DIR)
    return ConfigContext(document=document, local={}, root=REPO_DIR)


def load_project(path: Path) -> ConfigContext:
    """Разрешить проектный overlay поверх глобального каталога без записи файлов."""
    if path.is_symlink():
        raise ValueError("Проектный start.toml не должен быть symlink")
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Проектный конфиг не найден: {path}")
    project = _strict_document(path)
    if set(project) - {"ai", "local"}:
        raise ValueError("start.toml поддерживает только ai и local.ai")
    project_ai = project.get("ai", {})
    if not isinstance(project_ai, dict) or set(project_ai) - {
        "mcp", "skills", "agents", "plugins", "hooks", "platforms"
    }:
        raise ValueError("Неизвестная секция ai в start.toml")
    context = global_context()
    base_ai = context.document.setdefault("ai", {})
    # Относительный ключ существующего источника продолжает обозначать глобальный источник.
    original = _strict_document(CONFIG)
    aliases = _normalize_catalog(original, REPO_DIR)
    touched: dict[str, set[str]] = {}
    for section in ("mcp", "skills", "agents", "plugins", "hooks"):
        key = "name" if section == "mcp" else "path"
        entries = {entry[key]: entry for entry in _catalog_entries(base_ai, section)}
        seen: set[str] = set()
        for raw in _catalog_entries(project_ai, section):
            entry = deepcopy(raw)
            if section == "mcp" and "server" in entry:
                entry["server"] = _file_values(entry["server"], path.parent)
            name = entry[key]
            if key == "path":
                name = aliases[section].get(name, str((path.parent / name).resolve()))
                aliases[section][entry[key]] = name
                entry[key] = name
            if name in seen:
                raise ValueError(f"Дубль ai.{section}: {name}")
            seen.add(name)
            if "enabled" in entry:
                _validate_enabled(section, entry["enabled"])
            merged = _merge_tables(entries.get(name, {}), entry)
            if section == "mcp" and not isinstance(merged.get("server"), dict):
                raise ValueError(f"ai.mcp {name}: новый сервер требует server")
            if section == "hooks" and not isinstance(merged.get("events"), (list, dict)):
                raise ValueError(f"ai.hooks {name}: требуется events")
            if "platforms" in merged:
                warnings: list[str] = []
                _platforms(merged, name, warnings)
                if warnings:
                    raise ValueError("; ".join(warnings))
            entries[name] = merged
            touched.setdefault(section, set()).add(name)
        if entries:
            base_ai[section] = list(entries.values())
    platforms = project_ai.get("platforms", {})
    if not isinstance(platforms, dict) or set(platforms) - {"claude", "codex"}:
        raise ValueError("ai.platforms поддерживает claude и codex")
    for platform, settings in platforms.items():
        allowed = {"config", "features", "plugins", "skills"} if platform == "codex" else {"settings"}
        if not isinstance(settings, dict) or set(settings) - allowed:
            raise ValueError(f"Неизвестные настройки ai.platforms.{platform}")
        if any(not isinstance(value, dict) for value in settings.values()):
            raise ValueError(f"ai.platforms.{platform} содержит таблицы настроек")
    base_ai["platforms"] = _merge_tables(base_ai.get("platforms", {}), platforms)
    if platforms:
        touched["platforms"] = set(platforms)
    _apply_catalog_local(base_ai, _local_sections(project), aliases, path.parent, touched)
    context.root = path.parent
    context.project_keys = touched
    context.project_document = project
    return context


# --- модель -------------------------------------------------------------------

@dataclass
class Skill:
    """Найденный скил: имя папки, абсолютный путь, источник, статус."""
    name: str
    path: Path
    source: str          # path источника из config.toml
    enabled: bool        # эффективный: имя в enabled-спеке (с учётом local overlay)
    description: str = ""
    # Внешние зависимости из [[skills.requirements]] источника: [{name, check, hint}].
    # check — shell-команда проверки наличия (rc 0 = есть); hint — как поставить.
    # Необязательный install задаёт установку при up; без него выводится hint. Напр. локальный
    # Skottie-плеер для рендера.
    requirements: list[dict] = field(default_factory=list)
    platforms: tuple[str, ...] = ("claude", "codex")


@dataclass
class Agent:
    """Найденный агент: имя файла без .md, путь, источник, описание из frontmatter."""
    name: str
    path: Path
    source: str = ""     # path источника из config.toml ([[agents]])
    description: str = ""
    platforms: tuple[str, ...] = ("claude", "codex")


@dataclass
class Plugin:
    """Нативный CC-плагин из [[plugins]]: каталог с .claude-plugin/.

    marketplace/plugin читаются из .claude-plugin/marketplace.json+plugin.json
    (override полями в config.toml). enabled — bool (плагин атомарен). seed-сборка
    и enabledPlugins ведутся по паре (plugin, marketplace).
    """
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
    # Состояние для TUI: base — из config.toml; local — из config.local.toml (None = нет).
    enabled_base: bool = True
    enabled_local: bool | None = None
    platforms: tuple[str, ...] = ("claude", "codex")
    codex_exclude_skills: tuple[str, ...] = ()
    # Native representation(s) available in the source tree. Adapters prefer these
    # and only generate the missing platform representation.
    platform_paths: dict[str, Path] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """Идентификатор плагина для enabledPlugins: '<plugin>@<marketplace>'."""
        return f"{self.plugin}@{self.marketplace}"


@dataclass
class McpServer:
    """MCP-сервер из [[mcp]] (user-scope: пишется в ~/.claude.json mcpServers).

    inline-спека [mcp.server] (command/args/env | url/headers). enabled — bool
    (с учётом local overlay). enabled_base/enabled_local — для TUI (local/global).
    """
    name: str
    enabled: bool
    server: dict | None = None  # inline-спека {command,args,env|url,headers}
    enabled_base: bool = True
    enabled_local: bool | None = None
    platforms: tuple[str, ...] = ("claude", "codex")
    local_only: bool = False


@dataclass
class TaskFilter:
    """Поле фильтра интерактивной задачи; options пуст для свободного ввода."""
    name: str
    label: str
    default: str = ""
    options: tuple[str, ...] = ()


@dataclass
class Task:
    """Команда домена «Команды»: shell-действие или интерактивная таблица.

    run — команда по платформам (ключи sys.platform: darwin/linux/win32). На текущей
    ОС берётся run[sys.platform]; нет ключа под неё → команда недоступна здесь
    (`command` == None). view — platform→module:function для async table provider.
    Для run sudo является пометкой, сам sudo задаётся командой; для view менеджер
    получает sudo timestamp и TaskContext запускает команды привилегированно.
    """
    name: str
    title: str
    description: str
    run: dict[str, str]        # platform (sys.platform) -> команда
    sudo: bool = False
    view: dict[str, str] = field(default_factory=dict)
    filters: list[TaskFilter] = field(default_factory=list)
    refresh: float = 2.0
    category: str = "Общее"

    @property
    def provider(self) -> str | None:
        return self.view.get(sys.platform)

    @property
    def command(self) -> str | None:
        """Команда или provider для текущей ОС (None, если варианта нет)."""
        cmd = self.run.get(sys.platform) or self.provider
        return cmd if isinstance(cmd, str) and cmd.strip() else None


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
    if path.suffix == ".toml":
        try:
            return str(tomllib.loads(path.read_text()).get(key) or "")
        except (OSError, tomllib.TOMLDecodeError):
            return ""
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


def _ai(doc: dict) -> dict:
    """Суб-таблица [ai] из config.toml.

    Старый [claude] намеренно не читается: каталог AI мигрируется атомарно и не
    должен незаметно расходиться между двумя форматами.
    """
    value = doc.get("ai")
    return value if isinstance(value, dict) else {}


def _legacy_schema_warning(doc: dict, warnings: list[str]) -> None:
    if isinstance(doc.get("claude"), dict):
        warnings.append(
            "устаревшая секция [claude.*]; перенесите её в [ai.*] "
            "(старый формат не поддерживается)"
        )


def _platforms(entry: dict, rel: str, warnings: list[str]) -> tuple[str, ...]:
    """Платформы компонента; по умолчанию строгая поддержка обеих."""
    raw = entry.get("platforms", ["claude", "codex"])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        warnings.append(f"{rel}: platforms должен быть списком — использую обе платформы")
        return ("claude", "codex")
    out: list[str] = []
    for item in raw:
        platform = str(item).strip().lower()
        if platform not in ("claude", "codex"):
            warnings.append(f"{rel}: неизвестная платформа '{platform}' — игнорирую")
            continue
        if platform not in out:
            out.append(platform)
    if not out:
        warnings.append(f"{rel}: platforms пуст — компонент не будет установлен")
    return tuple(out)


def _files(doc: dict) -> dict:
    """Суб-таблица [files] из config.toml ({} если нет). Домен Files ($HOME): dotfiles."""
    f = doc.get("files")
    return f if isinstance(f, dict) else {}


def _commands(doc: dict) -> dict:
    """Суб-таблица [commands] из config.toml ({} если нет). Домен «Команды»: разовые
    действия (tasks), запускаемые из TUI по требованию (не провижининг, up не трогает)."""
    c = doc.get("commands")
    return c if isinstance(c, dict) else {}


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
    """`enabled`-список источника: ["*"] — все, [] — ни одного."""
    raw = entry.get("enabled", ["*"])
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _load_local(warnings: list[str]) -> dict:
    """Секция [local.ai] из config.local.toml ({} если файла нет).

    Формат: [local.<section>] <key> = <enabled>. section ∈ skills/agents/plugins/mcp.
    key — path источника (или name для mcp). Переопределяет enabled;
    machine-only MCP-источники объявляются отдельно через [[ai.mcp]] в том же файле.
    """
    doc = _load_doc(CONFIG_LOCAL, warnings)
    local = doc.get("local", {})
    if not isinstance(local, dict):
        return {}
    ai = local.get("ai", {})
    return ai if isinstance(ai, dict) else {}


def _effective_spec(lsec: dict, key: str, base_entry: dict) -> list[str]:
    """Эффективный enabled-spec: overlay если ключ есть в lsec, иначе base_entry."""
    if key in lsec:
        return _enabled_spec({"enabled": lsec[key]})
    return _enabled_spec(base_entry)


def _effective_bool(lsec: dict, key: str, base_entry: dict) -> bool:
    """Эффективный enabled-bool: overlay если ключ есть в lsec, иначе base_entry."""
    if key in lsec:
        return _bool_enabled({"enabled": lsec[key]})
    return _bool_enabled(base_entry)


def _select_names(spec: list[str], available: list[str]) -> set[str]:
    """Какие скилы источника включены: разворачивает "*" в все доступные имена."""
    if "*" in spec:
        return set(available)
    return set(spec)


def load(context: ConfigContext | None = None) -> ConfigResult:
    """Разобрать config.toml и обнаружить все скилы.

    Конфликты имён скилов: берётся первое вхождение, дубль попадает в warnings.
    В .skills попадают ВСЕ найденные скилы источника (для UI); enabled=False у
    тех, чьё имя не входит в `enabled`-список источника. exclude убирает скил
    совсем (даже из UI).
    """
    res = ConfigResult()
    base = (context.document if context else _load_doc(CONFIG, res.warnings))
    _legacy_schema_warning(base, res.warnings)
    lsec = (context.local if context else _load_local(res.warnings)).get("skills", {})

    if not base and not CONFIG.is_file():
        res.warnings.append(f"{CONFIG.name} не найден — скилы не линкуются")
        return res

    seen: dict[str, Skill] = {}
    for rel, entry in _sources(_ai(base), res.warnings):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            res.warnings.append(
                f"источник не найден: {rel} (нет папки; для сабмодуля — "
                f"git submodule update --init)")
            continue

        exclude = set(entry.get("exclude", []))
        requirements = _parse_requirements(entry, rel, res.warnings)
        available = {p.name: p for p in root.iterdir() if is_skill(p)}

        spec = _effective_spec(lsec, rel, entry)
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
                requirements=requirements,
                platforms=_platforms(entry, rel, res.warnings),
            )

    # Порядок: источники в порядке config.toml, внутри — имена по алфавиту
    # (seen заполнялся именно так). Для группировки по источнику в UI.
    res.skills = list(seen.values())
    return res


def _discover_agents(context: ConfigContext | None = None) -> tuple[list[Agent], list[str]]:
    """Все агенты из [[agents]]-источников config.toml и warnings.

    Зеркало skill-цикла в load(): источники в порядке config.toml, внутри —
    имена по алфавиту. Конфликт имён: первое вхождение, дубль → warnings
    (как у скилов). Линкуются только включённые `enabled`-списком.
    """
    warnings: list[str] = []
    base = (context.document if context else _load_doc(CONFIG, warnings))
    _legacy_schema_warning(base, warnings)
    lsec = (context.local if context else _load_local(warnings)).get("agents", {})

    seen: dict[str, Agent] = {}
    for rel, entry in _sources(_ai(base), warnings, key="agents"):
        root = (REPO_DIR / rel).resolve()
        if not root.is_dir():
            warnings.append(
                f"источник агентов не найден: {rel} (нет папки; для сабмодуля — "
                f"git submodule update --init)")
            continue

        exclude = set(entry.get("exclude", []))
        available: dict[str, Path] = {}
        for pattern in ("*.md", "*.toml"):
            for path in sorted(root.glob(pattern)):
                available.setdefault(path.stem, path)

        spec = _effective_spec(lsec, rel, entry)
        selected = _select_names(spec, list(available))
        for n in spec:
            if n != "*" and n not in available:
                warnings.append(f"{rel}: агент '{n}' не найден (нет {n}.md)")

        for n in sorted(set(available) & selected):
            if n in exclude:
                continue
            if n in seen:
                warnings.append(
                    f"дубль имени агента '{n}': {rel} — пропуск "
                    f"(уже взят из {seen[n].source})")
                continue
            seen[n] = Agent(name=n, path=available[n], source=rel,
                            description=_read_description(available[n]),
                            platforms=_platforms(entry, rel, warnings))
    return list(seen.values()), warnings


def load_agents(context: ConfigContext | None = None) -> list[Agent]:
    """Все агенты из [[agents]]-источников. Warnings глушатся (для TUI)."""
    agents, _ = _discover_agents(context)
    return agents


# --- плагины (нативные CC) ----------------------------------------------------

def _bool_enabled(entry: dict) -> bool:
    """`enabled` источника как bool. Дефолт True."""
    raw = entry.get("enabled", True)
    return raw if isinstance(raw, bool) else True


def read_plugin_manifest(path: Path) -> tuple[str, str, list[str]]:
    """Прочитать .claude-plugin плагина: (marketplace_name, plugin_name, session_start_cmds).

    marketplace_name — из marketplace.json `name`. plugin_name — из marketplace.json
    `plugins[0].name` (это install-идентификатор для `<plugin>@<mp>`; plugin.json `name`
    может отличаться и НЕ используется CC при install — fallback только если в
    marketplace.json нет plugins[]). session_start_cmds — shell-команды SessionStart-хуков
    из hooks/hooks.json (для предупреждения). Пустые строки при отсутствии/ошибке.
    """
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


def _json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _native_plugin_paths(root: Path) -> dict[str, Path]:
    """Найти нативные корни Claude/Codex внутри source.

    Marketplace-repositories часто держат реальный plugin в подпапке. Корневой
    manifest имеет приоритет, затем выбирается первый путь по алфавиту.
    """
    found: dict[str, Path] = {}
    for platform, marker in (
        ("claude", ".claude-plugin/plugin.json"),
        ("codex", ".codex-plugin/plugin.json"),
    ):
        marketplace = _json_object(root / f".{platform}-plugin" / "marketplace.json")
        entries = marketplace.get("plugins", [])
        if isinstance(entries, list) and entries:
            source = entries[0].get("source") if isinstance(entries[0], dict) else None
            if isinstance(source, dict):
                source = source.get("path")
            if isinstance(source, str):
                candidate = (root / source).resolve()
                if (candidate / marker).is_file():
                    found[platform] = candidate
                    continue
        direct = root / marker
        if direct.is_file():
            found[platform] = root
            continue
        matches = sorted(root.glob(f"*/{marker}")) + sorted(root.glob(f"*/*/{marker}"))
        if matches:
            found[platform] = matches[0].parent.parent
    return found


def _plugin_metadata(root: Path, native: dict[str, Path]) -> tuple[str, str, str]:
    """Общая идентичность plugin: marketplace, name, description."""
    mp, name, _ = read_plugin_manifest(root)
    description = ""
    for platform in ("claude", "codex"):
        plugin_root = native.get(platform)
        if not plugin_root:
            continue
        manifest = _json_object(plugin_root / f".{platform}-plugin" / "plugin.json")
        name = name or str(manifest.get("name") or "")
        description = description or str(manifest.get("description") or "")
    return mp or "personal", name, description


def _scan_session_start(path: Path) -> list[str]:
    """SessionStart shell-команды из hooks/hooks.json плагина ([] при отсутствии)."""
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
    """Разобрать `requirements`: name, check, hint и необязательный install.

    Общий парсер для [[plugins.requirements]], [[skills.requirements]] и
    [[statusline.requirements]]. nested array-of-tables → entry["requirements"] =
    list[dict]. Каждая запись обязана иметь непустые check (shell-команда проверки) и
    hint (как поставить); install — shell-команда установки при отсутствии зависимости.
    name опционален (для вывода). Битые записи пропускаются с warning.
    """
    out: list[dict] = []
    raw = entry.get("requirements", [])
    if not isinstance(raw, list):
        warnings.append(f"{rel}: requirements не список — игнорирую")
        return out
    for req in raw:
        if not isinstance(req, dict):
            continue
        check = (req.get("check") or "").strip()
        hint = (req.get("hint") or "").strip()
        name = (req.get("name") or "").strip()
        if not check or not hint:
            warnings.append(f"{rel}: requirements без check/hint — пропуск")
            continue
        requirement = {"name": name, "check": check, "hint": hint}
        install = req.get("install", "")
        if not isinstance(install, str):
            warnings.append(f"{rel}: requirements.install должен быть строкой — пропуск")
            continue
        if install.strip():
            requirement["install"] = install.strip()
        out.append(requirement)
    return out


def _discover_plugins(context: ConfigContext | None = None) -> tuple[list[Plugin], list[str]]:
    """Все плагины из [[plugins]]-источников config.toml + warnings.

    path → корень плагина (каталог с .claude-plugin/). marketplace/plugin читаются
    из манифеста (override полями marketplace/plugin в записи). Дубль ref → warning.
    """
    warnings: list[str] = []
    base = (context.document if context else _load_doc(CONFIG, warnings))
    _legacy_schema_warning(base, warnings)
    lsec = (context.local if context else _load_local(warnings)).get("plugins", {})

    seen: dict[str, Plugin] = {}
    for rel, entry in _sources(_ai(base), warnings, key="plugins"):
        root = (REPO_DIR / rel).resolve()
        native = _native_plugin_paths(root) if root.is_dir() else {}
        if not native:
            warnings.append(
                f"источник плагина не найден или без native manifest: {rel} "
                f"(для сабмодуля — git submodule update --init)")
            continue

        mp_name, plugin_name, description = _plugin_metadata(root, native)
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
            path=root, source=rel,
            marketplace=mp_name, plugin=plugin_name,
            enabled=_effective_bool(lsec, rel, entry),
            description=description,
            session_start_hooks=(
                _scan_session_start(native["claude"]) if "claude" in native else []
            ),
            requirements=_parse_requirements(entry, rel, warnings),
            enabled_base=_bool_enabled(entry),
            enabled_local=(_bool_enabled({"enabled": lsec[rel]}) if rel in lsec else None),
            platforms=_platforms(entry, rel, warnings),
            codex_exclude_skills=tuple(
                str(name) for name in entry.get("codex_exclude_skills", [])
            ),
            platform_paths=native,
        )
    return list(seen.values()), warnings


def load_plugins(context: ConfigContext | None = None) -> list[Plugin]:
    """Все плагины из [[plugins]]-источников. Warnings глушатся (для TUI)."""
    plugins, _ = _discover_plugins(context)
    return plugins


# --- MCP-серверы --------------------------------------------------------------

def load_statusline(platform: str = "claude") -> dict | None:
    """`[statusline]` из config.toml: {path, dest, command, requirements} или None.

    path — *.mjs/*.sh относительно репо; dest — имя в ~/.claude/; command — строка
    для settings.json `statusLine`. requirements — внешние зависимости команды
    ([[statusline.requirements]], та же схема, что у плагинов: {name, check, hint});
    напр. `node` для .mjs-статусбара. None если секции нет/неполная.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    sl = _ai(base).get("statusline")
    if not isinstance(sl, dict):
        return None
    platform_sl = sl.get(platform)
    if isinstance(platform_sl, dict):
        sl = platform_sl
    elif platform == "codex":
        return None
    if platform == "codex":
        items = sl.get("items")
        if not isinstance(items, list):
            return None
        return {
            "items": [str(x) for x in items],
            "use_colors": bool(sl.get("use_colors", True)),
        }
    path = (sl.get("path") or "").strip()
    command = (sl.get("command") or "").strip()
    if not path or not command:
        return None
    dest = (sl.get("dest") or Path(path).name).strip()
    requirements = _parse_requirements(sl, "config.toml [statusline]", warnings)
    return {"path": path, "dest": dest, "command": command,
            "requirements": requirements}


def load_env() -> dict[str, str]:
    """`[env]` из config.toml: произвольные env-переменные для ~/.claude/settings.json.

    Менеджер мержит их в settings.json `env` (managed, через sidecar). Значения — строки.
    Напр. ENABLE_CLAUDEAI_MCP_SERVERS = "false" (отключить claude.ai connectors).
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    env = _ai(base).get("platforms", {}).get("claude", {}).get("env", {})
    if not isinstance(env, dict):
        return {}
    return {str(k): str(v) for k, v in env.items()}


def load_codex_flags(section: Literal["features", "plugins", "skills"],
                     context: ConfigContext | None = None) -> dict[str, bool]:
    """Bool-флаги `[ai.platforms.codex.<section>]` из config.toml."""
    warnings: list[str] = []
    base = (context.document if context else _load_doc(CONFIG, warnings))
    flags = _ai(base).get("platforms", {}).get("codex", {}).get(section, {})
    if not isinstance(flags, dict):
        return {}
    return {str(key): value for key, value in flags.items() if isinstance(value, bool)}


def load_hooks(platform: str, context: ConfigContext | None = None) -> tuple[list[dict], list[str]]:
    """Loose hooks enabled for a platform."""
    warnings: list[str] = []
    base = (context.document if context else _load_doc(CONFIG, warnings))
    out: list[dict] = []
    for entry in _ai(base).get("hooks", []):
        if not isinstance(entry, dict):
            continue
        if not _bool_enabled(entry):
            continue
        path = str(entry.get("path") or "").strip()
        if not path:
            warnings.append("[[ai.hooks]] требует path и events")
            continue
        if platform not in _platforms(entry, path, warnings):
            continue
        events = entry.get("events")
        if isinstance(events, dict):
            events = events.get(platform)
        if not isinstance(events, list):
            warnings.append("[[ai.hooks]] требует path и events")
            continue
        out.append({"path": path, "events": [str(event) for event in events]})
    return out, warnings


def load_dotfiles() -> tuple[list[dict], list[str]]:
    """`[[files.dotfiles]]` из config.toml: список {source, target?, posthook?} + warnings.

    source — путь к файлу/папке относительно корня репо (обязателен); target — путь
    назначения в $HOME (поддержка ~ и абсолютных, разворачивается на этапе install;
    опционален — без него симлинк не ставится); posthook — shell-команда, выполняемая
    после раскладки записи (опциональна; env SOURCE/TARGET, см. cli/files.py). Не про
    Claude Code — про общий сетап машины. Запись без source, либо без target и без
    posthook (ничего бы не сделала), пропускается с предупреждением. Секции нет —
    пустой список.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    raw = _files(base).get("dotfiles", [])
    if not isinstance(raw, list):
        return [], warnings
    out: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        posthook = str(item.get("posthook") or "").strip()
        if not source:
            warnings.append(f"[[files.dotfiles]] #{i}: пустой source — пропуск")
            continue
        if not target and not posthook:
            warnings.append(
                f"[[files.dotfiles]] #{i}: нет ни target, ни posthook — пропуск")
            continue
        out.append({"source": source, "target": target, "posthook": posthook})
    return out, warnings


def load_tasks() -> tuple[list[Task], list[str]]:
    """`[[commands.tasks]]` из config.toml: список Task домена «Команды» + warnings.

    Разовые действия на машине, запускаемые из TUI (не провижининг — up их не трогает).
    Запись: name (обяз.), title (по умолчанию = name), description, run или view
    (platform→команда/provider), sudo, filters и refresh. Битые записи —
    предупреждение и пропуск. Секции нет — пустой список.
    """
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    raw = _commands(base).get("tasks", [])
    if not isinstance(raw, list):
        return [], warnings
    out: list[Task] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        run = item.get("run", {})
        view = item.get("view", {})
        if not name or not isinstance(run, dict) or not isinstance(view, dict) or not (run or view):
            warnings.append(
                f"[[commands.tasks]] #{i}: нужен name и непустой run или view — пропуск")
            continue
        try:
            refresh = float(item.get("refresh", 2))
            if not 0.2 <= refresh <= 3600:
                raise ValueError("refresh должен быть от 0.2 до 3600 секунд")
            filters: list[TaskFilter] = []
            for spec in item.get("filters", []):
                key = spec["name"]
                if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", key):
                    raise ValueError("некорректное имя фильтра")
                if any(f.name == key for f in filters):
                    raise ValueError(f"повторный фильтр {key}")
                options = spec.get("options", [])
                if not isinstance(options, list) or any(not isinstance(o, str) for o in options):
                    raise ValueError("options должен быть списком строк")
                default = str(spec.get("default", ""))
                if options and default not in options:
                    raise ValueError(f"default фильтра {key} отсутствует в options")
                filters.append(TaskFilter(key, str(spec.get("label", key)), default, tuple(options)))
            if any(not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z_][\w.]*:[a-zA-Z_]\w*", v) for v in view.values()):
                raise ValueError("view должен содержать ссылки module:function")
            if set(run) & set(view):
                raise ValueError("run и view не могут задавать одну ОС одновременно")
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"задача '{name}': {exc} — пропуск")
            continue
        out.append(Task(
            name=name,
            title=str(item.get("title") or name).strip(),
            description=str(item.get("description") or "").strip(),
            run={str(k): str(v) for k, v in run.items()},
            sudo=bool(item.get("sudo", False)),
            view=dict(view), filters=filters, refresh=refresh,
            category=str(item.get("category") or "Общее").strip() or "Общее",
        ))
    return out, warnings


def load_mcp(context: ConfigContext | None = None) -> tuple[list[McpServer], list[str]]:
    """MCP-серверы из [[ai.mcp]] config.toml и config.local.toml + warnings.

    [[ai.mcp]] — name-keyed (не path), поэтому отдельный ридер. Каждая запись:
    name (обяз.), enabled (bool, дефолт True), source (.mcp.json для symlink) либо
    inline [ai.mcp.server]. Локальный файл расширяет каталог; дубль name → warning.
    """
    warnings: list[str] = []
    base = (context.document if context else _load_doc(CONFIG, warnings))
    _legacy_schema_warning(base, warnings)
    local_doc = {"local": {"ai": context.local}} if context else _load_doc(CONFIG_LOCAL, warnings)
    local = local_doc.get("local", {})
    local_ai = local.get("ai", {}) if isinstance(local, dict) else {}
    lsec = local_ai.get("mcp", {}) if isinstance(local_ai, dict) else {}
    if not isinstance(lsec, dict):
        lsec = {}

    seen: dict[str, McpServer] = {}
    for doc, filename, local_only in (
        (base, CONFIG.name, False),
        (local_doc, CONFIG_LOCAL.name, True),
    ):
        entries = _ai(doc).get("mcp", [])
        if not isinstance(entries, list):
            warnings.append(f"[[ai.mcp]] в {filename} должен быть списком — пропуск")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                warnings.append(f"[[ai.mcp]] без name в {filename} — пропуск")
                continue
            if name in seen:
                warnings.append(f"дубль MCP '{name}' в {filename} — пропуск")
                continue
            server = entry.get("server")
            if isinstance(server, dict) and _effective_bool(lsec, name, entry):
                server = _file_values(server, context.root if context else REPO_DIR, read=True)
            enabled_local = (
                _effective_bool(lsec, name, entry) if local_only
                else (_bool_enabled({"enabled": lsec[name]}) if name in lsec else None)
            )
            seen[name] = McpServer(
                name=name,
                enabled=_effective_bool(lsec, name, entry),
                server=server if isinstance(server, dict) else None,
                enabled_base=False if local_only else _bool_enabled(entry),
                enabled_local=enabled_local,
                platforms=_platforms(entry, name, warnings),
                local_only=local_only,
            )
    for n in lsec:
        if n not in seen:
            warnings.append(f"config.local.toml [local.ai.mcp]: '{n}' не объявлен — игнор")
    return list(seen.values()), warnings


# --- запись (toggle enabled) --------------------------------------------------

def _ai_aot(doc, key):
    """Найти/создать array-of-tables [[ai.<key>]] в tomlkit-документе.

    Домен Claude вложен в таблицу [claude] (см. _claude/_files). Создаёт таблицу
    [claude] и вложенный AoT при отсутствии; комментарии/форматирование сохраняются.
    """
    ai = doc.get("ai")
    if ai is None:
        ai = tomlkit.table()
        doc["ai"] = ai
    aot = ai.get(key)
    if aot is None:
        aot = tomlkit.aot()
        ai[key] = aot
    return aot


def _write_aot_enabled(
    section: Literal["skills", "plugins", "mcp"],
    key: Literal["path", "name"],
    identifier: str,
    enabled: bool | list[str],
) -> None:
    """Записать `enabled` в запись `[[ai.<section>]]`, сохранив TOML-форматирование."""
    doc = tomlkit.parse(CONFIG.read_text()) if CONFIG.is_file() else tomlkit.document()
    entries = _ai_aot(doc, section)
    target = None
    for tbl in entries:
        if tbl.get(key) == identifier:
            target = tbl
            break
    if target is None:
        target = tomlkit.table()
        target[key] = identifier
        entries.append(target)

    if isinstance(enabled, list):
        value = tomlkit.array()
        value.multiline(False)
        value.extend(enabled)
        target["enabled"] = value
    else:
        target["enabled"] = enabled

    CONFIG.write_text(tomlkit.dumps(doc))


def _set_skill_enabled(
    source: str,
    name: str,
    enabled: bool,
    scope: Literal["global", "local"],
) -> None:
    """Изменить включение скила в глобальной конфигурации или локальном overlay."""
    warnings: list[str] = []
    entry: dict = {}
    for rel, e in _sources(_ai(_load_doc(CONFIG, warnings)), warnings):
        if rel == source:
            entry = e
            break
    root = (REPO_DIR / source).resolve()
    available = sorted(p.name for p in root.iterdir() if is_skill(p)) if root.is_dir() else []

    if scope == "local":
        spec = _effective_spec(_load_local(warnings).get("skills", {}), source, entry)
    else:
        spec = _enabled_spec(entry)
    selected = _select_names(spec, available)
    if enabled:
        selected.add(name)
    else:
        selected.discard(name)

    new_spec = ["*"] if selected >= set(available) and available else sorted(selected)
    if scope == "local":
        _write_local("skills", source, new_spec)
    else:
        _write_aot_enabled("skills", "path", source, new_spec)


def set_skill_enabled(source: str, name: str, enabled: bool) -> None:
    """Вкл/выкл скил `name` источника `source` в config.toml."""
    _set_skill_enabled(source, name, enabled, "global")


def set_source_enabled(source: str, enabled: bool) -> None:
    """Вкл/выкл ВСЕ скилы источника `source` разом, правя config.toml.

    enabled=True  → enabled = ["*"] (все);
    enabled=False → enabled = []   (ни одного).
    """
    _write_aot_enabled("skills", "path", source, ["*"] if enabled else [])


def add_source(
    rel_path: str,
    *,
    section: Literal["skills", "agents", "plugins"] = "skills",
    exclude: list[str] | None = None,
) -> bool:
    """Добавить AI-source в версионный config.toml; дубль path игнорируется."""
    warnings: list[str] = []
    base = _load_doc(CONFIG, warnings)
    paths = {
        entry["path"]
        for entry in _entries(_ai(base), CONFIG.name, warnings, key=section)
    }
    if rel_path in paths:
        return False

    # Рендерим новый [[ai.*]] как текст и дописываем в конец файла. tomlkit
    # при append в AoT кладёт отбивку внутрь header'а ([[ai.*]] + пустая
    # строка), что ломает выравнивание; текстовый append даёт ровно тот же стиль, что в base.
    enabled = "true" if section == "plugins" else '["*"]'
    block = (f"\n[[ai.{section}]]\n"
             f'path = "{rel_path}"\n'
             f"enabled = {enabled}\n")
    if exclude:
        block += f"exclude = {tomlkit.item(exclude).as_string()}\n"

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
    _write_aot_enabled("plugins", "path", source, enabled)


def set_mcp_enabled(name: str, enabled: bool) -> None:
    """Вкл/выкл MCP `name` глобально, правя `enabled` в [[mcp]] config.toml (по name)."""
    _write_aot_enabled("mcp", "name", name, enabled)


# --- запись (локальный overlay config.local.toml) -----------------------------

def _write_local(section: str, key: str, value) -> None:
    """Записать [local.<section>] <key> = value в config.local.toml (tomlkit).

    value — bool (plugins/mcp) или list[str] (skills/agents). Создаёт файл и
    таблицы при необходимости. Комментарии/форматирование сохраняются.
    """
    doc = tomlkit.parse(CONFIG_LOCAL.read_text()) if CONFIG_LOCAL.is_file() else tomlkit.document()
    local = doc.get("local")
    if local is None:
        local = tomlkit.table()
        doc["local"] = local
    ai = local.get("ai")
    if ai is None:
        ai = tomlkit.table()
        local["ai"] = ai
    sect = ai.get(section)
    if sect is None:
        sect = tomlkit.table()
        ai[section] = sect

    if isinstance(value, list):
        arr = tomlkit.array()
        arr.multiline(False)
        arr.extend(value)
        sect[key] = arr
    else:
        sect[key] = value
    CONFIG_LOCAL.write_text(tomlkit.dumps(doc))


def set_plugin_enabled_local(source: str, enabled: bool) -> None:
    """Локально (config.local.toml) вкл/выкл плагин-источник по path."""
    _write_local("plugins", source, enabled)


def set_mcp_enabled_local(name: str, enabled: bool) -> None:
    """Локально (config.local.toml) вкл/выкл MCP по name."""
    _write_local("mcp", name, enabled)


def set_source_enabled_local(source: str, enabled: bool) -> None:
    """Локально вкл/выкл ВСЕ скилы источника: ["*"] / []."""
    _write_local("skills", source, ["*"] if enabled else [])


def set_skill_enabled_local(source: str, name: str, enabled: bool) -> None:
    """Локально вкл/выкл скил `name` источника в config.local.toml."""
    _set_skill_enabled(source, name, enabled, "local")
