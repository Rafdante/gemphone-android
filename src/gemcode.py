#!/usr/bin/env python3
"""
GemCode — минималистичный консольный ИИ-агент для разработки (в стиле Claude Code),
работающий через Google AI Studio API (Gemini) без внешних зависимостей.

Оптимизирован для запуска на смартфоне (Termux / a-Shell) и на десктопе:
- Ноль сторонних библиотек (только стандартная библиотека Python 3.10+): не нужно
  компилировать тяжёлые SDK на телефоне.
- Полная поддержка инструментов (Function Calling): чтение, поиск, точечные правки
  с цветным unified diff, создание файлов и запуск команд в shell.
- Безопасность по умолчанию: изоляция файловых операций внутри рабочей папки
  и подтверждение пользователя (Human-in-the-Loop) перед правками и запуском команд.
- Корректная работа с Gemini 3 / Gemini 4: сохранение thoughtSignature и call id,
  откат истории при прерывании (Ctrl+C), экспоненциальные ретраи при 429/5xx.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import difflib
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Final
import urllib.error
import urllib.request

from safety import detect_destructive_command

# Подключаем readline для удобной истории ввода и стрелок в терминале, если доступен
try:
    import readline  # noqa: F401
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Константы и настройки по умолчанию
# ---------------------------------------------------------------------------

API_BASE_URL: Final[str] = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL: Final[str] = "gemini-4-hydrogen"
DEFAULT_MAX_STEPS: Final[int] = 15
DEFAULT_HTTP_TIMEOUT: Final[int] = 120
DEFAULT_CMD_TIMEOUT: Final[int] = 30
MAX_TOOL_OUTPUT_CHARS: Final[int] = 16_000
MAX_READ_LINES: Final[int] = 300

IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
)

SYSTEM_PROMPT: Final[str] = """Ты — GemCode, лаконичный и опытный консольный ИИ-агент для разработки (в стиле Claude Code).
Пользователь запускает тебя в терминале (часто с экрана смартфона), поэтому отвечай чётко, структурно и по делу.

Правила работы:
1. Сначала изучи проект инструментами (`list_files`, `search_text`, `read_file`), не угадывай содержимое файлов.
2. Для изменения существующего файла предпочитай `edit_file` с точным уникальным фрагментом `old_text` вместо полной перезаписи через `write_file`.
3. Перед правкой читай нужный участок файла, чтобы не ошибиться с отступами и контекстом.
4. После внесения правок при необходимости предложи или выполни проверку (`run_command` — запуск скрипта, тестов, линтера).
5. Если пользователь отклонил действие, учти его причину и скорректируй план.
6. Отвечай на языке пользователя (обычно на русском), избегай лишней «воды».
"""


# ---------------------------------------------------------------------------
# Терминальный UI (цвета ANSI, компактный вывод для телефона, спиннер)
# ---------------------------------------------------------------------------


class Theme:
    """Набор ANSI-цветов с автоматическим отключением при отсутствии TTY или NO_COLOR."""

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            no_color = os.environ.get("NO_COLOR") is not None
            enabled = sys.stdout.isatty() and not no_color
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def magenta(self, text: str) -> str:
        return self._wrap("35", text)


class Spinner:
    """Неблокирующий индикатор ожидания ответа API для терминала."""

    _FRAMES: Final[tuple[str, ...]] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, message: str, theme: Theme) -> None:
        self._message = message
        self._theme = theme
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active = sys.stderr.isatty()

    def __enter__(self) -> Spinner:
        if not self._active:
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self._active:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # Очищаем строку спиннера
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def _run(self) -> None:
        idx = 0
        while not self._stop.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            line = self._theme.dim(f"{frame} {self._message}")
            sys.stderr.write(f"\r\033[K{line}")
            sys.stderr.flush()
            idx += 1
            self._stop.wait(0.09)


def terminal_width() -> int:
    """Возвращает безопасную ширину разделителя (удобно на узком экране смартфона)."""
    cols = shutil.get_terminal_size(fallback=(60, 24)).columns
    return max(32, min(cols, 76))


def truncate_text(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Обрезает слишком длинный вывод инструмента, чтобы не переполнить контекст."""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n...[вывод обрезан: скрыто ещё {omitted} символов]"


def is_binary_file(path: Path) -> bool:
    """Быстрая эвристика определения бинарного файла по нулевым байтам."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(4096)
        return b"\x00" in chunk
    except OSError:
        return False


def format_colored_diff(old_text: str, new_text: str, filename: str, theme: Theme) -> str:
    """Строит компактный цветной unified diff для превью перед сохранением файла."""
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            n=2,
        )
    )
    if not diff_lines:
        return theme.dim("(без изменений)")

    rendered: list[str] = []
    max_preview_lines = 80
    for line in diff_lines[:max_preview_lines]:
        clean = line.rstrip("\r\n")
        if clean.startswith("+++") or clean.startswith("---"):
            rendered.append(theme.bold(clean))
        elif clean.startswith("@@"):
            rendered.append(theme.cyan(clean))
        elif clean.startswith("+"):
            rendered.append(theme.green(clean))
        elif clean.startswith("-"):
            rendered.append(theme.red(clean))
        else:
            rendered.append(theme.dim(clean))

    if len(diff_lines) > max_preview_lines:
        rendered.append(
            theme.yellow(f"... ещё {len(diff_lines) - max_preview_lines} строк diff скрыто")
        )
    return "\n".join(rendered)


# ---------------------------------------------------------------------------
# Клиент Google AI Studio (Gemini REST API, zero-dependency)
# ---------------------------------------------------------------------------


class GeminiApiError(Exception):
    """Ошибка взаимодействия с Gemini REST API."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class TokenUsage:
    """Накопительная статистика расхода токенов за сессию."""

    prompt_tokens: int = 0
    candidate_tokens: int = 0
    total_tokens: int = 0

    def add(self, metadata: dict[str, Any] | None) -> None:
        if not metadata:
            return
        self.prompt_tokens += int(metadata.get("promptTokenCount", 0))
        self.candidate_tokens += int(metadata.get("candidatesTokenCount", 0))
        self.total_tokens += int(metadata.get("totalTokenCount", 0))


class GeminiRestClient:
    """
    Минималистичный HTTP-клиент для Gemini GenerateContent REST API.
    Передаёт ключ через заголовок x-goog-api-key и поддерживает ретраи при 429/5xx.
    """

    _RETRYABLE_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_HTTP_TIMEOUT,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def generate(
        self,
        *,
        contents: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Отправляет запрос generateContent и возвращает распарсенный JSON-ответ."""
        url = f"{API_BASE_URL}/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "tools": tools,
            "generationConfig": {"temperature": temperature},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(
                url=url,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "x-goog-api-key": self.api_key,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                msg = self._extract_api_error_message(err_body) or f"HTTP {exc.code}"
                if exc.code in self._RETRYABLE_CODES and attempt < self.max_retries:
                    delay = min(16.0, 1.5 * (2**attempt))
                    time.sleep(delay)
                    last_error = GeminiApiError(f"HTTP {exc.code}: {msg}", exc.code)
                    continue
                raise GeminiApiError(f"HTTP {exc.code}: {msg}", exc.code) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    delay = min(10.0, 1.5 * (2**attempt))
                    time.sleep(delay)
                    last_error = exc
                    continue
                raise GeminiApiError(f"Сетевая ошибка: {exc}") from exc

        raise GeminiApiError(f"Не удалось выполнить запрос: {last_error}")

    @staticmethod
    def _extract_api_error_message(raw_body: str) -> str:
        try:
            data = json.loads(raw_body)
            error = data.get("error", {})
            if isinstance(error, dict) and "message" in error:
                return str(error["message"])
        except json.JSONDecodeError:
            pass
        return raw_body[:300].strip()


# ---------------------------------------------------------------------------
# Инструменты агента (безопасная песочница + Human-in-the-Loop)
# ---------------------------------------------------------------------------


@dataclass
class ApprovalPolicy:
    """Политика подтверждения потенциально опасных операций."""

    auto_approve_edits: bool = False
    auto_approve_commands: bool = False
    non_interactive: bool = False


class WorkspaceToolkit:
    """
    Набор локальных инструментов для агента, ограниченных рабочей директорией.
    Поддерживает: list_files, read_file, search_text, edit_file, write_file, run_command.
    """

    def __init__(
        self,
        workspace: Path,
        theme: Theme,
        policy: ApprovalPolicy,
    ) -> None:
        self.workspace = workspace.resolve()
        self.theme = theme
        self.policy = policy
        self._dispatch: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "list_files": self._tool_list_files,
            "read_file": self._tool_read_file,
            "search_text": self._tool_search_text,
            "edit_file": self._tool_edit_file,
            "write_file": self._tool_write_file,
            "run_command": self._tool_run_command,
        }

    def declarations(self) -> list[dict[str, Any]]:
        """Возвращает OpenAPI-схему инструментов в формате Gemini API."""
        return [
            {
                "functionDeclarations": [
                    {
                        "name": "list_files",
                        "description": (
                            "Возвращает список файлов и папок внутри рабочей директории "
                            "(автоматически пропускает .git, __pycache__, node_modules, .venv)."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Относительный путь к папке (по умолчанию '.').",
                                },
                                "max_depth": {
                                    "type": "integer",
                                    "description": "Максимальная глубина обхода (по умолчанию 3, макс. 6).",
                                },
                            },
                        },
                    },
                    {
                        "name": "read_file",
                        "description": (
                            "Читает текстовый файл с нумерацией строк. "
                            "Для больших файлов указывай диапазон start_line..end_line."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Относительный путь к файлу.",
                                },
                                "start_line": {
                                    "type": "integer",
                                    "description": "Начальная строка (1-based, по умолчанию 1).",
                                },
                                "end_line": {
                                    "type": "integer",
                                    "description": "Конечная строка включительно (по умолчанию start_line + 199).",
                                },
                            },
                            "required": ["path"],
                        },
                    },
                    {
                        "name": "search_text",
                        "description": (
                            "Ищет подстроку или регулярное выражение по текстовым файлам проекта."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "Текст или regex-паттерн для поиска.",
                                },
                                "path": {
                                    "type": "string",
                                    "description": "Папка или файл для поиска (по умолчанию '.').",
                                },
                                "is_regex": {
                                    "type": "boolean",
                                    "description": "Интерпретировать query как регулярное выражение (по умолчанию false).",
                                },
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "edit_file",
                        "description": (
                            "Точечно заменяет уникальный фрагмент old_text на new_text в файле. "
                            "Перед записью показывает пользователю diff и запрашивает подтверждение."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Относительный путь к существующему файлу.",
                                },
                                "old_text": {
                                    "type": "string",
                                    "description": "Точный фрагмент текста для замены (должен встречаться ровно 1 раз).",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "Новый текст вместо old_text.",
                                },
                            },
                            "required": ["path", "old_text", "new_text"],
                        },
                    },
                    {
                        "name": "write_file",
                        "description": (
                            "Создаёт новый файл или полностью перезаписывает существующий. "
                            "Требует подтверждения пользователя."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Относительный путь к файлу.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "Полное содержимое файла.",
                                },
                            },
                            "required": ["path", "content"],
                        },
                    },
                    {
                        "name": "run_command",
                        "description": (
                            "Выполняет консольную команду в рабочей директории (например, python3, pytest, git). "
                            "Требует подтверждения пользователя."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "Команда для запуска в shell.",
                                },
                                "timeout": {
                                    "type": "integer",
                                    "description": "Таймаут выполнения в секундах (по умолчанию 30, макс. 120).",
                                },
                            },
                            "required": ["command"],
                        },
                    },
                ]
            }
        ]

    def execute(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        """Безопасно вызывает инструмент по имени и гарантирует словарь-результат."""
        handler = self._dispatch.get(name)
        if handler is None:
            return {"error": f"Неизвестный инструмент: {name}"}

        safe_args = args if isinstance(args, dict) else {}
        try:
            return handler(safe_args)
        except ValueError as exc:
            return {"error": str(exc)}
        except OSError as exc:
            return {"error": f"Ошибка файловой системы: {exc}"}
        except Exception as exc:
            return {"error": f"Непредвиденная ошибка инструмента {name}: {exc}"}

    # --- Вспомогательные методы безопасности ---

    def _resolve_safe_path(self, raw_path: str) -> Path:
        """Разрешает путь и гарантирует, что он остаётся внутри рабочей директории."""
        candidate = (self.workspace / raw_path).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError(
                f"Доступ запрещён: путь '{raw_path}' выходит за пределы рабочей папки {self.workspace}"
            )
        return candidate

    def _relative_label(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)

    def _confirm(self, prompt_label: str, *, kind: str) -> tuple[bool, str]:
        """
        Запрашивает подтверждение у пользователя.
        Возвращает (approved, optional_feedback).
        """
        if kind == "edit" and self.policy.auto_approve_edits:
            return True, ""
        if kind == "command" and self.policy.auto_approve_commands:
            return True, ""

        if self.policy.non_interactive:
            return False, "Отклонено: неинтерактивный режим без --yolo."

        options = "[y] Да / [n] Нет / [a] Всегда (правки)" if kind == "edit" else "[y] Да / [n] Нет / [a] Всегда (команды)"
        print(self.theme.yellow(f"{prompt_label}\n  {options}"))
        try:
            answer = input(self.theme.bold("  Выбор > ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False, "Отменено пользователем."

        if answer in ("y", "yes", "д", "да"):
            return True, ""
        if answer in ("a", "always", "в", "всегда"):
            if kind == "edit":
                self.policy.auto_approve_edits = True
            else:
                self.policy.auto_approve_commands = True
            print(self.theme.dim("  -> Автоподтверждение включено для текущей сессии."))
            return True, ""
        return False, f"Пользователь отклонил действие (ответ: '{answer or 'n'}')."

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        """Атомарно записывает файл (через временный файл в той же директории)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target)
        except OSError:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            # Запасной вариант для ограниченных ФС смартфонов (например, SD/SAF)
            target.write_text(content, encoding="utf-8")

    # --- Реализация инструментов ---

    def _tool_list_files(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", ".") or ".")
        max_depth = max(1, min(int(args.get("max_depth", 3)), 6))
        root = self._resolve_safe_path(rel)

        if not root.exists():
            return {"error": f"Путь не найден: {rel}"}
        if root.is_file():
            return {"files": [self._relative_label(root)]}

        entries: list[str] = []
        max_items = 250
        truncated = False

        # Итеративный обход с контролем глубины
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                children = sorted(
                    os.scandir(current),
                    key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
                )
            except OSError:
                continue

            # Для корректного алфавитного порядка при pop() добавляем поддиректории в обратном порядке
            subdirs: list[tuple[Path, int]] = []
            for entry in children:
                if entry.name in IGNORED_DIRS:
                    continue
                item_path = Path(entry.path)
                rel_name = self._relative_label(item_path)
                is_dir = entry.is_dir(follow_symlinks=False)
                entries.append(f"{rel_name}/" if is_dir else rel_name)

                if len(entries) >= max_items:
                    truncated = True
                    break

                if is_dir and depth + 1 < max_depth:
                    subdirs.append((item_path, depth + 1))

            if truncated:
                break
            for subdir in reversed(subdirs):
                stack.append(subdir)

        return {
            "root": self._relative_label(root),
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        }

    def _tool_read_file(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", ""))
        if not rel:
            return {"error": "Не указан параметр path."}

        target = self._resolve_safe_path(rel)
        if not target.exists():
            return {"error": f"Файл не найден: {rel}"}
        if not target.is_file():
            return {"error": f"Путь является директорией, а не файлом: {rel}"}
        if is_binary_file(target):
            return {"error": f"Файл похож на бинарный и не может быть прочитан как текст: {rel}"}

        start_line = max(1, int(args.get("start_line", 1)))
        requested_end = args.get("end_line")
        end_line = (
            int(requested_end)
            if requested_end is not None
            else start_line + 199
        )
        if end_line < start_line:
            end_line = start_line
        if end_line - start_line + 1 > MAX_READ_LINES:
            end_line = start_line + MAX_READ_LINES - 1

        raw_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(raw_lines)
        selected = raw_lines[start_line - 1 : end_line]
        numbered = [
            f"{idx:4d} | {line}"
            for idx, line in enumerate(selected, start=start_line)
        ]

        return {
            "path": self._relative_label(target),
            "total_lines": total_lines,
            "shown_range": f"{start_line}-{min(end_line, total_lines)}",
            "content": truncate_text("\n".join(numbered)),
        }

    def _tool_search_text(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", ""))
        if not query:
            return {"error": "Параметр query не может быть пустым."}

        rel = str(args.get("path", ".") or ".")
        is_regex = bool(args.get("is_regex", False))
        root = self._resolve_safe_path(rel)
        if not root.exists():
            return {"error": f"Путь не найден: {rel}"}

        try:
            pattern = re.compile(query if is_regex else re.escape(query), re.IGNORECASE)
        except re.error as exc:
            return {"error": f"Некорректное регулярное выражение: {exc}"}

        files_to_search: list[Path] = []
        if root.is_file():
            files_to_search.append(root)
        else:
            stack = [root]
            while stack:
                cur = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for entry in it:
                            if entry.name in IGNORED_DIRS:
                                continue
                            p = Path(entry.path)
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(p)
                            elif entry.is_file(follow_symlinks=False):
                                files_to_search.append(p)
                except OSError:
                    continue

        matches: list[str] = []
        max_matches = 60
        for file_path in sorted(files_to_search):
            if len(matches) >= max_matches:
                break
            try:
                if file_path.stat().st_size > 1_000_000 or is_binary_file(file_path):
                    continue
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            rel_file = self._relative_label(file_path)
            for line_no, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append(f"{rel_file}:{line_no}: {line.strip()[:160]}")
                    if len(matches) >= max_matches:
                        break

        return {
            "query": query,
            "match_count": len(matches),
            "truncated": len(matches) >= max_matches,
            "matches": matches,
        }

    def _tool_edit_file(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", ""))
        old_text = str(args.get("old_text", ""))
        new_text = str(args.get("new_text", ""))

        if not rel or not old_text:
            return {"error": "Параметры path и old_text обязательны."}

        target = self._resolve_safe_path(rel)
        if not target.exists() or not target.is_file():
            return {"error": f"Файл не найден: {rel}"}

        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old_text)
        if occurrences == 0:
            return {
                "error": (
                    f"Фрагмент old_text не найден в {rel}. "
                    "Прочитай файл через read_file и проверь точность отступов."
                )
            }
        if occurrences > 1:
            return {
                "error": (
                    f"Фрагмент old_text встречается {occurrences} раз(а) в {rel}. "
                    "Передай больше контекстных строк, чтобы совпадение стало уникальным."
                )
            }

        updated = original.replace(old_text, new_text, 1)
        rel_label = self._relative_label(target)

        print(self.theme.dim("-" * terminal_width()))
        print(self.theme.bold(f"Предлагаемая правка: {rel_label}"))
        print(format_colored_diff(original, updated, rel_label, self.theme))
        print(self.theme.dim("-" * terminal_width()))

        approved, feedback = self._confirm(f"Применить правку в {rel_label}?", kind="edit")
        if not approved:
            return {"status": "rejected", "reason": feedback}

        self._atomic_write_text(target, updated)
        print(self.theme.green(f"  ✓ Файл обновлён: {rel_label}"))
        return {"status": "ok", "path": rel_label}

    def _tool_write_file(self, args: dict[str, Any]) -> dict[str, Any]:
        rel = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if not rel:
            return {"error": "Параметр path обязателен."}

        target = self._resolve_safe_path(rel)
        rel_label = self._relative_label(target)
        existed = target.exists() and target.is_file()
        old_content = target.read_text(encoding="utf-8", errors="replace") if existed else ""

        print(self.theme.dim("-" * terminal_width()))
        action = "Перезапись файла" if existed else "Создание нового файла"
        print(self.theme.bold(f"{action}: {rel_label} ({len(content.splitlines())} строк)"))
        print(format_colored_diff(old_content, content, rel_label, self.theme))
        print(self.theme.dim("-" * terminal_width()))

        approved, feedback = self._confirm(f"Сохранить файл {rel_label}?", kind="edit")
        if not approved:
            return {"status": "rejected", "reason": feedback}

        self._atomic_write_text(target, content)
        print(self.theme.green(f"  ✓ Файл сохранён: {rel_label}"))
        return {"status": "ok", "path": rel_label, "bytes_written": len(content.encode("utf-8"))}

    def _tool_run_command(self, args: dict[str, Any]) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        if not command:
            return {"error": "Параметр command не может быть пустым."}

        timeout = max(1, min(int(args.get("timeout", DEFAULT_CMD_TIMEOUT)), 120))

        blocked = detect_destructive_command(command)
        if blocked:
            return {"error": f"Команда отклонена политикой безопасности: {blocked}"}

        print(self.theme.dim("-" * terminal_width()))
        print(self.theme.bold("Запуск команды:"))
        print(f"  {self.theme.magenta('$')} {self.theme.cyan(command)}")
        print(self.theme.dim("-" * terminal_width()))

        approved, feedback = self._confirm("Выполнить эту команду?", kind="command")
        if not approved:
            return {"status": "rejected", "reason": feedback}

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"Команда превысила таймаут {timeout} с."}

        stdout = truncate_text(proc.stdout.strip(), MAX_TOOL_OUTPUT_CHARS // 2)
        stderr = truncate_text(proc.stderr.strip(), MAX_TOOL_OUTPUT_CHARS // 2)

        status_badge = (
            self.theme.green(f"exit {proc.returncode}")
            if proc.returncode == 0
            else self.theme.red(f"exit {proc.returncode}")
        )
        print(f"  -> Завершено ({status_badge})")
        if stdout:
            preview = stdout if len(stdout) <= 400 else f"{stdout[:400]}..."
            print(self.theme.dim(preview))
        if stderr:
            preview_err = stderr if len(stderr) <= 400 else f"{stderr[:400]}..."
            print(self.theme.red(preview_err))

        return {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }


# ---------------------------------------------------------------------------
# Ядро цикла агента (Agentic Loop)
# ---------------------------------------------------------------------------


@dataclass
class CodingAgent:
    """Управляет историей диалога и циклом «мысль -> инструмент -> наблюдение»."""

    client: GeminiRestClient
    toolkit: WorkspaceToolkit
    theme: Theme
    max_steps: int = DEFAULT_MAX_STEPS
    history: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)

    def clear_history(self) -> None:
        """Очищает контекст диалога."""
        self.history.clear()

    def ask(self, user_prompt: str) -> str:
        """
        Выполняет один пользовательский запрос (может включать несколько шагов
        вызова инструментов до получения финального текстового ответа).
        """
        # Снимок длины истории: при Ctrl+C откатываем незавершённый шаг,
        # чтобы история оставалась консистентной (без «висящих» functionCall).
        rollback_len = len(self.history)
        self.history.append({"role": "user", "parts": [{"text": user_prompt}]})
        tools_schema = self.toolkit.declarations()

        try:
            for step in range(1, self.max_steps + 1):
                with Spinner(f"Думаю ({self.client.model}, шаг {step})...", self.theme):
                    response = self.client.generate(
                        contents=self.history,
                        tools=tools_schema,
                        system_instruction=SYSTEM_PROMPT,
                    )

                self.usage.add(response.get("usageMetadata"))

                candidates = response.get("candidates")
                if not candidates:
                    block_reason = response.get("promptFeedback", {}).get("blockReason")
                    raise GeminiApiError(f"Модель не вернула ответ (blockReason={block_reason}).")

                model_content = candidates[0].get("content")
                if not isinstance(model_content, dict):
                    raise GeminiApiError("Некорректная структура ответа API: отсутствует content.")

                # КРИТИЧНО для Gemini 3 / Gemini 4:
                # Сохраняем оригинальный content целиком без пересборки parts,
                # чтобы не потерять thoughtSignature и внутренний порядок частей!
                if "role" not in model_content:
                    model_content = {**model_content, "role": "model"}
                self.history.append(model_content)

                parts = model_content.get("parts") or []
                visible_texts: list[str] = []
                function_calls: list[dict[str, Any]] = []

                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    # Пропускаем скрытые блоки рассуждений в выводе, но оставляем их в history
                    if part.get("thought") is True:
                        continue
                    if "text" in part and part["text"]:
                        visible_texts.append(str(part["text"]))
                    if "functionCall" in part and isinstance(part["functionCall"], dict):
                        function_calls.append(part["functionCall"])

                # Если вызовов инструментов нет — это финальный ответ пользователю
                if not function_calls:
                    return "\n".join(visible_texts).strip()

                # Если модель сопроводила вызов инструмента комментарием — покажем его
                if visible_texts:
                    print(self.theme.dim("\n".join(visible_texts).strip()))

                # Выполняем все запрошенные инструменты (включая параллельные вызовы)
                response_parts: list[dict[str, Any]] = []
                for call in function_calls:
                    fn_name = str(call.get("name", ""))
                    fn_args = call.get("args") or {}
                    call_id = call.get("id")

                    self._print_tool_badge(fn_name, fn_args)
                    result = self.toolkit.execute(fn_name, fn_args)

                    fn_response_obj: dict[str, Any] = {
                        "name": fn_name,
                        "response": result,
                    }
                    # Прокидываем call_id обратно, если API его вернул
                    if call_id:
                        fn_response_obj["id"] = call_id

                    response_parts.append({"functionResponse": fn_response_obj})

                self.history.append({"role": "user", "parts": response_parts})

            return (
                f"Достигнут лимит шагов ({self.max_steps}). "
                "Напишите «продолжай», если нужно завершить задачу."
            )
        except KeyboardInterrupt:
            del self.history[rollback_len:]
            raise
        except GeminiApiError:
            del self.history[rollback_len:]
            raise

    def _print_tool_badge(self, name: str, args: dict[str, Any]) -> None:
        """Компактно отображает вызываемый инструмент на экране телефона."""
        preview_items: list[str] = []
        for k, v in args.items():
            val_str = str(v).replace("\n", "\\n")
            if len(val_str) > 40:
                val_str = f"{val_str[:37]}..."
            preview_items.append(f"{k}={val_str}")
        args_str = ", ".join(preview_items)
        print(self.theme.cyan(f"⚙ {name}") + self.theme.dim(f"({args_str})"))


# ---------------------------------------------------------------------------
# Интерактивный REPL и CLI
# ---------------------------------------------------------------------------


def resolve_api_key(cli_key: str | None, non_interactive: bool) -> str:
    """Получает ключ Google AI Studio из аргумента, переменных окружения или ввода."""
    key = (
        cli_key
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
    )
    if key and key.strip():
        return key.strip()

    if non_interactive:
        raise SystemExit(
            "Ошибка: не найден API-ключ. Задайте переменную GEMINI_API_KEY или флаг --api-key."
        )

    print("Переменная GEMINI_API_KEY не задана.")
    try:
        entered = getpass.getpass("Вставьте ключ Google AI Studio API (ввод скрыт): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nВыход.")
    if not entered:
        raise SystemExit("API-ключ не указан.")
    return entered


def print_banner(agent: CodingAgent) -> None:
    """Компактная шапка при запуске в терминале."""
    t = agent.theme
    sep = t.dim("=" * terminal_width())
    print(sep)
    print(t.bold(t.cyan("GemCode")) + t.dim(" — мобильный ИИ-агент (Google AI Studio)"))
    print(f"Модель : {t.green(agent.client.model)}")
    print(f"Папка  : {t.dim(str(agent.toolkit.workspace))}")
    print(t.dim("Команды: /help  /model  /auto  /tokens  /clear  /exit"))
    print(sep)


def handle_slash_command(cmd_line: str, agent: CodingAgent) -> bool:
    """Обрабатывает служебные команды REPL. Возвращает False для выхода."""
    t = agent.theme
    parts = cmd_line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit", "/q"):
        return False

    if cmd == "/help":
        print(
            "\n".join(
                [
                    t.bold("Доступные команды:"),
                    "  /help           — показать эту справку",
                    "  /model [имя]    — показать или сменить модель",
                    "                    (напр.: gemini-4-hydrogen, gemini-4-argon,",
                    "                     gemini-3.8-flash, gemini-3.5-flash-lite)",
                    "  /auto           — вкл/выкл автоподтверждение правок файлов",
                    "  /tokens         — расход токенов в текущей сессии",
                    "  /clear          — очистить историю диалога",
                    "  /exit           — выйти из агента",
                ]
            )
        )
        return True

    if cmd == "/clear":
        agent.clear_history()
        print(t.green("История диалога очищена."))
        return True

    if cmd == "/tokens":
        u = agent.usage
        print(
            f"Токены сессии: ввод={u.prompt_tokens}, "
            f"вывод={u.candidate_tokens}, всего={u.total_tokens}"
        )
        return True

    if cmd == "/auto":
        policy = agent.toolkit.policy
        policy.auto_approve_edits = not policy.auto_approve_edits
        state = t.green("ВКЛ") if policy.auto_approve_edits else t.yellow("ВЫКЛ")
        print(f"Автоподтверждение правок файлов: {state}")
        return True

    if cmd == "/model":
        if not arg:
            print(f"Текущая модель: {t.green(agent.client.model)}")
        else:
            agent.client.model = arg
            print(f"Модель переключена на: {t.green(arg)}")
            if agent.history:
                print(t.dim("Совет: выполните /clear, если хотите начать контекст с чистого листа."))
        return True

    print(t.yellow(f"Неизвестная команда: {cmd}. Введите /help для списка команд."))
    return True


def build_parser() -> argparse.ArgumentParser:
    """Создаёт CLI-парсер аргументов запуска."""
    parser = argparse.ArgumentParser(
        prog="gemcode",
        description="Консольный ИИ-агент для кода (в стиле Claude Code) на базе Google AI Studio API.",
    )
    parser.add_argument(
        "-d",
        "--dir",
        type=Path,
        default=Path.cwd(),
        help="Рабочая директория проекта (по умолчанию: текущая папка).",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
        help=(
            f"ID модели Gemini (по умолчанию: {DEFAULT_MODEL}; "
            "также: gemini-4-argon, gemini-3.8-flash, gemini-3.5-flash-lite)."
        ),
    )
    parser.add_argument(
        "-p",
        "--prompt",
        default=None,
        help="Одиночный запрос без запуска интерактивного чата.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Ключ Google AI Studio (безопаснее задавать через переменную GEMINI_API_KEY).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=DEFAULT_MAX_STEPS,
        help=f"Максимум шагов вызова инструментов на один запрос (по умолчанию: {DEFAULT_MAX_STEPS}).",
    )
    parser.add_argument(
        "--yolo",
        action="store_true",
        help="Автоматически подтверждать правки файлов и команды (используйте осторожно!).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = args.dir.resolve()
    if not workspace.exists() or not workspace.is_dir():
        print(f"Ошибка: директория не существует: {workspace}", file=sys.stderr)
        return 1

    non_interactive = args.prompt is not None
    api_key = resolve_api_key(args.api_key, non_interactive=non_interactive)

    theme = Theme()
    policy = ApprovalPolicy(
        auto_approve_edits=args.yolo,
        auto_approve_commands=args.yolo,
        non_interactive=non_interactive,
    )
    client = GeminiRestClient(api_key=api_key, model=args.model)
    toolkit = WorkspaceToolkit(workspace=workspace, theme=theme, policy=policy)
    agent = CodingAgent(
        client=client,
        toolkit=toolkit,
        theme=theme,
        max_steps=max(1, args.max_steps),
    )

    # Одноразовый режим: python3 gemcode.py -p "объясни проект"
    if args.prompt is not None:
        try:
            answer = agent.ask(args.prompt)
            if answer:
                print(answer)
            return 0
        except GeminiApiError as exc:
            print(theme.red(f"Ошибка API: {exc}"), file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print(theme.yellow("\nПрервано пользователем."), file=sys.stderr)
            return 130

    # Интерактивный REPL-режим
    print_banner(agent)
    while True:
        try:
            user_input = input(f"\n{theme.bold(theme.green('Вы > '))}").strip()
        except EOFError:
            print("\nДо встречи!")
            return 0
        except KeyboardInterrupt:
            print(theme.dim("\n(Для выхода введите /exit или нажмите Ctrl+D)"))
            continue

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_slash_command(user_input, agent):
                print("До встречи!")
                return 0
            continue

        try:
            answer = agent.ask(user_input)
            if answer:
                print(f"\n{theme.bold(theme.cyan('GemCode >'))}\n{answer}")
        except KeyboardInterrupt:
            print(theme.yellow("\nЗапрос прерван (история возвращена в консистентное состояние)."))
        except GeminiApiError as exc:
            print(theme.red(f"\nОшибка API: {exc}"))


if __name__ == "__main__":
    sys.exit(main())
