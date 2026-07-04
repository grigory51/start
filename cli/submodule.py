"""submodule.py — добавление git-сабмодуля в contrib/ + регистрация source.

Логика общая для CLI (`start add-submodule`) и TUI (модалка в manage).

Шаги add_submodule:
  1. git submodule add <url> contrib/<name>
  2. автодетект подпапки со скилами (корень / ./skills / ./contrib/*)
  3. config.add_source(path) — записать [[skills]] в версионный config.toml
  4. (опц.) install — разложить symlink'и нового источника

Имя <name> по умолчанию выводится из URL (basename без .git). skills_subdir
можно задать вручную (override автодетекта); пустая строка = корень сабмодуля.

Регистрируется только источник скилов ([[skills]]). Если в сабмодуле есть
агенты (папка с *.md), [[agents]] в config.toml добавляется вручную.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config
from .config import REPO_DIR, is_skill

CONTRIB = REPO_DIR / "contrib"

# Где искать папки со скилами внутри клонированного сабмодуля, в порядке приоритета.
# Относительно корня сабмодуля. "" = сам корень.
_SUBDIR_CANDIDATES = ["", "skills"]


@dataclass
class AddResult:
    ok: bool
    message: str
    source_path: str = ""    # rel-путь источника в config.toml (contrib/<name>[/sub])
    submodule_path: str = ""  # rel-путь сабмодуля (contrib/<name>)
    install_errors: int = 0


def name_from_url(url: str) -> str:
    """basename URL без .git. git@host:owner/repo.git → repo; .../repo → repo."""
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def _has_skill_dirs(d: Path) -> bool:
    return d.is_dir() and any(is_skill(p) for p in d.iterdir())


def detect_skills_subdir(root: Path) -> tuple[str | None, list[str]]:
    """Найти подпапку со скилами внутри сабмодуля.

    Проверяет кандидатов (_SUBDIR_CANDIDATES), затем contrib/* (как у claude-seo,
    где skills рядом с прочими каталогами). Возвращает (subdir, all_matches):
      subdir       — единственный однозначный кандидат, иначе None (требует выбора);
      all_matches  — все найденные подпапки (rel к root) для подсказки/ошибки.
    """
    matches: list[str] = []
    for cand in _SUBDIR_CANDIDATES:
        if _has_skill_dirs(root / cand if cand else root):
            matches.append(cand)
    # Вложенные contrib/<x>/skills и т.п. — один уровень вглубь.
    nested = root / "contrib"
    if nested.is_dir():
        for sub in sorted(nested.iterdir()):
            if _has_skill_dirs(sub):
                matches.append(str(sub.relative_to(root)))

    matches = list(dict.fromkeys(matches))  # дедуп, порядок сохранён
    return (matches[0] if len(matches) == 1 else None), matches


def add_submodule(
    url: str,
    *,
    name: str | None = None,
    skills_subdir: str | None = None,
    do_install: bool = True,
    quiet: bool = False,
) -> AddResult:
    """Добавить сабмодуль и зарегистрировать источник скилов.

    name          — имя папки в contrib/ (по умолчанию из URL).
    skills_subdir — подпапка со скилами относительно корня сабмодуля. None =
                    автодетект; "" = корень. Если автодетект неоднозначен и
                    skills_subdir не задан — вернуть ошибку со списком кандидатов.
    do_install    — после регистрации разложить symlink'и (run_up без сабмодулей).

    Сетевые/файловые ошибки не бросаются наружу — оборачиваются в AddResult(ok=False).
    """
    url = url.strip()
    if not url:
        return AddResult(False, "пустой URL")

    name = (name or name_from_url(url)).strip()
    if not name or "/" in name or name in (".", ".."):
        return AddResult(False, f"некорректное имя сабмодуля: {name!r}")

    sub_path = CONTRIB / name
    sub_rel = f"contrib/{name}"
    if sub_path.exists():
        return AddResult(False, f"{sub_rel} уже существует — выберите другое имя (--name)")

    # 1. git submodule add
    proc = subprocess.run(
        ["git", "submodule", "add", url, sub_rel],
        cwd=REPO_DIR, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        return AddResult(False, f"git submodule add не удался: {err}")

    # 2a. нативный плагин? (.claude-plugin/) → регистрируем как [[plugins]], не [[skills]].
    if (sub_path / ".claude-plugin").is_dir():
        added = config.add_plugin_source(sub_rel)
        install_errors = 0
        if do_install:
            from .up import run_up
            install_errors = run_up(skip_submodules=True, quiet=quiet)
        mp, plugin, _ = config.read_plugin_manifest(sub_path)
        ref = f"{plugin}@{mp}" if plugin and mp else sub_rel
        src_note = "плагин зарегистрирован" if added else "плагин уже был в config.toml"
        return AddResult(
            ok=True,
            message=f"{sub_rel} подключён как нативный плагин ({ref}), {src_note}",
            source_path=sub_rel,
            submodule_path=sub_rel,
            install_errors=install_errors,
        )

    # 2. определить подпапку со скилами
    if skills_subdir is None:
        detected, matches = detect_skills_subdir(sub_path)
        if detected is None:
            if not matches:
                hint = ("скилы (папки с SKILL.md) не найдены ни в корне, ни в ./skills. "
                        "Укажите подпапку вручную (--skills-subdir) или '' для корня.")
            else:
                hint = (f"несколько кандидатов со скилами: {matches}. "
                        f"Укажите нужную через --skills-subdir.")
            return AddResult(False, f"{sub_rel} добавлен, но источник не определён: {hint}",
                             submodule_path=sub_rel)
        skills_subdir = detected

    skills_subdir = skills_subdir.strip().strip("/")
    source_rel = f"{sub_rel}/{skills_subdir}" if skills_subdir else sub_rel

    # 3. записать [[skills]] в config.toml
    added = config.add_source(source_rel)
    if not added and not quiet:
        # path уже был — не ошибка, но сообщим.
        pass

    # 4. install
    install_errors = 0
    if do_install:
        from .up import run_up
        install_errors = run_up(skip_submodules=True, quiet=quiet)

    src_note = "источник добавлен" if added else "источник уже был в config.toml"
    return AddResult(
        ok=True,
        message=f"{sub_rel} подключён, {src_note}: {source_rel}",
        source_path=source_rel,
        submodule_path=sub_rel,
        install_errors=install_errors,
    )
