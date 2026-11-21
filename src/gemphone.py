#!/usr/bin/env python3
"""
GemPhone 3.8.1 — мультипровайдерный ИИ-агент для полноценного управления
Android-смартфоном из Termux (AccessibilityService Bridge + ADB + UI Automator +
Vision + Termux:API + Код) с интерфейсом в стиле Claude Code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
import base64
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import difflib
import getpass
from html import escape as html_escape
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
try:
    import termios
    import tty

    HAS_TERMIOS = True
except ImportError:
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]
    HAS_TERMIOS = False
import threading
import time
from typing import Any, Final, Literal
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Опциональные библиотеки (Termux: pip / pkg)
# ---------------------------------------------------------------------------

try:
    import httpx  # type: ignore[import-not-found]

    HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HAS_HTTPX = False

try:
    from PIL import Image, ImageEnhance  # type: ignore[import-not-found]

    HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageEnhance = None  # type: ignore[assignment]
    HAS_PIL = False

try:
    from prompt_toolkit import PromptSession  # type: ignore[import-not-found]
    from prompt_toolkit.completion import Completer, Completion  # type: ignore[import-not-found]
    from prompt_toolkit.formatted_text import HTML  # type: ignore[import-not-found]
    from prompt_toolkit.history import FileHistory  # type: ignore[import-not-found]
    from prompt_toolkit.styles import Style as PTStyle  # type: ignore[import-not-found]

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    PromptSession = None  # type: ignore[assignment]
    Completer = object  # type: ignore[assignment,misc]
    Completion = None  # type: ignore[assignment]
    HTML = None  # type: ignore[assignment]
    FileHistory = None  # type: ignore[assignment]
    PTStyle = None  # type: ignore[assignment]
    HAS_PROMPT_TOOLKIT = False

try:
    from rich.console import Console  # type: ignore[import-not-found]
    from rich.live import Live  # type: ignore[import-not-found]
    from rich.markdown import Markdown  # type: ignore[import-not-found]
    from rich.markup import escape as rich_escape  # type: ignore[import-not-found]
    from rich.panel import Panel  # type: ignore[import-not-found]
    from rich.table import Table  # type: ignore[import-not-found]
    from rich.text import Text  # type: ignore[import-not-found]

    HAS_RICH = True
except ImportError:
    Console = None  # type: ignore[assignment]
    Live = None  # type: ignore[assignment]
    Markdown = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    Text = None  # type: ignore[assignment]
    HAS_RICH = False

    def rich_escape(text: str) -> str:
        return text


try:
    import readline  # noqa: F401
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Константы, палитра Claude Code, лимиты и пресеты провайдеров
# ---------------------------------------------------------------------------

APP_VERSION: Final[str] = "3.9.0"
ACCENT: Final[str] = "#D97757"  # Фирменный тёплый терракотовый акцент в стиле Claude Code

APP_DIR: Final[Path] = Path.home() / ".gemphone"
CONFIG_PATH: Final[Path] = APP_DIR / "config.json"
LEGACY_GEMCODE_CONFIG: Final[Path] = Path.home() / ".gemcode.json"
MEMORY_PATH: Final[Path] = APP_DIR / "memory.json"
SESSIONS_DIR: Final[Path] = APP_DIR / "sessions"
HISTORY_PATH: Final[Path] = APP_DIR / "prompt_history.txt"
LAST_SCREENSHOT_PATH: Final[Path] = APP_DIR / "last_screen.jpg"
CANCEL_FLAG_PATH: Final[Path] = APP_DIR / "cancel.flag"
BRIDGE_TOKEN_PATH: Final[Path] = APP_DIR / "bridge.token"

BRIDGE_DEFAULT_HOST: Final[str] = "127.0.0.1"
BRIDGE_DEFAULT_PORT: Final[int] = 7331
ADB_CACHE_TTL_SEC: Final[float] = 10.0

DEFAULT_PROVIDER: Final[str] = "gemini"
DEFAULT_MAX_STEPS: Final[int] = 10
DEFAULT_TURN_TOKEN_BUDGET: Final[int] = 60_000
DEFAULT_HTTP_TIMEOUT: Final[int] = 120
DEFAULT_CMD_TIMEOUT: Final[int] = 30
MAX_TOOL_OUTPUT_CHARS: Final[int] = 5_000
STALE_TOOL_OUTPUT_CHARS: Final[int] = 400
MAX_READ_LINES: Final[int] = 150
SCREENSHOT_MAX_DIM: Final[int] = 1280
SCREENSHOT_HQ_MAX_DIM: Final[int] = 1920
SCREENSHOT_JPEG_QUALITY: Final[int] = 85
HTTP_USER_AGENT: Final[str] = f"GemPhone/{APP_VERSION} (Android; Termux)"

RiskLevel = Literal["safe", "moderate", "dangerous"]
ToolMode = Literal["all", "phone", "code", "chat"]

SLASH_COMMANDS_META: Final[list[tuple[str, str]]] = [
    ("/help", "список всех команд"),
    ("/providers", "провайдеры API и подписки"),
    ("/use", "переключить нейросеть (/use <имя>)"),
    ("/login", "войти по подписке (gemini-sub, claude-sub, chatgpt-sub)"),
    ("/model", "сменить модель (/model <id>)"),
    ("/mode", "профиль инструментов: all | phone | code | chat"),
    ("/quality", "чёткость скриншотов: eco | normal | hd"),
    ("/budget", "лимит токенов на запрос (/budget 60k)"),
    ("/steps", "лимит шагов на запрос (/steps 25)"),
    ("/bridge", "статус и токен AccessibilityService Bridge"),
    ("/adb", "мастер подключения беспроводной отладки ADB"),
    ("/screen", "показать элементы текущего экрана"),
    ("/status", "диагностика модулей и настроек"),
    ("/notify", "оповещения Android: on | off | tts | test"),
    ("/key", "задать API-ключ для провайдера"),
    ("/add", "добавить свой API или CLI-подписку"),
    ("/vision", "вкл/выкл отправку скриншотов"),
    ("/auto", "вкл/выкл автоподтверждение действий"),
    ("/memory", "долгосрочная память агента"),
    ("/save", "сохранить диалог"),
    ("/load", "загрузить диалог"),
    ("/tokens", "расход токенов за сессию"),
    ("/clear", "очистить историю диалога"),
    ("/exit", "выход из GemPhone"),
]

TOOL_DISPLAY_NAMES: Final[dict[str, str]] = {
    "adb_status": "PhoneStatus",
    "adb_ui_dump": "UIDump",
    "adb_screenshot": "Screenshot",
    "adb_tap": "Tap",
    "adb_swipe": "Swipe",
    "adb_type_text": "Type",
    "adb_keyevent": "Key",
    "adb_app_control": "App",
    "phone_sensors": "Sensors",
    "phone_controls": "Control",
    "phone_notifications": "Notify",
    "phone_clipboard": "Clipboard",
    "phone_comms": "Comms",
    "phone_media": "Media",
    "list_files": "List",
    "read_file": "Read",
    "search_text": "Search",
    "edit_file": "Edit",
    "write_file": "Write",
    "run_command": "Run",
    "web_fetch": "Fetch",
    "memory": "Memory",
}

SUBSCRIPTION_INSTALL_HINTS: Final[dict[str, tuple[str, str]]] = {
    "claude-sub": ("npm install -g @anthropic-ai/claude-code", "claude login"),
    "gemini-sub": ("npm install -g @google/gemini-cli", "gemini"),
    "chatgpt-sub": ("npm install -g @openai/codex", "codex login"),
}

PROVIDER_PRESETS: Final[dict[str, dict[str, Any]]] = {
    "gemini": {
        "type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/models",
        "api_key": "",
        "env_var": "GEMINI_API_KEY",
        "model": "gemini-3.8-flash",
        "requires_key": True,
        "supports_vision": True,
        "description": "Google AI Studio (API-ключ или OAuth токен ya29./gcloud)",
    },
    "gemini-sub": {
        "type": "cli",
        "base_url": "oauth://gemini-cli",
        "cli_command": "gemini -o json",
        "api_key": "",
        "env_var": "",
        "model": "default",
        "requires_key": False,
        "supports_vision": False,
        "description": "Подписка Google / Gemini AI Pro (OAuth через Gemini CLI)",
    },
    "claude-sub": {
        "type": "cli",
        "base_url": "oauth://claude-code",
        "cli_command": "claude -p --output-format json",
        "api_key": "",
        "env_var": "",
        "model": "default",
        "requires_key": False,
        "supports_vision": False,
        "description": "Подписка Claude Pro / Max (OAuth через Claude Code CLI)",
    },
    "chatgpt-sub": {
        "type": "cli",
        "base_url": "oauth://codex-cli",
        "cli_command": "codex exec -",
        "api_key": "",
        "env_var": "",
        "model": "default",
        "requires_key": False,
        "supports_vision": False,
        "description": "Подписка ChatGPT Plus / Pro (OAuth через OpenAI Codex CLI)",
    },
    "deepseek": {
        "type": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "env_var": "DEEPSEEK_API_KEY",
        "model": "deepseek-flash",
        "requires_key": True,
        "supports_vision": True,
        "description": "DeepSeek API (V4.1 Flash: Vision, 1M контекст)",
    },
    "openrouter": {
        "type": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
        "env_var": "OPENROUTER_API_KEY",
        "model": "nvidia/nemotron-3.5-super-vl:free",
        "requires_key": True,
        "supports_vision": True,
        "description": "OpenRouter (десятки моделей, включая :free и DeepSeek)",
    },
    "groq": {
        "type": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": "",
        "env_var": "GROQ_API_KEY",
        "model": "openai/gpt-oss-120b",
        "requires_key": True,
        "supports_vision": False,
        "description": "Groq (сверхбыстрый API)",
    },
    "openai": {
        "type": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "env_var": "OPENAI_API_KEY",
        "model": "gpt-4o",
        "requires_key": True,
        "supports_vision": True,
        "description": "OpenAI (GPT с Vision)",
    },
    "anthropic": {
        "type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
        "env_var": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "requires_key": True,
        "supports_vision": True,
        "description": "Anthropic (Claude API с Vision)",
    },
    "mistral": {
        "type": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "api_key": "",
        "env_var": "MISTRAL_API_KEY",
        "model": "mistral-large-latest",
        "requires_key": True,
        "supports_vision": True,
        "description": "Mistral AI",
    },
    "ollama": {
        "type": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "",
        "env_var": "OLLAMA_API_KEY",
        "model": "qwen2.5-coder:7b",
        "requires_key": False,
        "supports_vision": False,
        "description": "Локальная нейросеть (Ollama / llama.cpp без ключа)",
    },
}

ANDROID_KEYEVENTS: Final[dict[str, str]] = {
    "HOME": "KEYCODE_HOME",
    "BACK": "KEYCODE_BACK",
    "RECENTS": "KEYCODE_APP_SWITCH",
    "APP_SWITCH": "KEYCODE_APP_SWITCH",
    "POWER": "KEYCODE_POWER",
    "WAKEUP": "KEYCODE_WAKEUP",
    "SLEEP": "KEYCODE_SLEEP",
    "ENTER": "KEYCODE_ENTER",
    "DEL": "KEYCODE_DEL",
    "BACKSPACE": "KEYCODE_DEL",
    "TAB": "KEYCODE_TAB",
    "ESC": "KEYCODE_ESCAPE",
    "VOLUME_UP": "KEYCODE_VOLUME_UP",
    "VOLUME_DOWN": "KEYCODE_VOLUME_DOWN",
    "VOLUME_MUTE": "KEYCODE_VOLUME_MUTE",
    "MEDIA_PLAY_PAUSE": "KEYCODE_MEDIA_PLAY_PAUSE",
    "MEDIA_NEXT": "KEYCODE_MEDIA_NEXT",
    "MEDIA_PREVIOUS": "KEYCODE_MEDIA_PREVIOUS",
    "PASTE": "KEYCODE_PASTE",
    "COPY": "KEYCODE_COPY",
    "SEARCH": "KEYCODE_SEARCH",
    "NOTIFICATIONS": "KEYCODE_NOTIFICATION",
}

DANGEROUS_CMD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+(/|~|\$HOME|\*)\b"),
    re.compile(r"\b(mkfs|dd\s+if=|format\s+[a-zA-Z]:|diskpart)\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"),
    re.compile(r"\b(shutdown|reboot|poweroff|init\s+0)\b", re.IGNORECASE),
)

IGNORED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
    }
)

SYSTEM_PROMPT: Final[str] = (
    "You are GemPhone, a proactive autonomous AI agent running in Termux on the user's Android phone.\n"
    "Rules:\n"
    "1. ACT, DON'T INSTRUCT: Whenever a task can be done with your tools (writing/editing scripts, running commands, "
    "checking files, controlling apps/phone), DO IT YOURSELF immediately instead of telling the user to do it manually. "
    "Never dump code with manual steps like 'save via nano and run python' when tools are available — create the file "
    "with `write_file`, verify/test it with `run_command`, and concisely report the result.\n"
    "2. Reply concisely in the user's language (usually Russian). Do not repeat a failing action more than twice; "
    "if stuck, briefly explain why and ask.\n"
    "3. Prefer fast `phone_*` tools (Termux:API) for sensors, notifications, clipboard, SMS, calls, media.\n"
    "4. For UI control: call `adb_ui_dump` first, then `adb_tap(element_id=...)` (works via AccessibilityService Bridge "
    "without ADB or via ADB). Use `adb_screenshot` when visual inspection is needed (pass `hq=true` for fine print).\n"
    "5. Read on-screen text carefully and literally; never guess or fabricate words that are unclear.\n"
    "6. If neither AccessibilityService Bridge nor ADB is connected, tell the user to enable GemPhone Bridge in Accessibility "
    "or run `/adb`."
)


# ---------------------------------------------------------------------------
# Терминальный интерфейс в стиле Claude Code (Rich + PromptToolkit)
# ---------------------------------------------------------------------------


class _SlashCompleter(Completer):  # type: ignore[misc]
    """Автодополнение slash-команд с описаниями прямо над клавиатурой."""

    def get_completions(self, document: Any, _complete_event: Any) -> Any:
        if not HAS_PROMPT_TOOLKIT or Completion is None:
            return
        text = document.text_before_cursor.lstrip()
        if not text.startswith("/") or " " in text:
            return
        for cmd, desc in SLASH_COMMANDS_META:
            if cmd.startswith(text.lower()):
                yield Completion(cmd, start_position=-len(text), display=cmd, display_meta=desc)


class _ThinkingSpinner:
    """Живой индикатор в стиле Claude Code: ✻ Думаю… (2.4s · шаг 1/10 · esc — отмена)."""

    _GLYPHS: Final[tuple[str, ...]] = ("✻", "✽", "✶", "✢", "·", "✢", "✶", "✽")

    def __init__(self, ui: ConsoleUI, label: str, sublabel: str = "") -> None:
        self._ui = ui
        self._label = label
        self._sublabel = sublabel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0
        self._live: Any = None

    def _render_rich(self, idx: int) -> Any:
        elapsed = time.perf_counter() - self._started
        glyph = self._GLYPHS[idx % len(self._GLYPHS)]
        t = Text()
        t.append(f"{glyph} ", style=f"bold {ACCENT}")
        t.append(self._label, style="bold white")
        meta = f" ({elapsed:.1f}s"
        if self._sublabel:
            meta += f" · {self._sublabel}"
        meta += " · esc — отмена)"
        t.append(meta, style="dim")
        return t

    def __enter__(self) -> _ThinkingSpinner:
        self._started = time.perf_counter()
        if self._ui.rich_console is not None and Live is not None and Text is not None:
            self._live = Live(
                self._render_rich(0),
                console=self._ui.rich_console,
                refresh_per_second=8,
                transient=True,
            )
            self._live.__enter__()
            self._thread = threading.Thread(target=self._loop_rich, daemon=True)
            self._thread.start()
        elif sys.stderr.isatty():
            self._thread = threading.Thread(target=self._loop_ansi, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.4)
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None
        elif sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def _loop_rich(self) -> None:
        idx = 0
        while not self._stop.is_set():
            if self._live is not None:
                self._live.update(self._render_rich(idx))
            idx += 1
            self._stop.wait(0.14)

    def _loop_ansi(self) -> None:
        idx = 0
        while not self._stop.is_set():
            elapsed = time.perf_counter() - self._started
            glyph = self._GLYPHS[idx % len(self._GLYPHS)]
            sub = f" · {self._sublabel}" if self._sublabel else ""
            sys.stderr.write(f"\r\033[K\033[33m{glyph}\033[0m {self._label} \033[2m({elapsed:.1f}s{sub})\033[0m")
            sys.stderr.flush()
            idx += 1
            self._stop.wait(0.14)


class _RunningToolLine:
    """Живая строка выполняющегося инструмента в стиле Claude Code (сворачивается в ● / ⎿)."""

    _FRAMES: Final[tuple[str, ...]] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, ui: ConsoleUI, name: str, args: dict[str, Any]) -> None:
        self._ui = ui
        self._display = TOOL_DISPLAY_NAMES.get(name, name)
        self._arg_str = _format_tool_args(args)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._live: Any = None
        self._started = 0.0

    def _render(self, idx: int) -> Any:
        frame = self._FRAMES[idx % len(self._FRAMES)]
        elapsed = time.perf_counter() - self._started
        if Text is not None:
            t = Text()
            t.append(f"{frame} ", style=f"bold {ACCENT}")
            t.append(self._display, style="bold")
            t.append(f"({self._arg_str})", style="dim")
            if elapsed >= 0.4:
                t.append(f"  {elapsed:.1f}s", style="dim")
            return t
        return f"{frame} {self._display}({self._arg_str})"

    def __enter__(self) -> _RunningToolLine:
        self._started = time.perf_counter()
        if self._ui.rich_console is not None and Live is not None:
            self._live = Live(
                self._render(0),
                console=self._ui.rich_console,
                refresh_per_second=10,
                transient=True,
            )
            self._live.__enter__()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None

    def _loop(self) -> None:
        idx = 0
        while not self._stop.is_set():
            if self._live is not None:
                self._live.update(self._render(idx))
            idx += 1
            self._stop.wait(0.1)


def _format_tool_args(args: dict[str, Any]) -> str:
    items: list[str] = []
    for k, v in args.items():
        if k in ("content", "old_text", "new_text"):
            items.append(f"{k}=…({len(str(v))} симв.)")
            continue
        s = str(v).replace("\n", " ")
        if len(s) > 36:
            s = f"{s[:33]}…"
        items.append(f"{k}={s}" if len(args) > 1 else s)
    return ", ".join(items)


def _summarize_tool_result(name: str, payload: dict[str, Any], elapsed: float) -> tuple[str, str]:
    """Возвращает (статус: ok|err|warn, краткая строка-итог для ветки ⎿)."""
    if "error" in payload:
        return "err", str(payload["error"])[:110]
    if "rejected" in payload:
        return "warn", f"Отклонено: {payload['rejected']}"

    via = f" [{payload['via']}]" if payload.get("via") else ""

    if name == "read_file":
        return "ok", f"Прочитано {payload.get('range', '?')} из {payload.get('total', '?')} строк ({elapsed:.1f}s)"
    if name == "list_files":
        return "ok", f"Найдено элементов: {payload.get('count', 0)}"
    if name == "search_text":
        return "ok", f"Совпадений: {payload.get('count', 0)}"
    if name in ("edit_file", "write_file"):
        return "ok", f"Сохранено: {payload.get('path', '')}"
    if name == "run_command":
        code = payload.get("code", 0)
        lines = len((payload.get("stdout") or "").splitlines())
        st = "ok" if code == 0 else "err"
        return st, f"exit {code} · {elapsed:.1f}s (вывод: {lines} стр.)"
    if name == "adb_ui_dump":
        lines = len((payload.get("ui") or "").splitlines())
        return "ok", f"{payload.get('app', '?')} · {lines} элементов{via} ({elapsed:.1f}s)"
    if name == "adb_screenshot":
        return "ok", f"Скриншот {payload.get('dim', '?')} · {payload.get('kb', '?')} KB{via} ({elapsed:.1f}s)"
    if name == "adb_tap":
        lbl = f" «{payload['label']}»" if payload.get("label") else ""
        return "ok", f"{payload.get('action', 'tap')}{lbl} @({payload.get('x')}, {payload.get('y')}){via}"
    if name == "adb_swipe":
        return "ok", f"Свайп {payload.get('from')} → {payload.get('to')}{via}"
    if name == "adb_type_text":
        return "ok", f"Введено {payload.get('chars', 0)} симв. ({payload.get('method', '')}{via})"
    if name == "adb_keyevent":
        return "ok", f"Нажата клавиша {payload.get('keycode', '')}{via}"
    if name == "adb_app_control":
        if "packages" in payload:
            return "ok", f"Найдено приложений: {payload.get('count', 0)}"
        return "ok", f"{payload.get('package') or payload.get('url') or payload.get('app') or 'OK'}{via}"
    if name == "web_fetch":
        return "ok", f"HTTP {payload.get('status', '?')} · {len(str(payload.get('content', '')))} симв."
    if name == "memory":
        return "ok", f"Записей в памяти: {len(payload.get('memory', {}))}" if "memory" in payload else "Память обновлена"

    compact = json.dumps(payload, ensure_ascii=False)
    if len(compact) > 90:
        compact = compact[:87] + "…"
    return "ok", compact


class ConsoleUI:
    """Минималистичный UI в стиле Claude Code поверх Rich + ANSI."""

    def __init__(self) -> None:
        self.rich_console = Console() if HAS_RICH else None
        self.ansi = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def width(self) -> int:
        cols = shutil.get_terminal_size(fallback=(60, 24)).columns
        return max(34, min(cols, 84))

    def thinking(self, label: str, sublabel: str = "") -> _ThinkingSpinner:
        return _ThinkingSpinner(self, label, sublabel)

    def running_tool(self, name: str, args: dict[str, Any]) -> _RunningToolLine:
        return _RunningToolLine(self, name, args)

    def header_card(
        self,
        *,
        provider: str,
        model: str,
        workspace: str,
        transport_state: str,
        mode: str,
        tools_count: int,
    ) -> None:
        short_ws = workspace.replace(str(Path.home()), "~")
        if self.rich_console is not None and Panel is not None:
            body = (
                f"[bold {ACCENT}]✻ GemPhone {APP_VERSION}[/]  [dim]·[/]  [bold]{rich_escape(provider)}[/] / {rich_escape(model)}\n"
                f"  [dim]{rich_escape(short_ws)} · {rich_escape(transport_state)} · {mode} ({tools_count} инстр.)[/dim]\n"
                f"  [dim]/help команды · /bridge мост · esc отмена[/dim]"
            )
            self.rich_console.print(Panel(body, border_style="bright_black", padding=(0, 1)))
            return
        print(f"✻ GemPhone {APP_VERSION} · {provider}/{model} | {short_ws} | {transport_state}")

    def rule(self) -> None:
        line = "─" * self.width()
        if self.rich_console is not None:
            self.rich_console.print(f"[bright_black]{line}[/bright_black]")
        elif self.ansi:
            print(f"\033[90m{line}\033[0m")
        else:
            print(line)

    def info(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(rich_escape(text))
        else:
            print(text)

    def dim(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[dim]{rich_escape(text)}[/dim]")
        elif self.ansi:
            print(f"\033[2m{text}\033[0m")
        else:
            print(text)

    def success(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[green]{rich_escape(text)}[/green]")
        elif self.ansi:
            print(f"\033[32m{text}\033[0m")
        else:
            print(text)

    def warn(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[yellow]{rich_escape(text)}[/yellow]")
        elif self.ansi:
            print(f"\033[33m{text}\033[0m")
        else:
            print(text)

    def error(self, text: str) -> None:
        if self.rich_console is not None:
            self.rich_console.print(f"[bold red]{rich_escape(text)}[/bold red]")
        elif self.ansi:
            print(f"\033[31m{text}\033[0m")
        else:
            print(text)

    def panel(self, title: str, body: str, style: str = "bright_black") -> None:
        if self.rich_console is not None and Panel is not None:
            self.rich_console.print(
                Panel(
                    rich_escape(body),
                    title=f"[bold {ACCENT}]{rich_escape(title)}[/]",
                    title_align="left",
                    border_style=style,
                    padding=(0, 1),
                )
            )
            return
        sep = "─" * self.width()
        print(f"{sep}\n[{title}]\n{body}\n{sep}")

    def table(self, title: str, headers: list[str], rows: list[list[str]]) -> None:
        if self.rich_console is not None and Table is not None:
            tbl = Table(
                title=f"[bold]{rich_escape(title)}[/bold]",
                show_lines=False,
                border_style="bright_black",
                header_style=f"bold {ACCENT}",
            )
            for h in headers:
                tbl.add_column(rich_escape(h))
            for r in rows:
                tbl.add_row(*(rich_escape(c) for c in r))
            self.rich_console.print(tbl)
            return
        print(f"\n=== {title} ===")
        print(" | ".join(headers))
        for r in rows:
            print(" | ".join(r))

    def markdown(self, text: str) -> None:
        if self.rich_console is not None and Markdown is not None:
            self.rich_console.print()
            self.rich_console.print(Markdown(text))
            return
        print(f"\n{text}")

    def tool_tree_result(
        self,
        name: str,
        args: dict[str, Any],
        status: str,
        summary: str,
    ) -> None:
        """Древовидный лог инструмента в стиле Claude Code: ● Tool(args) / ⎿ summary."""
        display = TOOL_DISPLAY_NAMES.get(name, name)
        arg_str = _format_tool_args(args)
        dot_color = "green" if status == "ok" else ("red" if status == "err" else "yellow")
        sum_style = "dim" if status == "ok" else ("red" if status == "err" else "yellow")

        if self.rich_console is not None:
            self.rich_console.print(
                f"[{dot_color}]●[/] [bold]{rich_escape(display)}[/bold][dim]({rich_escape(arg_str)})[/dim]"
            )
            self.rich_console.print(f"  [bright_black]⎿[/]  [{sum_style}]{rich_escape(summary)}[/]")
        elif self.ansi:
            code = "32" if status == "ok" else ("31" if status == "err" else "33")
            print(f"\033[{code}m●\033[0m \033[1m{display}\033[0m\033[2m({arg_str})\033[0m")
            print(f"  \033[90m⎿\033[0m  \033[2m{summary}\033[0m")
        else:
            print(f"* {display}({arg_str})\n  |_ {summary}")

    def diff(self, old_text: str, new_text: str, filename: str) -> None:
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        added = sum(j2 - j1 for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag in ("insert", "replace"))
        removed = sum(i2 - i1 for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag in ("delete", "replace"))

        if added == 0 and removed == 0:
            self.dim("  (без изменений)")
            return

        if self.rich_console is not None:
            self.rich_console.print(
                f"  [bold]{rich_escape(filename)}[/bold]  [green]+{added}[/green] [red]-{removed}[/red]"
            )
        else:
            print(f"  {filename} (+{added} / -{removed})")

        shown = 0
        limit = 55
        for group in matcher.get_grouped_opcodes(2):
            for tag, i1, i2, j1, j2 in group:
                if shown >= limit:
                    break
                if tag == "equal":
                    for k in range(i1, i2):
                        if shown >= limit:
                            break
                        self._print_diff_line(k + 1, " ", old_lines[k])
                        shown += 1
                elif tag in ("delete", "replace"):
                    for k in range(i1, i2):
                        if shown >= limit:
                            break
                        self._print_diff_line(k + 1, "-", old_lines[k])
                        shown += 1
                    if tag == "replace":
                        for k in range(j1, j2):
                            if shown >= limit:
                                break
                            self._print_diff_line(k + 1, "+", new_lines[k])
                            shown += 1
                elif tag == "insert":
                    for k in range(j1, j2):
                        if shown >= limit:
                            break
                        self._print_diff_line(k + 1, "+", new_lines[k])
                        shown += 1
        if shown >= limit:
            self.dim("  … (остальные строки diff скрыты)")

    def _print_diff_line(self, line_no: int, sign: str, content: str) -> None:
        if self.rich_console is not None:
            esc = rich_escape(content)
            if sign == "+":
                self.rich_console.print(f"  [dim]{line_no:4d}[/dim] [green on #142f1c]+ {esc}[/]")
            elif sign == "-":
                self.rich_console.print(f"  [dim]{line_no:4d}[/dim] [red on #331616]- {esc}[/]")
            else:
                self.rich_console.print(f"  [dim]{line_no:4d}   {esc}[/dim]")
        else:
            print(f"  {line_no:4d} {sign} {content}")

    def done_banner(
        self,
        *,
        steps: int,
        tokens: int,
        elapsed: float,
        status: str = "done",
    ) -> None:
        stats = f"{steps} ш. · {format_tokens(tokens)} ток. · {elapsed:.1f}с"
        if status == "done":
            icon, title, color = "✓", "Готово", "green"
        elif status == "limit":
            icon, title, color = "⏸", "Лимит шагов", "yellow"
        else:
            icon, title, color = "⏹", "Остановлено", "red"

        if self.rich_console is not None:
            self.rich_console.print(f"\n[{color}]{icon} {title}[/] [dim]· {rich_escape(stats)}[/dim]")
        elif self.ansi:
            code = "32" if status == "done" else ("33" if status == "limit" else "31")
            print(f"\n\033[{code}m{icon} {title}\033[0m \033[2m· {stats}\033[0m")
        else:
            print(f"\n{icon} {title} · {stats}")


class CancellationMonitor:
    """Отслеживает отмену запроса по ESC / Ctrl+C в Termux или кнопке в шторке Android."""

    def __init__(self, cancel_flag: Path = CANCEL_FLAG_PATH) -> None:
        self.cancel_flag = cancel_flag
        self.event = threading.Event()
        self.reason = ""
        self._stop_watcher = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_termios: Any = None
        self._stdin_fd: int | None = None

    def cancel(self, reason: str = "Отменено пользователем") -> None:
        if not self.event.is_set():
            self.reason = reason
            self.event.set()

    def is_cancelled(self) -> bool:
        return self.event.is_set()

    def _enable_cbreak(self) -> None:
        if HAS_TERMIOS and termios is not None and tty is not None and sys.stdin.isatty():
            try:
                self._stdin_fd = sys.stdin.fileno()
                if self._old_termios is None:
                    self._old_termios = termios.tcgetattr(self._stdin_fd)
                tty.setcbreak(self._stdin_fd)
            except Exception:
                self._stdin_fd = None

    def _restore_terminal(self) -> None:
        if (
            self._stdin_fd is not None
            and self._old_termios is not None
            and HAS_TERMIOS
            and termios is not None
        ):
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSADRAIN, self._old_termios)
            except Exception:
                pass

    @contextmanager
    def suspend(self) -> Any:
        was_active = self._thread is not None and not self._paused.is_set()
        if was_active:
            self._paused.set()
            time.sleep(0.1)
            self._restore_terminal()
        try:
            yield
        finally:
            if was_active:
                self._enable_cbreak()
                self._paused.clear()

    def __enter__(self) -> CancellationMonitor:
        self.event.clear()
        self.reason = ""
        self._stop_watcher.clear()
        self._paused.clear()
        try:
            self.cancel_flag.unlink(missing_ok=True)
        except OSError:
            pass
        self._enable_cbreak()
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop_watcher.set()
        if self._thread is not None:
            self._thread.join(timeout=0.3)
        self._thread = None
        self._restore_terminal()
        self._old_termios = None
        try:
            self.cancel_flag.unlink(missing_ok=True)
        except OSError:
            pass

    def _watch_loop(self) -> None:
        while not self._stop_watcher.is_set() and not self.event.is_set():
            if self._paused.is_set():
                self._stop_watcher.wait(0.05)
                continue
            if self.cancel_flag.exists():
                self.cancel("Отменено кнопкой из шторки уведомлений")
                return
            if self._stdin_fd is not None:
                try:
                    readable, _, _ = select.select([self._stdin_fd], [], [], 0.08)
                    if readable and not self._paused.is_set():
                        ch = os.read(self._stdin_fd, 1)
                        if ch == b"\x1b":
                            more, _, _ = select.select([self._stdin_fd], [], [], 0.03)
                            if more:
                                os.read(self._stdin_fd, 8)
                            else:
                                self.cancel("Отменено клавишей ESC")
                                return
                        elif ch == b"\x03":
                            self.cancel("Отменено клавишей Ctrl+C")
                            return
                except Exception:
                    pass
            else:
                self._stop_watcher.wait(0.08)


class PhoneNotifier:
    """Фоновые оповещения Android через Termux:API (вибрация, тост, шторка, TTS)."""

    NOTIF_ID: Final[str] = "gemphone_status"

    def __init__(
        self,
        *,
        enabled: bool = True,
        vibrate: bool = True,
        toast: bool = True,
        notification: bool = True,
        tts: bool = False,
    ) -> None:
        self.enabled = enabled
        self.vibrate = vibrate
        self.toast = toast
        self.notification = notification
        self.tts = tts
        self._lock = threading.Lock()
        self._seq = 0

    def is_available(self) -> bool:
        return any(
            shutil.which(cmd) is not None
            for cmd in ("termux-vibrate", "termux-toast", "termux-notification")
        )

    def _run_cmd_async(self, cmd: list[str], *, serialized: bool = False, seq: int | None = None) -> None:
        if shutil.which(cmd[0]) is None:
            return

        def _worker() -> None:
            try:
                if serialized:
                    with self._lock:
                        if seq is not None and seq != self._seq:
                            return
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                else:
                    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _terminal_bell(self) -> None:
        if sys.stdout.isatty():
            try:
                sys.stdout.write("\a")
                sys.stdout.flush()
            except OSError:
                pass

    def notify_working(self, prompt: str) -> None:
        if not self.enabled or not self.notification:
            return
        self._seq += 1
        seq = self._seq
        snippet = prompt.replace("\n", " ").strip()[:80]
        cancel_cmd = f"mkdir -p '{APP_DIR}' && touch '{CANCEL_FLAG_PATH}'"
        self._run_cmd_async(
            [
                "termux-notification",
                "--id",
                self.NOTIF_ID,
                "--title",
                "⏳ GemPhone работает…",
                "--content",
                snippet or "Выполняю задачу…",
                "--ongoing",
                "--button1",
                "⏹ Отменить",
                "--button1-action",
                cancel_cmd,
            ],
            serialized=True,
            seq=seq,
        )

    def notify_done(self, summary: str, *, steps: int, tokens: int, elapsed: float) -> None:
        self._terminal_bell()
        if not self.enabled:
            return
        self._seq += 1
        seq = self._seq
        clean = re.sub(r"\s+", " ", summary).strip()[:140] or "Задача выполнена."
        meta = f"{steps} ш. · {format_tokens(tokens)} ток. · {elapsed:.1f}с"

        if self.vibrate:
            self._run_cmd_async(["termux-vibrate", "-d", "160"])
        if self.toast:
            self._run_cmd_async(["termux-toast", "-g", "top", f"✅ GemPhone закончил ({meta})"])
        if self.notification:
            self._run_cmd_async(
                [
                    "termux-notification",
                    "--id",
                    self.NOTIF_ID,
                    "--title",
                    f"✅ GemPhone закончил ({meta})",
                    "--content",
                    clean,
                    "--priority",
                    "high",
                ],
                serialized=True,
                seq=seq,
            )
        if self.tts:
            self._run_cmd_async(["termux-tts-speak", "Готово"])

    def notify_attention(self, reason: str) -> None:
        self._terminal_bell()
        if not self.enabled:
            return
        self._seq += 1
        seq = self._seq
        clean = re.sub(r"\s+", " ", reason).strip()[:120]

        if self.vibrate:
            self._run_cmd_async(["termux-vibrate", "-d", "300"])
        if self.toast:
            self._run_cmd_async(["termux-toast", "-g", "top", f"⚠️ GemPhone ждёт ответ: {clean[:60]}"])
        if self.notification:
            self._run_cmd_async(
                [
                    "termux-notification",
                    "--id",
                    self.NOTIF_ID,
                    "--title",
                    "⚠️ GemPhone ждёт ваш ответ",
                    "--content",
                    clean,
                    "--priority",
                    "high",
                ],
                serialized=True,
                seq=seq,
            )

    def notify_error(self, message: str) -> None:
        self._terminal_bell()
        if not self.enabled:
            return
        self._seq += 1
        seq = self._seq
        clean = re.sub(r"\s+", " ", message).strip()[:140]

        if self.vibrate:
            self._run_cmd_async(["termux-vibrate", "-d", "380"])
        if self.toast:
            self._run_cmd_async(["termux-toast", "-g", "top", "❌ GemPhone: ошибка (см. Termux)"])
        if self.notification:
            self._run_cmd_async(
                [
                    "termux-notification",
                    "--id",
                    self.NOTIF_ID,
                    "--title",
                    "❌ GemPhone: ошибка",
                    "--content",
                    clean,
                    "--priority",
                    "high",
                ],
                serialized=True,
                seq=seq,
            )

    def clear(self) -> None:
        if not self.enabled or not self.notification:
            return
        self._seq += 1
        seq = self._seq
        self._run_cmd_async(["termux-notification-remove", self.NOTIF_ID], serialized=True, seq=seq)


# ---------------------------------------------------------------------------
# Утилиты: обрезка текста, форматирование чисел, HTML-парсер
# ---------------------------------------------------------------------------


def truncate_text(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n...[обрезано ещё {omitted} симв.]"


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def mask_secret(secret: str) -> str:
    if not secret:
        return "(не задан)"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


def is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"\x00" in fh.read(4096)
    except OSError:
        return False


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
        elif tag.lower() in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "noscript", "svg") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            cleaned = data.strip()
            if cleaned:
                self._chunks.append(cleaned + " ")

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        lines = [re.sub(r"\s+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Конфигурация провайдеров, долгосрочная память и сессии
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    name: str
    provider_type: str  # "gemini" | "openai" | "anthropic" | "cli"
    base_url: str
    model: str
    api_key: str = ""
    env_var: str = ""
    cli_command: str = ""
    requires_key: bool = True
    supports_vision: bool = True
    description: str = ""

    def effective_api_key(self) -> str:
        if self.api_key.strip():
            return self.api_key.strip()
        if self.env_var:
            val = os.environ.get(self.env_var, "").strip()
            if val:
                return val
        if self.provider_type == "gemini":
            return os.environ.get("GOOGLE_API_KEY", "").strip()
        return ""

    def cli_binary(self) -> str:
        if not self.cli_command.strip():
            return ""
        try:
            parts = shlex.split(self.cli_command)
            return parts[0] if parts else ""
        except ValueError:
            return self.cli_command.strip().split()[0]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.provider_type,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "env_var": self.env_var,
            "model": self.model,
            "requires_key": self.requires_key,
            "supports_vision": self.supports_vision,
            "description": self.description,
        }
        if self.cli_command:
            data["cli_command"] = self.cli_command
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ProviderConfig:
        preset = PROVIDER_PRESETS.get(name, {})
        default_vision = bool(preset.get("supports_vision", name not in ("gemini-sub", "claude-sub", "chatgpt-sub")))
        return cls(
            name=name,
            provider_type=str(data.get("type", preset.get("type", "openai"))).lower(),
            base_url=str(data.get("base_url", preset.get("base_url", ""))).strip(),
            model=str(data.get("model", preset.get("model", ""))).strip(),
            api_key=str(data.get("api_key", "")).strip(),
            env_var=str(data.get("env_var", preset.get("env_var", ""))).strip(),
            cli_command=str(data.get("cli_command", preset.get("cli_command", ""))).strip(),
            requires_key=bool(data.get("requires_key", preset.get("requires_key", True))),
            supports_vision=bool(data.get("supports_vision", default_vision)),
            description=str(data.get("description", preset.get("description", ""))),
        )


class ConfigStore:
    """Управляет настройками провайдеров в ~/.gemphone/config.json."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self.active_provider: str = DEFAULT_PROVIDER
        self.providers: dict[str, ProviderConfig] = {}
        self.load()

    def load(self) -> None:
        raw: dict[str, Any] = {}
        created = False
        migrated = False

        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
        else:
            created = True
            if LEGACY_GEMCODE_CONFIG.exists():
                try:
                    raw = json.loads(LEGACY_GEMCODE_CONFIG.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = {}

        saved = raw.get("providers")
        if not isinstance(saved, dict):
            saved = {}

        self.providers = {}
        for name, preset in PROVIDER_PRESETS.items():
            if name not in saved:
                migrated = True
            merged = {**preset, **(saved.get(name) or {})}
            self.providers[name] = ProviderConfig.from_dict(name, merged)

        for name, pdata in saved.items():
            if name not in self.providers and isinstance(pdata, dict):
                self.providers[name] = ProviderConfig.from_dict(name, pdata)

        ds = self.providers.get("deepseek")
        if ds is not None and ds.model == "deepseek-chat":
            ds.model = "deepseek-flash"
            ds.supports_vision = True
            migrated = True

        gr = self.providers.get("groq")
        if gr is not None and gr.model == "llama-3.3-70b-versatile":
            gr.model = "openai/gpt-oss-120b"
            migrated = True

        env_gemini_model = os.environ.get("GEMINI_MODEL", "").strip()
        if env_gemini_model and created and "gemini" in self.providers:
            self.providers["gemini"].model = env_gemini_model

        active = str(raw.get("active_provider", DEFAULT_PROVIDER)).strip()
        self.active_provider = active if active in self.providers else DEFAULT_PROVIDER

        if created or migrated:
            self.save()

    def save(self) -> None:
        payload = {
            "active_provider": self.active_provider,
            "providers": {name: cfg.to_dict() for name, cfg in self.providers.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def get(self, name: str) -> ProviderConfig | None:
        return self.providers.get(name)

    def get_active(self) -> ProviderConfig:
        cfg = self.providers.get(self.active_provider)
        if cfg is None:
            self.active_provider = next(iter(self.providers))
            cfg = self.providers[self.active_provider]
        return cfg


class MemoryStore:
    """Долгосрочная key-value память агента между сессиями."""

    def __init__(self, path: Path = MEMORY_PATH) -> None:
        self.path = path
        self.data: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.data = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.data = {str(k): str(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def set(self, key: str, value: str) -> None:
        self.data[key.strip()] = value.strip()
        self.save()

    def delete(self, key: str) -> bool:
        existed = key in self.data
        if existed:
            del self.data[key]
            self.save()
        return existed


# ---------------------------------------------------------------------------
# Мультимодальные REST-клиенты + OAuth CLI-мост для подписок
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RequestCancelledError(Exception):
    """Исключение при ручной отмене активного запроса пользователем."""


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, prompt: int = 0, completion: int = 0, total: int | None = None) -> None:
        self.prompt_tokens += max(0, int(prompt))
        self.completion_tokens += max(0, int(completion))
        if total is not None and total > 0:
            self.total_tokens += int(total)
        else:
            self.total_tokens += max(0, int(prompt)) + max(0, int(completion))


@dataclass(frozen=True)
class ImageAttachment:
    mime_type: str
    data_b64: str
    label: str = "Phone screenshot"


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionResult:
    payload: dict[str, Any]
    image: ImageAttachment | None = None


@dataclass(frozen=True)
class TurnResponse:
    text: str
    tool_calls: list[ToolCall]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _extract_error_message(raw_body: str) -> str:
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and "message" in err:
                return str(err["message"])
            if isinstance(err, str):
                return err
            if "message" in data:
                return str(data["message"])
    except json.JSONDecodeError:
        pass
    return raw_body[:350].strip()


def _http_post_json(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    max_retries: int = 3,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if cancel_event is not None and cancel_event.is_set():
        raise RequestCancelledError("Запрос отменён.")

    if cancel_event is None:
        return _http_post_json_sync(
            url=url,
            payload=payload,
            headers=headers,
            timeout=timeout,
            max_retries=max_retries,
        )

    result_box: list[dict[str, Any]] = []
    error_box: list[BaseException] = []

    def _worker() -> None:
        try:
            res = _http_post_json_sync(
                url=url,
                payload=payload,
                headers=headers,
                timeout=timeout,
                max_retries=max_retries,
                cancel_event=cancel_event,
            )
            result_box.append(res)
        except BaseException as exc:  # noqa: BLE001
            error_box.append(exc)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    while t.is_alive():
        if cancel_event.is_set():
            raise RequestCancelledError("Запрос отменён.")
        t.join(timeout=0.06)

    if cancel_event.is_set():
        raise RequestCancelledError("Запрос отменён.")
    if error_box:
        raise error_box[0]
    if result_box:
        return result_box[0]
    raise ApiError("Пустой ответ от сетевого потока.")


def _http_post_json_sync(
    *,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = DEFAULT_HTTP_TIMEOUT,
    max_retries: int = 3,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    retryable = {429, 500, 502, 503, 504}
    merged_headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": HTTP_USER_AGENT,
        **headers,
    }
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("Запрос отменён.")
        if HAS_HTTPX and httpx is not None:
            try:
                with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                    resp = client.post(url, json=payload, headers=merged_headers)
                if resp.status_code >= 400:
                    msg = _extract_error_message(resp.text) or f"HTTP {resp.status_code}"
                    if resp.status_code == 429 and "limit: 0" in msg:
                        raise ApiError(f"HTTP 429: {msg}", 429)
                    if resp.status_code in retryable and attempt < max_retries:
                        time.sleep(min(12.0, 1.5 * (2**attempt)))
                        last_error = ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)
                        continue
                    raise ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)
                return resp.json()
            except ApiError:
                raise
            except (httpx.HTTPError, ValueError, OSError) as exc:
                if attempt < max_retries:
                    time.sleep(min(8.0, 1.5 * (2**attempt)))
                    last_error = exc
                    continue
                raise ApiError(f"Сетевая ошибка: {exc}") from exc
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url=url,
                data=body,
                method="POST",
                headers={**merged_headers, "Connection": "close"},
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                err_body = exc.read().decode("utf-8", errors="replace")
                msg = _extract_error_message(err_body) or f"HTTP {exc.code}"
                if exc.code == 429 and "limit: 0" in msg:
                    raise ApiError(f"HTTP 429: {msg}", 429) from exc
                if exc.code in retryable and attempt < max_retries:
                    time.sleep(min(12.0, 1.5 * (2**attempt)))
                    last_error = ApiError(f"HTTP {exc.code}: {msg}", exc.code)
                    continue
                raise ApiError(f"HTTP {exc.code}: {msg}", exc.code) from exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                if attempt < max_retries:
                    time.sleep(min(8.0, 1.5 * (2**attempt)))
                    last_error = exc
                    continue
                raise ApiError(f"Сетевая ошибка: {exc}") from exc

    raise ApiError(f"Не удалось выполнить запрос: {last_error}")


def _http_get_text(url: str, timeout: int = 20) -> tuple[int, str, str]:
    headers = {"User-Agent": HTTP_USER_AGENT}
    if HAS_HTTPX and httpx is not None:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            return resp.status_code, resp.headers.get("content-type", ""), resp.text

    req = urllib.request.Request(url=url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        raw = resp.read(512_000).decode("utf-8", errors="replace")
        return resp.status, ctype, raw


class BaseLLMClient(ABC):
    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        self.config = config
        self.api_key = api_key
        self.model = config.model
        self.timeout = timeout

    @property
    def provider_name(self) -> str:
        return self.config.name

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def snapshot(self) -> int:
        pass

    @abstractmethod
    def rollback(self, marker: int) -> None:
        pass

    @abstractmethod
    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        pass

    @abstractmethod
    def add_user_message(self, text: str) -> None:
        pass

    @abstractmethod
    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        pass

    def prune_stale_context(self) -> None:
        pass

    def finalize_turn(self) -> None:
        pass

    @abstractmethod
    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        pass


class GeminiRestClient(BaseLLMClient):
    """Нативный клиент Google AI Studio / Vertex OAuth."""

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._contents: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._contents.clear()

    def snapshot(self) -> int:
        return len(self._contents)

    def rollback(self, marker: int) -> None:
        del self._contents[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._contents.clear()
        for role, text in transcript[-16:]:
            self._contents.append({"role": "user" if role == "user" else "model", "parts": [{"text": text}]})

    def add_user_message(self, text: str) -> None:
        self._contents.append({"role": "user", "parts": [{"text": text}]})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        parts: list[dict[str, Any]] = []
        attached_images: list[ImageAttachment] = []

        for call, res in results:
            fn_resp: dict[str, Any] = {"name": call.name, "response": res.payload}
            if call.id:
                fn_resp["id"] = call.id
            parts.append({"functionResponse": fn_resp})
            if res.image is not None and self.config.supports_vision:
                attached_images.append(res.image)

        for img in attached_images:
            parts.append({"inlineData": {"mimeType": img.mime_type, "data": img.data_b64}})

        self._contents.append({"role": "user", "parts": parts})

    def prune_stale_context(self) -> None:
        seen_image = False
        seen_ui_dump = False
        keep_recent_from = max(0, len(self._contents) - 4)

        for idx in range(len(self._contents) - 1, -1, -1):
            msg = self._contents[idx]
            if msg.get("role") != "user":
                continue
            parts = msg.get("parts")
            if not isinstance(parts, list):
                continue

            new_parts: list[dict[str, Any]] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if "inlineData" in part:
                    if seen_image:
                        new_parts.append({"text": "[old screenshot pruned]"})
                        continue
                    seen_image = True
                    new_parts.append(part)
                    continue

                if "functionResponse" in part and isinstance(part["functionResponse"], dict):
                    fr = part["functionResponse"]
                    name = fr.get("name", "")
                    if name == "adb_ui_dump":
                        if seen_ui_dump:
                            fr["response"] = {"pruned": "older UI dump removed to save tokens"}
                        else:
                            seen_ui_dump = True
                    elif idx < keep_recent_from:
                        serialized = json.dumps(fr.get("response", {}), ensure_ascii=False)
                        if len(serialized) > STALE_TOOL_OUTPUT_CHARS:
                            fr["response"] = {"pruned_preview": serialized[:STALE_TOOL_OUTPUT_CHARS] + "..."}
                new_parts.append(part)
            msg["parts"] = new_parts

    def _auth_headers(self) -> dict[str, str]:
        token = self.api_key.strip()
        if token.lower() == "gcloud":
            if shutil.which("gcloud") is None:
                raise ApiError("Для режима 'gcloud' установите Google Cloud CLI и выполните `gcloud auth application-default login`.")
            for cmd in (
                ["gcloud", "auth", "application-default", "print-access-token"],
                ["gcloud", "auth", "print-access-token"],
            ):
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if proc.returncode == 0 and proc.stdout.strip():
                    return {"Authorization": f"Bearer {proc.stdout.strip()}"}
            raise ApiError("Не удалось получить OAuth-токен от gcloud.")
        if token.startswith("ya29."):
            return {"Authorization": f"Bearer {token}"}
        return {"x-goog-api-key": token}

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        self.prune_stale_context()
        url = f"{self.config.base_url.rstrip('/')}/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": self._contents,
            "generationConfig": {"temperature": temperature},
        }
        if tool_specs:
            payload["tools"] = [{"functionDeclarations": tool_specs}]

        response = _http_post_json(
            url=url,
            payload=payload,
            headers=self._auth_headers(),
            timeout=self.timeout,
            cancel_event=cancel_event,
        )

        candidates = response.get("candidates")
        if not candidates:
            reason = response.get("promptFeedback", {}).get("blockReason")
            raise ApiError(f"Модель не вернула ответ (blockReason={reason}).")

        model_content = candidates[0].get("content")
        if not isinstance(model_content, dict):
            raise ApiError("Некорректный ответ Gemini API: отсутствует content.")

        if "role" not in model_content:
            model_content = {**model_content, "role": "model"}
        self._contents.append(model_content)

        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in model_content.get("parts") or []:
            if not isinstance(part, dict) or part.get("thought") is True:
                continue
            if part.get("text"):
                texts.append(str(part["text"]))
            if isinstance(part.get("functionCall"), dict):
                fc = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=str(fc.get("id") or ""),
                        name=str(fc.get("name") or ""),
                        args=fc.get("args") if isinstance(fc.get("args"), dict) else {},
                    )
                )

        usage = response.get("usageMetadata") or {}
        return TurnResponse(
            text="\n".join(texts).strip(),
            tool_calls=calls,
            prompt_tokens=int(usage.get("promptTokenCount", 0)),
            completion_tokens=int(usage.get("candidatesTokenCount", 0)),
            total_tokens=int(usage.get("totalTokenCount", 0)),
        )


class OpenAICompatClient(BaseLLMClient):
    """Универсальный клиент OpenAI-совместимых API с жёсткой экономией токенов."""

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._messages: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._messages.clear()

    def snapshot(self) -> int:
        return len(self._messages)

    def rollback(self, marker: int) -> None:
        del self._messages[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._messages.clear()
        for role, text in transcript[-16:]:
            self._messages.append({"role": "user" if role == "user" else "assistant", "content": text})

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        images: list[ImageAttachment] = []
        for call, res in results:
            self._messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(res.payload, ensure_ascii=False),
                }
            )
            if res.image is not None and self.config.supports_vision:
                images.append(res.image)

        if images:
            content_blocks: list[dict[str, Any]] = [{"type": "text", "text": "Screenshot:"}]
            for img in images:
                content_blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.mime_type};base64,{img.data_b64}",
                            "detail": "high",
                        },
                    }
                )
            self._messages.append({"role": "user", "content": content_blocks})

    def prune_stale_context(self) -> None:
        seen_image = False
        seen_ui_dump = False
        keep_recent_from = max(0, len(self._messages) - 4)

        for idx in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[idx]
            role = msg.get("role")

            if role == "user" and isinstance(msg.get("content"), list):
                has_img = any(isinstance(blk, dict) and blk.get("type") == "image_url" for blk in msg["content"])
                if has_img:
                    if seen_image:
                        msg["content"] = "[old screenshot pruned to save tokens]"
                    else:
                        seen_image = True

            elif role == "tool":
                name = msg.get("name", "")
                if name == "adb_ui_dump":
                    if seen_ui_dump:
                        msg["content"] = '{"pruned":"older UI dump removed"}'
                    else:
                        seen_ui_dump = True
                elif idx < keep_recent_from:
                    raw = str(msg.get("content", ""))
                    if len(raw) > STALE_TOOL_OUTPUT_CHARS:
                        msg["content"] = raw[:STALE_TOOL_OUTPUT_CHARS] + "...[pruned]"

    def finalize_turn(self) -> None:
        for msg in self._messages:
            if msg.get("role") == "assistant":
                msg.pop("reasoning_content", None)
                msg.pop("reasoning_details", None)

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        self.prune_stale_context()
        base = self.config.base_url.rstrip("/")
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_instruction}, *self._messages],
            "temperature": temperature,
        }
        if tool_specs:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": spec.get("description", ""),
                        "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
                for spec in tool_specs
            ]
            payload["tool_choice"] = "auto"

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in base:
            headers["HTTP-Referer"] = "https://github.com/gemphone-cli"
            headers["X-Title"] = "GemPhone Android Agent"

        response = _http_post_json(
            url=url,
            payload=payload,
            headers=headers,
            timeout=self.timeout,
            cancel_event=cancel_event,
        )
        choices = response.get("choices")
        if not choices or not isinstance(choices[0], dict):
            raise ApiError("Некорректный ответ OpenAI-совместимого API: отсутствует choices.")

        msg = choices[0].get("message")
        if not isinstance(msg, dict):
            raise ApiError("Некорректный ответ OpenAI-совместимого API: отсутствует message.")

        raw_content = msg.get("content")
        raw_tool_calls = msg.get("tool_calls") or []
        record: dict[str, Any] = {
            "role": "assistant",
            "content": raw_content if raw_content is not None else "",
        }
        if raw_calls := raw_tool_calls:
            if msg.get("reasoning_content") is not None:
                record["reasoning_content"] = msg["reasoning_content"]
            if msg.get("reasoning_details") is not None:
                record["reasoning_details"] = msg["reasoning_details"]
            record["tool_calls"] = raw_calls
        self._messages.append(record)

        parsed_calls: list[ToolCall] = []
        for idx, tc in enumerate(raw_tool_calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            call_id = str(tc.get("id") or f"call_{idx}_{int(time.time())}")
            fn_name = str(fn.get("name") or "")
            fn_args = self._parse_arguments(fn.get("arguments"))
            parsed_calls.append(ToolCall(id=call_id, name=fn_name, args=fn_args))

        text = self._extract_text(raw_content)
        usage = response.get("usage") or {}
        return TurnResponse(
            text=text,
            tool_calls=parsed_calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
        )

    @staticmethod
    def _extract_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p).strip()
        return ""

    @staticmethod
    def _parse_arguments(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            cleaned = raw_args.strip()
            if not cleaned:
                return {}
            try:
                parsed = json.loads(cleaned)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


class AnthropicRestClient(BaseLLMClient):
    """Нативный клиент Anthropic Claude API (/v1/messages)."""

    def __init__(self, config: ProviderConfig, api_key: str, timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._messages: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._messages.clear()

    def snapshot(self) -> int:
        return len(self._messages)

    def rollback(self, marker: int) -> None:
        del self._messages[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._messages.clear()
        for role, text in transcript[-16:]:
            self._messages.append({"role": "user" if role == "user" else "assistant", "content": text})

    def add_user_message(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        blocks: list[dict[str, Any]] = []
        for call, res in results:
            content: Any = json.dumps(res.payload, ensure_ascii=False)
            if res.image is not None and self.config.supports_vision:
                content = [
                    {"type": "text", "text": content},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": res.image.mime_type,
                            "data": res.image.data_b64,
                        },
                    },
                ]
            blocks.append({"type": "tool_result", "tool_use_id": call.id, "content": content})
        self._messages.append({"role": "user", "content": blocks})

    def prune_stale_context(self) -> None:
        seen_image = False
        keep_recent_from = max(0, len(self._messages) - 4)

        for idx in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[idx]
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for blk in msg["content"]:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                content = blk.get("content")
                if isinstance(content, list):
                    if any(isinstance(c, dict) and c.get("type") == "image" for c in content):
                        if seen_image:
                            blk["content"] = "[old screenshot pruned]"
                        else:
                            seen_image = True
                elif isinstance(content, str) and idx < keep_recent_from and len(content) > STALE_TOOL_OUTPUT_CHARS:
                    blk["content"] = content[:STALE_TOOL_OUTPUT_CHARS] + "...[pruned]"

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        self.prune_stale_context()
        base = self.config.base_url.rstrip("/")
        url = base if base.endswith("/messages") else f"{base}/messages"
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system_instruction,
            "messages": self._messages,
            "temperature": temperature,
        }
        if tool_specs:
            payload["tools"] = [
                {
                    "name": spec["name"],
                    "description": spec.get("description", ""),
                    "input_schema": spec.get("parameters", {"type": "object", "properties": {}}),
                }
                for spec in tool_specs
            ]

        response = _http_post_json(
            url=url,
            payload=payload,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            timeout=self.timeout,
            cancel_event=cancel_event,
        )
        blocks = response.get("content")
        if not isinstance(blocks, list):
            raise ApiError("Некорректный ответ Anthropic API: отсутствует content.")

        self._messages.append({"role": "assistant", "content": blocks})
        texts: list[str] = []
        calls: list[ToolCall] = []
        for blk in blocks:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "text" and blk.get("text"):
                texts.append(str(blk["text"]))
            elif blk.get("type") == "tool_use":
                calls.append(
                    ToolCall(
                        id=str(blk.get("id") or ""),
                        name=str(blk.get("name") or ""),
                        args=blk.get("input") if isinstance(blk.get("input"), dict) else {},
                    )
                )

        usage = response.get("usage") or {}
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
        return TurnResponse(
            text="\n".join(texts).strip(),
            tool_calls=calls,
            prompt_tokens=in_tok,
            completion_tokens=out_tok,
            total_tokens=in_tok + out_tok,
        )


class CliSubscriptionClient(BaseLLMClient):
    """OAuth-мост для работы по подпискам (Claude Pro/Max, Gemini Pro, ChatGPT Plus/Pro)."""

    _JSON_BLOCK_RE: Final[re.Pattern[str]] = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

    def __init__(self, config: ProviderConfig, api_key: str = "", timeout: int = DEFAULT_HTTP_TIMEOUT) -> None:
        super().__init__(config, api_key, timeout)
        self._history: list[dict[str, str]] = []

    def clear(self) -> None:
        self._history.clear()

    def snapshot(self) -> int:
        return len(self._history)

    def rollback(self, marker: int) -> None:
        del self._history[marker:]

    def seed_transcript(self, transcript: list[tuple[str, str]]) -> None:
        self._history.clear()
        for role, text in transcript[-12:]:
            self._history.append({"role": role, "content": text})

    def add_user_message(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[tuple[ToolCall, ToolExecutionResult]]) -> None:
        lines: list[str] = []
        for call, res in results:
            body = json.dumps(res.payload, ensure_ascii=False)
            lines.append(f"Tool `{call.name}` result: {body}")
            if res.image is not None:
                lines.append(
                    "[Note: Screenshot image cannot be attached directly in CLI subscription mode; "
                    "use `adb_ui_dump` for UI elements and text.]"
                )
        self._history.append({"role": "tool", "content": "\n".join(lines)})

    def prune_stale_context(self) -> None:
        if len(self._history) > 14:
            self._history = self._history[-14:]
        keep_recent = max(0, len(self._history) - 3)
        for idx, item in enumerate(self._history):
            if idx < keep_recent and item["role"] == "tool" and len(item["content"]) > STALE_TOOL_OUTPUT_CHARS:
                item["content"] = item["content"][:STALE_TOOL_OUTPUT_CHARS] + "...[pruned]"

    def _build_prompt(self, tool_specs: list[dict[str, Any]], system_instruction: str) -> str:
        sections: list[str] = [f"[SYSTEM]\n{system_instruction}"]
        if tool_specs:
            compact_tools: list[str] = []
            for spec in tool_specs:
                props = (spec.get("parameters") or {}).get("properties") or {}
                req = set((spec.get("parameters") or {}).get("required") or [])
                params = ", ".join(f"{k}{'*' if k in req else ''}" for k in props)
                compact_tools.append(f"- {spec['name']}({params}): {spec.get('description', '')}")
            sections.append(
                "[GEMPHONE TOOLS — USE THESE INSTEAD OF ANY BUILT-IN CLI TOOLS]\n"
                + "\n".join(compact_tools)
                + "\n\n[TOOL CALLING FORMAT]\n"
                "To invoke one or more GemPhone tools, include a JSON object:\n"
                '{"thought": "brief plan", "tool_calls": [{"name": "tool_name", "args": {...}}]}\n'
                "If no tool call is needed and you are ready to answer the user, reply in normal plain text."
            )
        conv_lines = [
            f"{'USER' if i['role'] == 'user' else ('ASSISTANT' if i['role'] == 'assistant' else 'TOOL_RESULTS')}:\n{i['content']}"
            for i in self._history
        ]
        sections.append("[CONVERSATION]\n" + "\n\n".join(conv_lines))
        return "\n\n".join(sections)

    def _build_command(self) -> list[str]:
        raw_cmd = self.config.cli_command.strip()
        if not raw_cmd:
            raise ApiError(f"Для подписки '{self.config.name}' не задан cli_command.")
        cmd = shlex.split(raw_cmd)
        binary = cmd[0]
        if shutil.which(binary) is None:
            hint = SUBSCRIPTION_INSTALL_HINTS.get(self.config.name)
            install_msg = f" Установите: `{hint[0]}` и войдите через `/login {self.config.name}`." if hint else ""
            raise ApiError(f"CLI `{binary}` не найден в Termux.{install_msg}")
        if self.model and self.model.lower() not in ("default", "auto", ""):
            if "--model" not in cmd and "-m" not in cmd:
                cmd.extend(["--model", self.model])
        return cmd

    def _run_cli(self, prompt_text: str, cancel_event: threading.Event | None) -> tuple[str, str]:
        cmd = self._build_command()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out_box: list[tuple[str, str]] = []
        err_box: list[BaseException] = []

        def _comm() -> None:
            try:
                stdout, stderr = proc.communicate(input=prompt_text, timeout=self.timeout)
                out_box.append((stdout or "", stderr or ""))
            except BaseException as exc:  # noqa: BLE001
                err_box.append(exc)

        t = threading.Thread(target=_comm, daemon=True)
        t.start()
        while t.is_alive():
            if cancel_event is not None and cancel_event.is_set():
                try:
                    proc.terminate()
                    proc.wait(timeout=1.5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise RequestCancelledError("Запрос к CLI подписки отменён.")
            t.join(timeout=0.06)

        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("Запрос к CLI подписки отменён.")
        if err_box:
            raise ApiError(f"Ошибка запуска `{cmd[0]}`: {err_box[0]}")
        stdout, stderr = out_box[0] if out_box else ("", "")

        if proc.returncode != 0 and not stdout.strip():
            err_msg = (stderr or stdout).strip()[:350]
            raise ApiError(
                f"CLI `{cmd[0]}` завершился с кодом {proc.returncode}: {err_msg}\n"
                f"Если сессия истекла, выполните `/login {self.config.name}`."
            )
        return stdout, stderr

    @staticmethod
    def _parse_cli_envelope(stdout: str) -> tuple[str, int, int, int]:
        cleaned = stdout.strip()
        if not cleaned:
            return "", 0, 0, 0
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return cleaned, 0, 0, 0
        if not isinstance(data, dict):
            return cleaned, 0, 0, 0
        if "result" in data and isinstance(data["result"], str):
            usage = data.get("usage") or {}
            in_tok = int(usage.get("input_tokens", 0))
            out_tok = int(usage.get("output_tokens", 0))
            return data["result"].strip(), in_tok, out_tok, in_tok + out_tok
        if "response" in data and isinstance(data["response"], str):
            stats = data.get("stats") or {}
            models_stats = stats.get("models") if isinstance(stats, dict) else None
            in_tok = out_tok = 0
            if isinstance(models_stats, dict):
                for mstat in models_stats.values():
                    if isinstance(mstat, dict):
                        toks = mstat.get("tokens") or {}
                        in_tok += int(toks.get("prompt", 0))
                        out_tok += int(toks.get("candidates", 0))
            return data["response"].strip(), in_tok, out_tok, in_tok + out_tok
        return cleaned, 0, 0, 0

    def _extract_tool_calls(self, text: str) -> tuple[str, list[ToolCall]]:
        candidates: list[tuple[str, str]] = []
        for m in self._JSON_BLOCK_RE.finditer(text):
            prefix = text[: m.start()].strip()
            suffix = text[m.end() :].strip()
            candidates.append((m.group(1).strip(), f"{prefix}\n{suffix}".strip()))

        decoder = json.JSONDecoder()
        idx = 0
        while True:
            brace_idx = text.find("{", idx)
            if brace_idx == -1:
                break
            try:
                obj, end_idx = decoder.raw_decode(text, brace_idx)
                if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
                    prefix = text[:brace_idx].strip()
                    suffix = text[end_idx:].strip()
                    candidates.append((text[brace_idx:end_idx], f"{prefix}\n{suffix}".strip()))
                    idx = end_idx
                    continue
            except json.JSONDecodeError:
                pass
            idx = brace_idx + 1

        for raw_json, leftover_text in candidates:
            try:
                obj = json.loads(raw_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("tool_calls"), list):
                continue
            calls: list[ToolCall] = []
            for c_idx, item in enumerate(obj["tool_calls"]):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                args = item.get("args")
                calls.append(
                    ToolCall(
                        id=f"cli_{c_idx}_{int(time.time())}",
                        name=str(item["name"]),
                        args=args if isinstance(args, dict) else {},
                    )
                )
            if calls:
                thought = str(obj.get("thought") or leftover_text or "").strip()
                return thought, calls
        return text.strip(), []

    def generate_step(
        self,
        *,
        tool_specs: list[dict[str, Any]],
        system_instruction: str,
        temperature: float = 0.2,
        cancel_event: threading.Event | None = None,
    ) -> TurnResponse:
        del temperature
        self.prune_stale_context()
        prompt_text = self._build_prompt(tool_specs, system_instruction)
        stdout, _stderr = self._run_cli(prompt_text, cancel_event)
        raw_reply, in_tok, out_tok, tot_tok = self._parse_cli_envelope(stdout)
        text, calls = self._extract_tool_calls(raw_reply)
        self._history.append({"role": "assistant", "content": raw_reply})
        return TurnResponse(
            text=text,
            tool_calls=calls,
            prompt_tokens=in_tok,
            completion_tokens=out_tok,
            total_tokens=tot_tok,
        )


def create_llm_client(config: ProviderConfig, api_key: str) -> BaseLLMClient:
    ptype = config.provider_type.lower()
    if ptype == "gemini":
        return GeminiRestClient(config=config, api_key=api_key)
    if ptype == "anthropic":
        return AnthropicRestClient(config=config, api_key=api_key)
    if ptype == "openai":
        return OpenAICompatClient(config=config, api_key=api_key)
    if ptype == "cli":
        return CliSubscriptionClient(config=config, api_key=api_key)
    raise ValueError(f"Неподдерживаемый тип API: '{ptype}'")


# ---------------------------------------------------------------------------
# Ядро управления Android: AccessibilityService Bridge + ADB + Termux:API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UiElement:
    element_id: int
    text: str
    content_desc: str
    resource_id: str
    class_name: str
    center_x: int
    center_y: int
    bounds: tuple[int, int, int, int]
    clickable: bool
    scrollable: bool
    editable: bool
    focused: bool
    node_id: str = ""

    def to_compact_line(self) -> str:
        flags: list[str] = []
        if self.clickable:
            flags.append("c")
        if self.editable:
            flags.append("in")
        if self.scrollable:
            flags.append("sc")
        if self.focused:
            flags.append("foc")
        label = self.text or self.content_desc
        if self.content_desc and self.text and self.content_desc != self.text:
            label = f"{self.text} ({self.content_desc})"
        label = label[:55]
        res = self.resource_id.split("/")[-1][:25] if self.resource_id else ""
        flag_str = f"[{','.join(flags)}]" if flags else ""
        res_str = f" #{res}" if res else ""
        lbl_str = f' "{label}"' if label else ""
        return f"{self.element_id}:{flag_str}{lbl_str}{res_str} @{self.center_x},{self.center_y}"


class AccessibilityBridgeClient:
    """Клиент локального HTTP-моста GemPhone Bridge (AccessibilityService на 127.0.0.1:7331)."""

    def __init__(
        self,
        host: str = BRIDGE_DEFAULT_HOST,
        port: int = BRIDGE_DEFAULT_PORT,
        token_path: Path = BRIDGE_TOKEN_PATH,
        timeout: float = 8.0,
    ) -> None:
        self.host = host
        self.port = port
        self.token_path = token_path
        self.timeout = timeout
        self.token: str = self.load_token()
        self._last_ping_at: float = 0.0
        self._last_ping_ok: bool = False
        self._last_unauthorized: bool = False
        self._last_info: dict[str, Any] = {}

    def load_token(self) -> str:
        # Файл — источник истины (его пишет и /bridge token, и сам сервис).
        # env GEMPHONE_BRIDGE_TOKEN — только фолбэк, если файла нет.
        if self.token_path.exists():
            try:
                file_tok = self.token_path.read_text(encoding="utf-8").strip()
            except OSError:
                file_tok = ""
            if file_tok:
                return file_tok
        env_tok = os.environ.get("GEMPHONE_BRIDGE_TOKEN", "").strip()
        if env_tok:
            return env_tok
        return ""

    def save_token(self, token: str) -> None:
        cleaned = token.strip()
        self.token = cleaned
        self._last_ping_at = 0.0
        self._last_unauthorized = False
        try:
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.token_path.write_text(cleaned + "\n", encoding="utf-8")
            try:
                os.chmod(self.token_path, 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any], bytes]:
        if not self.token:
            self.token = self.load_token()
        url = f"{self.base_url()}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers: dict[str, str] = {"User-Agent": HTTP_USER_AGENT}
        if self.token:
            headers["x-gemphone-token"] = self.token
        data: bytes | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                parsed: dict[str, Any] = {}
                if "json" in ctype.lower():
                    try:
                        obj = json.loads(raw.decode("utf-8"))
                        if isinstance(obj, dict):
                            parsed = obj
                    except json.JSONDecodeError:
                        pass
                return resp.status, parsed, raw
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            parsed = {}
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
                if isinstance(obj, dict):
                    parsed = obj
            except json.JSONDecodeError:
                pass
            return exc.code, parsed, raw

    def is_available(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and (now - self._last_ping_at) < 4.0:
            return self._last_ping_ok
        self._last_ping_at = now
        try:
            status, info, _ = self._request("GET", "/status", timeout=2.5)
            if status in (401, 403):
                self._last_ping_ok = False
                self._last_unauthorized = True
                return False
            self._last_unauthorized = False
            ok = status == 200 and bool(info.get("service_running", True))
            self._last_ping_ok = ok
            if ok and info:
                self._last_info = info
            return ok
        except Exception:
            self._last_ping_ok = False
            self._last_unauthorized = False
            return False

    def status_info(self) -> dict[str, Any]:
        if self.is_available(force=True) and self._last_info:
            return dict(self._last_info)
        return {
            "connected": False,
            "unauthorized": self._last_unauthorized,
            "port": self.port,
            "token_set": bool(self.token),
        }

    def dump_ui(self, *, only_interactive: bool = False) -> dict[str, Any]:
        _, data, _ = self._request(
            "GET", "/ui?only_interactive=" + ("1" if only_interactive else "0"), timeout=15.0
        )
        if not data.get("ok", True) and data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def tap(self, *, x: int, y: int, node_id: str = "", long_press: bool = False, duration_ms: int = 700) -> dict[str, Any]:
        _, data, _ = self._request(
            "POST",
            "/tap",
            json_body={"x": x, "y": y, "duration_ms": duration_ms if long_press else 60},
            timeout=10.0,
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def swipe(self, *, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 320) -> dict[str, Any]:
        _, data, _ = self._request(
            "POST",
            "/swipe",
            json_body={"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms},
            timeout=10.0,
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def type_text(self, text: str, *, press_enter: bool = False) -> dict[str, Any]:
        _, data, _ = self._request(
            "POST",
            "/type",
            json_body={"text": text, "press_enter": press_enter},
            timeout=10.0,
        )
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def global_action(self, action_name: str) -> dict[str, Any]:
        _, data, _ = self._request("POST", "/key", json_body={"key": action_name}, timeout=10.0)
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def capture_screenshot_bytes(self) -> tuple[bytes, str]:
        _, data, raw = self._request("GET", "/screenshot", timeout=20.0)
        if data and isinstance(data.get("image_b64"), str) and data["image_b64"]:
            mime = str(data.get("mime_type") or "image/jpeg")
            return base64.b64decode(data["image_b64"]), mime
        if data and data.get("error"):
            raise RuntimeError(str(data["error"]))
        if len(raw) > 100:
            mime = "image/png" if raw.startswith(b"\x89PNG") else "image/jpeg"
            return raw, mime
        raise RuntimeError("Bridge не вернул изображение скриншота (проверьте Android 11+ / takeScreenshot).")


class PhoneController:
    """
    Высокоуровневый драйвер Android для Termux:
    основной быстрый транспорт — AccessibilityService Bridge (без ADB и Wi-Fi),
    резервный транспорт — Wireless ADB + Termux:API.
    """

    _BOUNDS_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

    def __init__(self, target_serial: str | None = None, bridge: AccessibilityBridgeClient | None = None) -> None:
        self.target_serial = target_serial
        self.bridge = bridge or AccessibilityBridgeClient()
        self.last_ui_elements: dict[int, UiElement] = {}
        self._screen_size_cache: tuple[int, int] | None = None
        self._adb_cache_at: float = 0.0
        self._adb_devices_cache: list[dict[str, str]] = []
        self.screenshot_max_dim: int = SCREENSHOT_MAX_DIM
        self.screenshot_quality: int = SCREENSHOT_JPEG_QUALITY

    def has_bridge(self, *, force: bool = False) -> bool:
        return self.bridge.is_available(force=force)

    def has_adb(self) -> bool:
        return shutil.which("adb") is not None

    def has_termux_api(self) -> bool:
        return shutil.which("termux-battery-status") is not None

    def invalidate_adb_cache(self) -> None:
        self._adb_cache_at = 0.0

    def adb_devices(self, *, force: bool = False) -> list[dict[str, str]]:
        if not self.has_adb():
            return []
        now = time.monotonic()
        if not force and (now - self._adb_cache_at) < ADB_CACHE_TTL_SEC:
            return list(self._adb_devices_cache)
        try:
            proc = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=6)
            devices: list[dict[str, str]] = []
            for line in proc.stdout.splitlines()[1:]:
                parts = line.strip().split()
                if len(parts) >= 2:
                    devices.append({"serial": parts[0], "state": parts[1]})
            self._adb_devices_cache = devices
            self._adb_cache_at = now
            return list(devices)
        except Exception:
            return list(self._adb_devices_cache)

    def active_adb_serial(self, *, force: bool = False) -> str | None:
        devices = [d for d in self.adb_devices(force=force) if d["state"] == "device"]
        if not devices:
            return None
        if self.target_serial:
            for d in devices:
                if d["serial"] == self.target_serial:
                    return self.target_serial
        return devices[0]["serial"]

    def transport_badge(self, *, force: bool = False) -> str:
        bridge_ok = self.has_bridge(force=force)
        adb_ser = self.active_adb_serial(force=force)
        if bridge_ok and adb_ser:
            return f"Bridge:{self.bridge.port} + ADB"
        if bridge_ok:
            return f"Bridge:{self.bridge.port}"
        if adb_ser:
            return f"ADB:{adb_ser}"
        return "нет связи (/bridge /adb)"

    def run_adb(
        self,
        args: list[str],
        *,
        timeout: int = 15,
        binary: bool = False,
        require_device: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        if cancel_event is not None and cancel_event.is_set():
            raise RequestCancelledError("Операция ADB отменена.")
        if not self.has_adb():
            raise RuntimeError("`adb` не установлен (pkg install android-tools).")
        cmd = ["adb"]
        if require_device:
            serial = self.active_adb_serial()
            if not serial:
                raise RuntimeError(
                    "Нет активного подключения к экрану: включите GemPhone Bridge в Спец. возможностях "
                    "или подключите ADB командой `/adb`."
                )
            cmd.extend(["-s", serial])
        cmd.extend(args)

        if cancel_event is None:
            return subprocess.run(cmd, capture_output=True, text=not binary, timeout=timeout)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        res_box: list[subprocess.CompletedProcess[Any]] = []
        err_box: list[BaseException] = []

        def _wait_proc() -> None:
            try:
                out_b, err_b = proc.communicate(timeout=timeout)
                out_val: Any = out_b if binary else out_b.decode("utf-8", errors="replace")
                err_val: Any = err_b if binary else err_b.decode("utf-8", errors="replace")
                res_box.append(subprocess.CompletedProcess(cmd, proc.returncode, out_val, err_val))
            except BaseException as exc:  # noqa: BLE001
                err_box.append(exc)

        t = threading.Thread(target=_wait_proc, daemon=True)
        t.start()
        while t.is_alive():
            if cancel_event.is_set():
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise RequestCancelledError("Операция ADB отменена пользователем.")
            t.join(timeout=0.05)

        if cancel_event.is_set():
            raise RequestCancelledError("Операция ADB отменена пользователем.")
        if err_box:
            raise err_box[0]
        return res_box[0]

    def run_termux_cmd(
        self,
        cmd_name: str,
        args: list[str] | None = None,
        *,
        stdin_text: str | None = None,
        timeout: int = 15,
        parse_json: bool = False,
    ) -> Any:
        if shutil.which(cmd_name) is None:
            raise RuntimeError(f"`{cmd_name}` не найден (pkg install termux-api + приложение Termux:API).")
        proc = subprocess.run(
            [cmd_name, *(args or [])],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            raise RuntimeError(f"{cmd_name} exit {proc.returncode}: {err}")
        out = proc.stdout.strip()
        if parse_json:
            return json.loads(out) if out else {}
        return out

    def get_screen_size(self) -> tuple[int, int]:
        if self._screen_size_cache is not None:
            return self._screen_size_cache
        if self.has_bridge():
            try:
                info = self.bridge.status_info()
                w, h = int(info.get("screen_width", 0)), int(info.get("screen_height", 0))
                if w > 0 and h > 0:
                    self._screen_size_cache = (w, h)
                    return self._screen_size_cache
            except Exception:
                pass
        if self.active_adb_serial():
            try:
                proc = self.run_adb(["shell", "wm", "size"], timeout=8)
                match = re.search(r"(\d+)\s*x\s*(\d+)", proc.stdout)
                if match:
                    self._screen_size_cache = (int(match.group(1)), int(match.group(2)))
                    return self._screen_size_cache
            except Exception:
                pass
        return (1080, 2400)

    def get_foreground_app(self) -> str:
        if self.has_bridge():
            try:
                info = self.bridge.status_info()
                pkg = str(info.get("foreground_package") or "").strip()
                if pkg:
                    return pkg
            except Exception:
                pass
        if self.active_adb_serial():
            try:
                proc = self.run_adb(["shell", "dumpsys", "window"], timeout=10)
                for pattern in (
                    r"mCurrentFocus=Window\{[^\}]+\s+([^\s\}]+)\}",
                    r"mFocusedApp=[^\n]*ActivityRecord\{[^\}]+\s+([^\s\}]+)",
                ):
                    m = re.search(pattern, proc.stdout)
                    if m:
                        return m.group(1)
            except Exception:
                pass
        return "unknown"

    def _parse_bridge_nodes(self, payload: dict[str, Any], *, only_interactive: bool = False) -> list[UiElement]:
        raw_nodes = payload.get("nodes") or payload.get("elements") or []
        if not isinstance(raw_nodes, list):
            return []
        elements: list[UiElement] = []
        next_id = 1
        for item in raw_nodes:
            if not isinstance(item, dict):
                continue
            bounds_raw = item.get("bounds")
            x1 = y1 = x2 = y2 = 0
            if isinstance(bounds_raw, list) and len(bounds_raw) == 4:
                x1, y1, x2, y2 = (int(bounds_raw[i]) for i in range(4))
            elif isinstance(bounds_raw, dict):
                x1 = int(bounds_raw.get("left", 0))
                y1 = int(bounds_raw.get("top", 0))
                x2 = int(bounds_raw.get("right", 0))
                y2 = int(bounds_raw.get("bottom", 0))
            elif isinstance(bounds_raw, str):
                m = self._BOUNDS_RE.match(bounds_raw)
                if m:
                    x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))

            if x2 <= x1 or y2 <= y1:
                continue

            text = str(item.get("text") or "").strip()
            desc = str(item.get("content_desc") or item.get("desc") or "").strip()
            res_id = str(item.get("resource_id") or item.get("id_name") or "").strip()
            cls_name = str(item.get("class_name") or item.get("class") or "").strip()
            clickable = bool(item.get("clickable", False))
            scrollable = bool(item.get("scrollable", False))
            focused = bool(item.get("focused", False))
            editable = bool(item.get("editable", False)) or ("EditText" in cls_name or "AutoComplete" in cls_name)

            is_interactive = clickable or scrollable or editable or focused
            has_label = bool(text or desc)
            if only_interactive and not is_interactive:
                continue
            if not (is_interactive or has_label):
                continue

            cx = int(item.get("center_x") if item.get("center_x") is not None else (x1 + x2) // 2)
            cy = int(item.get("center_y") if item.get("center_y") is not None else (y1 + y2) // 2)
            elements.append(
                UiElement(
                    element_id=next_id,
                    text=text,
                    content_desc=desc,
                    resource_id=res_id,
                    class_name=cls_name,
                    center_x=cx,
                    center_y=cy,
                    bounds=(x1, y1, x2, y2),
                    clickable=clickable,
                    scrollable=scrollable,
                    editable=editable,
                    focused=focused,
                    node_id=str(item.get("node_id") or ""),
                )
            )
            next_id += 1
            if len(elements) >= 60:
                break
        return elements

    def dump_ui(self, *, only_interactive: bool = False, cancel_event: threading.Event | None = None) -> dict[str, Any]:
        if self.has_bridge():
            try:
                bdata = self.bridge.dump_ui(only_interactive=only_interactive)
                if isinstance(bdata.get("ui"), str) and bdata["ui"].strip():
                    w, h = self.get_screen_size()
                    app_name = str(bdata.get("app") or bdata.get("package") or self.get_foreground_app())
                    return {
                        "via": "bridge",
                        "screen": str(bdata.get("screen") or f"{w}x{h}"),
                        "app": app_name,
                        "legend": "id:[c=click,in=input,sc=scroll] \"text\" #res @x,y",
                        "ui": bdata["ui"],
                    }
                elements = self._parse_bridge_nodes(bdata, only_interactive=only_interactive)
                if elements:
                    self.last_ui_elements = {e.element_id: e for e in elements}
                    w, h = self.get_screen_size()
                    app_name = str(bdata.get("app") or bdata.get("package") or self.get_foreground_app())
                    return {
                        "via": "bridge",
                        "screen": str(bdata.get("screen") or f"{w}x{h}"),
                        "app": app_name,
                        "legend": "id:[c=click,in=input,sc=scroll] \"text\" #res @x,y",
                        "ui": "\n".join(e.to_compact_line() for e in elements),
                    }
            except Exception:
                pass

        remote_path = "/sdcard/gemphone_ui.xml"
        dump_proc = self.run_adb(["shell", "uiautomator", "dump", remote_path], timeout=20, cancel_event=cancel_event)
        if dump_proc.returncode != 0:
            raise RuntimeError(f"uiautomator dump failed: {(dump_proc.stderr or dump_proc.stdout).strip()}")

        cat_proc = self.run_adb(["shell", "cat", remote_path], timeout=12, cancel_event=cancel_event)
        xml_raw = cat_proc.stdout.strip()
        if not xml_raw.startswith("<"):
            xml_start = xml_raw.find("<?xml")
            if xml_start != -1:
                xml_raw = xml_raw[xml_start:]
        if not xml_raw:
            raise RuntimeError("Пустой XML от uiautomator. Попробуйте adb_screenshot.")

        root = ET.fromstring(xml_raw)
        elements = []
        next_id = 1

        for node in root.iter("node"):
            attrib = node.attrib
            m = self._BOUNDS_RE.match(attrib.get("bounds", ""))
            if not m:
                continue
            x1, y1, x2, y2 = (int(m.group(i)) for i in range(1, 5))
            if x2 <= x1 or y2 <= y1:
                continue

            text = (attrib.get("text") or "").strip()
            desc = (attrib.get("content-desc") or "").strip()
            res_id = (attrib.get("resource-id") or "").strip()
            cls_name = (attrib.get("class") or "").strip()
            clickable = attrib.get("clickable") == "true"
            scrollable = attrib.get("scrollable") == "true"
            focused = attrib.get("focused") == "true"
            editable = "EditText" in cls_name or "AutoComplete" in cls_name

            is_interactive = clickable or scrollable or editable or focused
            has_label = bool(text or desc)

            if only_interactive and not is_interactive:
                continue
            if not (is_interactive or has_label):
                continue

            elements.append(
                UiElement(
                    element_id=next_id,
                    text=text,
                    content_desc=desc,
                    resource_id=res_id,
                    class_name=cls_name,
                    center_x=(x1 + x2) // 2,
                    center_y=(y1 + y2) // 2,
                    bounds=(x1, y1, x2, y2),
                    clickable=clickable,
                    scrollable=scrollable,
                    editable=editable,
                    focused=focused,
                )
            )
            next_id += 1
            if len(elements) >= 60:
                break

        self.last_ui_elements = {e.element_id: e for e in elements}
        w, h = self.get_screen_size()
        return {
            "via": "adb",
            "screen": f"{w}x{h}",
            "app": self.get_foreground_app(),
            "legend": "id:[c=click,in=input,sc=scroll] \"text\" #res @x,y",
            "ui": "\n".join(e.to_compact_line() for e in elements),
        }

    def capture_screenshot(
        self,
        *,
        save_path: Path | None = None,
        max_dim: int | None = None,
        quality: int | None = None,
        hq: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> tuple[dict[str, Any], ImageAttachment]:
        via = "adb"
        raw_bytes: bytes = b""
        mime_type = "image/jpeg"

        if self.has_bridge():
            try:
                raw_bytes, mime_type = self.bridge.capture_screenshot_bytes()
                via = "bridge"
            except Exception:
                raw_bytes = b""

        if not raw_bytes:
            proc = self.run_adb(["exec-out", "screencap", "-p"], timeout=20, binary=True, cancel_event=cancel_event)
            raw_bytes = proc.stdout
            mime_type = "image/png"
            via = "adb"
            if proc.returncode != 0 or len(raw_bytes) < 100:
                raise RuntimeError("Не удалось снять скриншот (FLAG_SECURE, нет доступа Bridge или обрыв ADB).")

        target_dim = SCREENSHOT_HQ_MAX_DIM if hq else (max_dim or self.screenshot_max_dim)
        target_qual = 92 if hq else (quality or self.screenshot_quality)

        out_bytes = raw_bytes
        dimensions = "original"

        if HAS_PIL and Image is not None:
            try:
                img = Image.open(io.BytesIO(raw_bytes))
                orig_w, orig_h = img.size
                if max(orig_w, orig_h) > target_dim:
                    resampling = getattr(Image, "Resampling", Image).LANCZOS
                    img.thumbnail((target_dim, target_dim), resample=resampling)
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                if ImageEnhance is not None:
                    img = ImageEnhance.Sharpness(img).enhance(1.25)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=target_qual, optimize=True, subsampling=0)
                out_bytes = buf.getvalue()
                mime_type = "image/jpeg"
                dimensions = f"{img.size[0]}x{img.size[1]}"
            except Exception:
                out_bytes = raw_bytes

        saved_to = ""
        if save_path is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(out_bytes)
            saved_to = str(save_path)

        b64 = base64.b64encode(out_bytes).decode("ascii")
        meta = {
            "ok": True,
            "via": via,
            "app": self.get_foreground_app(),
            "dim": dimensions,
            "kb": round(len(out_bytes) / 1024, 1),
            "saved": saved_to or None,
        }
        return meta, ImageAttachment(mime_type=mime_type, data_b64=b64)

    def tap(
        self,
        *,
        element_id: int | None = None,
        x: int | None = None,
        y: int | None = None,
        long_press: bool = False,
        duration_ms: int = 700,
    ) -> dict[str, Any]:
        label = ""
        node_id = ""
        if element_id is not None:
            elem = self.last_ui_elements.get(element_id)
            if elem is None:
                raise ValueError(f"Элемент id={element_id} не найден. Сначала вызовите adb_ui_dump.")
            x, y = elem.center_x, elem.center_y
            node_id = elem.node_id
            label = elem.text or elem.content_desc or elem.resource_id

        if x is None or y is None:
            raise ValueError("Укажите element_id или x, y.")

        dur = max(300, min(int(duration_ms), 4000))
        if self.has_bridge():
            try:
                bres = self.bridge.tap(x=x, y=y, node_id=node_id, long_press=long_press, duration_ms=dur)
                if bres.get("ok", True):
                    action = f"long_press({dur}ms)" if long_press else "tap"
                    return {"ok": True, "via": "bridge", "action": action, "x": x, "y": y, "label": label or None}
            except Exception:
                pass

        if long_press:
            self.run_adb(["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(dur)])
            action = f"long_press({dur}ms)"
        else:
            self.run_adb(["shell", "input", "tap", str(x), str(y)])
            action = "tap"

        return {"ok": True, "via": "adb", "action": action, "x": x, "y": y, "label": label or None}

    def swipe(
        self,
        *,
        direction: str | None = None,
        x1: int | None = None,
        y1: int | None = None,
        x2: int | None = None,
        y2: int | None = None,
        duration_ms: int = 320,
    ) -> dict[str, Any]:
        w, h = self.get_screen_size()
        if direction:
            d = direction.strip().lower()
            if d == "down":
                x1, y1, x2, y2 = w // 2, int(h * 0.78), w // 2, int(h * 0.25)
            elif d == "up":
                x1, y1, x2, y2 = w // 2, int(h * 0.25), w // 2, int(h * 0.78)
            elif d == "left":
                x1, y1, x2, y2 = int(w * 0.85), h // 2, int(w * 0.15), h // 2
            elif d == "right":
                x1, y1, x2, y2 = int(w * 0.15), h // 2, int(w * 0.85), h // 2
            else:
                raise ValueError("direction: down, up, left, right.")

        if None in (x1, y1, x2, y2):
            raise ValueError("Укажите direction или x1, y1, x2, y2.")

        dur = max(50, min(int(duration_ms), 3000))
        if self.has_bridge():
            try:
                bres = self.bridge.swipe(x1=int(x1), y1=int(y1), x2=int(x2), y2=int(y2), duration_ms=dur)
                if bres.get("ok", True):
                    return {"ok": True, "via": "bridge", "from": [x1, y1], "to": [x2, y2]}
            except Exception:
                pass

        self.run_adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(dur)])
        return {"ok": True, "via": "adb", "from": [x1, y1], "to": [x2, y2]}

    def type_text(self, text: str, *, press_enter: bool = False) -> dict[str, Any]:
        bridge_error: str = ""
        bridge_warnings: list[str] = []
        if self.has_bridge():
            try:
                bres = self.bridge.type_text(text, press_enter=press_enter)
                bridge_warnings = [str(w) for w in (bres.get("warnings") or [])]
                if bres.get("ok", False):
                    res = {"ok": True, "via": "bridge", "method": "accessibility", "chars": len(text)}
                    if bridge_warnings:
                        res["warnings"] = bridge_warnings
                    return res
                bridge_error = str(bres.get("reason") or "ACTION_SET_TEXT отклонён")
            except Exception as exc:
                bridge_error = f"bridge error: {exc}"

        if bridge_error and (not self.has_adb() or self.active_adb_serial() is None):
            raise RuntimeError(
                f"Ввод текста не удался. Bridge: {bridge_error}. "
                "ADB не подключён (нужен для фолбэка) — включите беспроводную отладку и /adb. "
                "Если поле кастомное (WebView/Flutter), используйте adb_tap по полю + вставку из буфера."
            )

        is_simple_ascii = bool(re.fullmatch(r"[A-Za-z0-9._:/@+-]+", text))
        method = "adb_input"

        if is_simple_ascii:
            self.run_adb(["shell", "input", "text", text])
        elif shutil.which("termux-clipboard-set") is not None:
            self.run_termux_cmd("termux-clipboard-set", stdin_text=text)
            time.sleep(0.15)
            self.run_adb(["shell", "input", "keyevent", "279"])
            method = "clipboard_paste"
        else:
            escaped = text.replace(" ", "%s").replace("'", "\\'").replace('"', '\\"')
            self.run_adb(["shell", "input", "text", escaped])
            method = "adb_fallback"

        if press_enter:
            time.sleep(0.1)
            self.run_adb(["shell", "input", "keyevent", "KEYCODE_ENTER"])

        return {"ok": True, "via": "adb", "method": method, "chars": len(text)}

    def keyevent(self, key: str) -> dict[str, Any]:
        upper = key.strip().upper()
        keycode = ANDROID_KEYEVENTS.get(upper, upper)
        if self.has_bridge():
            global_map = {
                "KEYCODE_BACK": "BACK",
                "KEYCODE_HOME": "HOME",
                "KEYCODE_APP_SWITCH": "RECENTS",
                "KEYCODE_POWER": "POWER_DIALOG",
                "KEYCODE_NOTIFICATION": "NOTIFICATIONS",
            }
            g_act = global_map.get(keycode)
            if g_act:
                try:
                    bres = self.bridge.global_action(g_act)
                    if bres.get("ok", True):
                        return {"ok": True, "via": "bridge", "keycode": keycode}
                except Exception:
                    pass

        self.run_adb(["shell", "input", "keyevent", keycode])
        return {"ok": True, "via": "adb", "keycode": keycode}

    def app_control(
        self,
        action: str,
        *,
        package: str = "",
        query: str = "",
        url: str = "",
    ) -> dict[str, Any]:
        act = action.strip().lower()
        if act == "current":
            return {"app": self.get_foreground_app()}
        if act == "list":
            if shutil.which("pm") is not None and not self.active_adb_serial():
                proc = subprocess.run(["pm", "list", "packages", "-3"], capture_output=True, text=True, timeout=12)
                pkgs = [ln.replace("package:", "").strip() for ln in proc.stdout.splitlines() if ln.startswith("package:")]
            else:
                proc = self.run_adb(["shell", "pm", "list", "packages", "-3"], timeout=12)
                pkgs = [ln.replace("package:", "").strip() for ln in proc.stdout.splitlines() if ln.startswith("package:")]
            if query:
                q = query.lower()
                pkgs = [p for p in pkgs if q in p.lower()]
            pkgs.sort()
            return {"count": len(pkgs), "packages": pkgs[:60]}
        if act == "launch":
            if not package:
                raise ValueError("Укажите package.")
            if self.active_adb_serial():
                proc = self.run_adb(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=12)
                return {"ok": proc.returncode == 0, "via": "adb", "package": package}
            if shutil.which("monkey") is not None:
                proc = subprocess.run(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], capture_output=True, text=True, timeout=12)
                return {"ok": proc.returncode == 0, "via": "shell", "package": package}
            raise RuntimeError("Для запуска приложений подключите ADB (/adb).")
        if act == "stop":
            if not package:
                raise ValueError("Укажите package.")
            self.run_adb(["shell", "am", "force-stop", package], timeout=10)
            return {"ok": True, "via": "adb", "stopped": package}
        if act == "open_url":
            if not url:
                raise ValueError("Укажите url.")
            if self.active_adb_serial():
                self.run_adb(["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url])
                return {"ok": True, "via": "adb", "url": url}
            if shutil.which("termux-open-url") is not None:
                self.run_termux_cmd("termux-open-url", [url])
                return {"ok": True, "via": "termux-api", "url": url}
            raise RuntimeError("Не удалось открыть URL.")
        raise ValueError("action: current, list, launch, stop, open_url.")


# ---------------------------------------------------------------------------
# Единый реестр инструментов с профилями (/mode)
# ---------------------------------------------------------------------------


@dataclass
class ApprovalPolicy:
    auto_approve_moderate: bool = False
    auto_approve_dangerous: bool = False
    non_interactive: bool = False


PHONE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "adb_status",
        "adb_ui_dump",
        "adb_screenshot",
        "adb_tap",
        "adb_swipe",
        "adb_type_text",
        "adb_keyevent",
        "adb_app_control",
        "phone_sensors",
        "phone_controls",
        "phone_notifications",
        "phone_clipboard",
        "phone_comms",
        "phone_media",
        "memory",
    }
)

CODE_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "list_files",
        "read_file",
        "search_text",
        "edit_file",
        "write_file",
        "run_command",
        "web_fetch",
        "memory",
    }
)


class AgentToolkit:
    """Набор инструментов управления смартфоном, файлами и вебом."""

    def __init__(
        self,
        workspace: Path,
        phone: PhoneController,
        memory: MemoryStore,
        ui: ConsoleUI,
        policy: ApprovalPolicy,
        mode: ToolMode = "all",
        notifier: PhoneNotifier | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.phone = phone
        self.memory = memory
        self.ui = ui
        self.policy = policy
        self.mode: ToolMode = mode
        self.notifier = notifier
        self.active_monitor: CancellationMonitor | None = None
        self._dispatch: dict[str, Callable[[dict[str, Any]], ToolExecutionResult]] = {
            "adb_status": self._tool_adb_status,
            "adb_ui_dump": self._tool_adb_ui_dump,
            "adb_screenshot": self._tool_adb_screenshot,
            "adb_tap": self._tool_adb_tap,
            "adb_swipe": self._tool_adb_swipe,
            "adb_type_text": self._tool_adb_type_text,
            "adb_keyevent": self._tool_adb_keyevent,
            "adb_app_control": self._tool_adb_app_control,
            "phone_sensors": self._tool_phone_sensors,
            "phone_controls": self._tool_phone_controls,
            "phone_notifications": self._tool_phone_notifications,
            "phone_clipboard": self._tool_phone_clipboard,
            "phone_comms": self._tool_phone_comms,
            "phone_media": self._tool_phone_media,
            "list_files": self._tool_list_files,
            "read_file": self._tool_read_file,
            "search_text": self._tool_search_text,
            "edit_file": self._tool_edit_file,
            "write_file": self._tool_write_file,
            "run_command": self._tool_run_command,
            "web_fetch": self._tool_web_fetch,
            "memory": self._tool_memory,
        }

    def tool_specs(self) -> list[dict[str, Any]]:
        if self.mode == "chat":
            return []

        all_specs: list[dict[str, Any]] = [
            {
                "name": "adb_status",
                "description": "Screen control status (AccessibilityService Bridge / ADB), screen size, foreground app.",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "adb_ui_dump",
                "description": "Dump Android UI elements with id and x,y coords.",
                "parameters": {
                    "type": "object",
                    "properties": {"only_interactive": {"type": "boolean"}},
                },
            },
            {
                "name": "adb_screenshot",
                "description": "Capture phone screenshot for visual inspection (set hq=true to read small text clearly).",
                "parameters": {
                    "type": "object",
                    "properties": {"save_as": {"type": "string"}, "hq": {"type": "boolean"}},
                },
            },
            {
                "name": "adb_tap",
                "description": "Tap or long-press by element_id (from adb_ui_dump) or x,y.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "element_id": {"type": "integer"},
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "long_press": {"type": "boolean"},
                    },
                },
            },
            {
                "name": "adb_swipe",
                "description": "Swipe screen by direction (down/up/left/right) or x1,y1,x2,y2.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": ["down", "up", "left", "right"]},
                        "x1": {"type": "integer"},
                        "y1": {"type": "integer"},
                        "x2": {"type": "integer"},
                        "y2": {"type": "integer"},
                        "duration_ms": {"type": "integer"},
                    },
                },
            },
            {
                "name": "adb_type_text",
                "description": "Type text (supports Cyrillic/Unicode) into focused field.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "press_enter": {"type": "boolean"}},
                    "required": ["text"],
                },
            },
            {
                "name": "adb_keyevent",
                "description": "Press Android key: HOME, BACK, RECENTS, ENTER, DEL, VOLUME_UP/DOWN, MEDIA_PLAY_PAUSE.",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
            {
                "name": "adb_app_control",
                "description": "Manage Android apps: current, list, launch, stop, open_url.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["current", "list", "launch", "stop", "open_url"]},
                        "package": {"type": "string"},
                        "query": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_sensors",
                "description": "Termux:API sensors: battery, wifi, wifi_scan, location, volume.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["battery", "wifi", "wifi_scan", "location", "volume"]},
                        "provider": {"type": "string", "enum": ["network", "gps", "passive"]},
                    },
                    "required": ["kind"],
                },
            },
            {
                "name": "phone_controls",
                "description": "Termux:API hardware control: torch(on/off), brightness(0..255/auto), volume, vibrate(ms), toast, open_url.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["torch", "brightness", "volume", "vibrate", "toast", "open_url"]},
                        "value": {"type": "string"},
                        "stream": {"type": "string", "enum": ["music", "ring", "alarm", "notification", "call", "system"]},
                    },
                    "required": ["action", "value"],
                },
            },
            {
                "name": "phone_notifications",
                "description": "Android notifications: list, send, remove.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "send", "remove"]},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "notif_id": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_clipboard",
                "description": "Get or set Android clipboard.",
                "parameters": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": ["get", "set"]}, "text": {"type": "string"}},
                    "required": ["action"],
                },
            },
            {
                "name": "phone_comms",
                "description": "Contacts, SMS, calls (send_sms/call ask confirmation).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search_contacts", "list_sms", "send_sms", "call"]},
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                        "number": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "phone_media",
                "description": "TTS speak, camera photo, or mic recording.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["tts_speak", "camera_photo", "mic_record_start", "mic_record_stop"]},
                        "text": {"type": "string"},
                        "camera_id": {"type": "integer"},
                        "output_file": {"type": "string"},
                        "duration_sec": {"type": "integer"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "list_files",
                "description": "List files in workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "max_depth": {"type": "integer"}},
                },
            },
            {
                "name": "read_file",
                "description": "Read text file lines.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "search_text",
                "description": "Search text/regex in workspace files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string"},
                        "is_regex": {"type": "boolean"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "edit_file",
                "description": "Replace unique old_text with new_text in a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "write_file",
                "description": "Create or overwrite a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
            {
                "name": "run_command",
                "description": "Run Termux shell command (asks confirmation).",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                    "required": ["command"],
                },
            },
            {
                "name": "web_fetch",
                "description": "Fetch webpage/API URL as plain text.",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
            {
                "name": "memory",
                "description": "Persistent key-value memory across sessions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "set", "delete"]},
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
        ]

        if self.mode == "phone":
            return [s for s in all_specs if s["name"] in PHONE_TOOLS]
        if self.mode == "code":
            return [s for s in all_specs if s["name"] in CODE_TOOLS]
        return all_specs

    def execute(self, name: str, args: dict[str, Any] | None) -> ToolExecutionResult:
        handler = self._dispatch.get(name)
        if handler is None:
            return ToolExecutionResult({"error": f"Неизвестный инструмент: {name}"})
        safe_args = args if isinstance(args, dict) else {}
        try:
            return handler(safe_args)
        except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, RequestCancelledError) as exc:
            return ToolExecutionResult({"error": str(exc)})
        except Exception as exc:
            return ToolExecutionResult({"error": f"Ошибка {name}: {exc}"})

    def _confirm(self, summary: str, *, risk: RiskLevel) -> tuple[bool, str]:
        if risk == "safe":
            return True, ""
        if risk == "moderate" and self.policy.auto_approve_moderate:
            return True, ""
        if risk == "dangerous" and self.policy.auto_approve_dangerous:
            return True, ""
        if self.policy.non_interactive:
            return False, "Отклонено: неинтерактивный режим без --yolo."

        title = "Опасное действие" if risk == "dangerous" else "Подтверждение"
        all_lbl = "разрешать все опасные в сессии" if risk == "dangerous" else "разрешать все правки/тапы в сессии"
        self.ui.panel(
            title,
            f"{summary}\n\n"
            f"  [1 / y] Да, выполнить\n"
            f"  [2 / a] Да, и {all_lbl}\n"
            f"  [3 / n] Нет, отменить",
            style="red" if risk == "dangerous" else "yellow",
        )
        if self.notifier is not None:
            self.notifier.notify_attention(summary)
        try:
            if self.active_monitor is not None:
                with self.active_monitor.suspend():
                    ans = input("  ❯ ").strip().lower()
            else:
                ans = input("  ❯ ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False, "Отменено пользователем."

        if ans in ("1", "y", "yes", "д", "да"):
            return True, ""
        if ans in ("2", "a", "always", "в", "всегда"):
            if risk == "dangerous":
                self.policy.auto_approve_dangerous = True
            else:
                self.policy.auto_approve_moderate = True
            self.ui.dim("  ⎿ Автоподтверждение включено до конца сессии.")
            return True, ""
        return False, f"Пользователь отклонил действие ('{ans or 'n'}')."

    def _resolve_safe_path(self, raw_path: str) -> Path:
        cleaned = raw_path.strip()
        if not cleaned:
            raise ValueError("Путь не может быть пустым.")
        candidate = (self.workspace / cleaned).resolve()
        if not candidate.is_relative_to(self.workspace):
            raise ValueError(f"Путь '{raw_path}' выходит за пределы рабочей папки {self.workspace}")
        return candidate

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, target)
        except OSError:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            target.write_text(content, encoding="utf-8")

    # --- Инструменты экрана и ввода (Bridge / ADB) ---

    def _tool_adb_status(self, _args: dict[str, Any]) -> ToolExecutionResult:
        bridge_ok = self.phone.has_bridge(force=True)
        active = self.phone.active_adb_serial(force=True)
        info: dict[str, Any] = {
            "bridge_connected": bridge_ok,
            "bridge_unauthorized": self.phone.bridge._last_unauthorized,
            "adb_installed": self.phone.has_adb(),
            "adb_connected": active is not None,
            "device": active,
            "transport": self.phone.transport_badge(force=False),
        }
        if bridge_ok or active:
            w, h = self.phone.get_screen_size()
            info["screen"] = f"{w}x{h}"
            info["app"] = self.phone.get_foreground_app()
        return ToolExecutionResult(info)

    def _tool_adb_ui_dump(self, args: dict[str, Any]) -> ToolExecutionResult:
        cancel_ev = self.active_monitor.event if self.active_monitor is not None else None
        return ToolExecutionResult(
            self.phone.dump_ui(only_interactive=bool(args.get("only_interactive", False)), cancel_event=cancel_ev)
        )

    def _tool_adb_screenshot(self, args: dict[str, Any]) -> ToolExecutionResult:
        save_as = str(args.get("save_as", "")).strip()
        save_path = self._resolve_safe_path(save_as) if save_as else None
        hq = bool(args.get("hq", False))
        cancel_ev = self.active_monitor.event if self.active_monitor is not None else None
        meta, image = self.phone.capture_screenshot(save_path=save_path, hq=hq, cancel_event=cancel_ev)
        return ToolExecutionResult(meta, image=image)

    def _tool_adb_tap(self, args: dict[str, Any]) -> ToolExecutionResult:
        el_id, x, y = args.get("element_id"), args.get("x"), args.get("y")
        lp = bool(args.get("long_press", False))
        desc = f"id={el_id}" if el_id is not None else f"({x},{y})"
        ok, reason = self._confirm(f"{'Долгое нажатие' if lp else 'Тап'} {desc}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(
            self.phone.tap(
                element_id=int(el_id) if el_id is not None else None,
                x=int(x) if x is not None else None,
                y=int(y) if y is not None else None,
                long_press=lp,
            )
        )

    def _tool_adb_swipe(self, args: dict[str, Any]) -> ToolExecutionResult:
        direction = args.get("direction")
        desc = f"{direction}" if direction else f"({args.get('x1')},{args.get('y1')})->({args.get('x2')},{args.get('y2')})"
        ok, reason = self._confirm(f"Свайп: {desc}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(
            self.phone.swipe(
                direction=str(direction) if direction else None,
                x1=int(args["x1"]) if args.get("x1") is not None else None,
                y1=int(args["y1"]) if args.get("y1") is not None else None,
                x2=int(args["x2"]) if args.get("x2") is not None else None,
                y2=int(args["y2"]) if args.get("y2") is not None else None,
                duration_ms=int(args.get("duration_ms", 320)),
            )
        )

    def _tool_adb_type_text(self, args: dict[str, Any]) -> ToolExecutionResult:
        text = str(args.get("text", ""))
        ok, reason = self._confirm(f"Ввести текст: «{text[:60]}»", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(self.phone.type_text(text, press_enter=bool(args.get("press_enter", False))))

    def _tool_adb_keyevent(self, args: dict[str, Any]) -> ToolExecutionResult:
        key = str(args.get("key", ""))
        ok, reason = self._confirm(f"Кнопка Android: {key}", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(self.phone.keyevent(key))

    def _tool_adb_app_control(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        package = str(args.get("package", "")).strip()
        query = str(args.get("query", "")).strip()
        url = str(args.get("url", "")).strip()
        if action == "open_url":
            ok, reason = self._confirm(f"Открыть URL: {url}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
        elif action in ("launch", "stop"):
            ok, reason = self._confirm(f"Действие с приложением ({action}): {package}", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
        return ToolExecutionResult(self.phone.app_control(action, package=package, query=query, url=url))

    # --- Termux:API инструменты ---

    def _tool_phone_sensors(self, args: dict[str, Any]) -> ToolExecutionResult:
        kind = str(args.get("kind", "")).lower()
        if kind == "battery":
            return ToolExecutionResult(self.phone.run_termux_cmd("termux-battery-status", parse_json=True))
        if kind == "wifi":
            return ToolExecutionResult(self.phone.run_termux_cmd("termux-wifi-connectioninfo", parse_json=True))
        if kind == "wifi_scan":
            nets = self.phone.run_termux_cmd("termux-wifi-scaninfo", parse_json=True, timeout=20)
            return ToolExecutionResult({"networks": nets[:15] if isinstance(nets, list) else nets})
        if kind == "location":
            prov = str(args.get("provider", "network")).lower()
            loc = self.phone.run_termux_cmd("termux-location", ["-p", prov], parse_json=True, timeout=25)
            return ToolExecutionResult(loc if isinstance(loc, dict) else {"location": loc})
        if kind == "volume":
            return ToolExecutionResult({"streams": self.phone.run_termux_cmd("termux-volume", parse_json=True)})
        return ToolExecutionResult({"error": f"Неизвестный сенсор: {kind}"})

    def _tool_phone_controls(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        value = str(args.get("value", "")).strip()
        stream = str(args.get("stream", "music")).lower()
        risk: RiskLevel = "dangerous" if action == "open_url" else "moderate"
        ok, reason = self._confirm(f"Телефон: {action} = {value}", risk=risk)
        if not ok:
            return ToolExecutionResult({"rejected": reason})

        if action == "torch":
            val = "on" if value.lower() in ("on", "1", "true", "вкл") else "off"
            self.phone.run_termux_cmd("termux-torch", [val])
            return ToolExecutionResult({"ok": True, "torch": val})
        if action == "brightness":
            self.phone.run_termux_cmd("termux-brightness", [value])
            return ToolExecutionResult({"ok": True, "brightness": value})
        if action == "volume":
            self.phone.run_termux_cmd("termux-volume", [stream, value])
            return ToolExecutionResult({"ok": True, "stream": stream, "volume": value})
        if action == "vibrate":
            ms = str(max(50, min(int(value or "300"), 5000)))
            self.phone.run_termux_cmd("termux-vibrate", ["-d", ms])
            return ToolExecutionResult({"ok": True, "ms": int(ms)})
        if action == "toast":
            self.phone.run_termux_cmd("termux-toast", [value])
            return ToolExecutionResult({"ok": True})
        if action == "open_url":
            self.phone.run_termux_cmd("termux-open-url", [value])
            return ToolExecutionResult({"ok": True, "url": value})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    def _tool_phone_notifications(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "list":
            items = self.phone.run_termux_cmd("termux-notification-list", parse_json=True)
            if isinstance(items, list):
                compact = [
                    {"app": it.get("packageName"), "title": it.get("title"), "text": str(it.get("content", ""))[:120]}
                    for it in items[:20]
                ]
                return ToolExecutionResult({"count": len(items), "items": compact})
            return ToolExecutionResult({"items": items})
        if action == "send":
            title = str(args.get("title", "GemPhone"))
            content = str(args.get("content", ""))
            notif_id = str(args.get("notif_id", "gemphone"))
            ok, reason = self._confirm(f"Уведомление: «{title}: {content[:50]}»", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-notification", ["--id", notif_id, "--title", title, "--content", content])
            return ToolExecutionResult({"ok": True, "id": notif_id})
        if action == "remove":
            notif_id = str(args.get("notif_id", "gemphone"))
            self.phone.run_termux_cmd("termux-notification-remove", [notif_id])
            return ToolExecutionResult({"ok": True})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    def _tool_phone_clipboard(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "get":
            return ToolExecutionResult({"text": truncate_text(self.phone.run_termux_cmd("termux-clipboard-get"), 2000)})
        if action == "set":
            text = str(args.get("text", ""))
            ok, reason = self._confirm(f"В буфер: «{text[:60]}»", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-clipboard-set", stdin_text=text)
            return ToolExecutionResult({"ok": True, "chars": len(text)})
        return ToolExecutionResult({"error": "action: get | set"})

    def _tool_phone_comms(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "search_contacts":
            q = str(args.get("query", "")).lower()
            contacts = self.phone.run_termux_cmd("termux-contact-list", parse_json=True, timeout=20)
            if not isinstance(contacts, list):
                return ToolExecutionResult({"contacts": contacts})
            filtered = [c for c in contacts if not q or q in str(c.get("name", "")).lower() or q in str(c.get("number", "")).lower()]
            return ToolExecutionResult({"count": len(filtered), "contacts": filtered[:20]})
        if action == "list_sms":
            limit = max(1, min(int(args.get("limit", 8)), 20))
            msgs = self.phone.run_termux_cmd("termux-sms-list", ["-l", str(limit)], parse_json=True, timeout=20)
            return ToolExecutionResult({"messages": msgs})
        if action == "send_sms":
            num, txt = str(args.get("number", "")).strip(), str(args.get("text", "")).strip()
            if not num or not txt:
                raise ValueError("Нужны number и text.")
            ok, reason = self._confirm(f"ОТПРАВИТЬ SMS на {num}: «{txt}»", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-sms-send", ["-n", num, txt])
            return ToolExecutionResult({"sent": True, "number": num})
        if action == "call":
            num = str(args.get("number", "")).strip()
            if not num:
                raise ValueError("Нужен number.")
            ok, reason = self._confirm(f"ПОЗВОНИТЬ на {num}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-telephony-call", [num])
            return ToolExecutionResult({"calling": True, "number": num})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    def _tool_phone_media(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "")).lower()
        if action == "tts_speak":
            txt = str(args.get("text", "")).strip()
            ok, reason = self._confirm(f"Озвучить (TTS): «{txt[:60]}»", risk="moderate")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            self.phone.run_termux_cmd("termux-tts-speak", [txt], timeout=30)
            return ToolExecutionResult({"ok": True})
        if action == "camera_photo":
            cam = str(int(args.get("camera_id", 0)))
            rel = str(args.get("output_file", f"photo_{int(time.time())}.jpg"))
            target = self._resolve_safe_path(rel)
            ok, reason = self._confirm(f"Фото с камеры #{cam} -> {rel}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            target.parent.mkdir(parents=True, exist_ok=True)
            self.phone.run_termux_cmd("termux-camera-photo", ["-c", cam, str(target)], timeout=25)
            return ToolExecutionResult({"ok": True, "saved": self._rel(target)})
        if action == "mic_record_start":
            rel = str(args.get("output_file", f"rec_{int(time.time())}.m4a"))
            dur = max(1, min(int(args.get("duration_sec", 10)), 120))
            target = self._resolve_safe_path(rel)
            ok, reason = self._confirm(f"Запись микрофона ({dur}с) -> {rel}", risk="dangerous")
            if not ok:
                return ToolExecutionResult({"rejected": reason})
            target.parent.mkdir(parents=True, exist_ok=True)
            self.phone.run_termux_cmd("termux-microphone-record", ["-f", str(target), "-l", str(dur)])
            return ToolExecutionResult({"recording": True, "file": self._rel(target)})
        if action == "mic_record_stop":
            self.phone.run_termux_cmd("termux-microphone-record", ["-q"])
            return ToolExecutionResult({"stopped": True})
        return ToolExecutionResult({"error": f"Неизвестное действие: {action}"})

    # --- Файлы, терминал, веб и память ---

    def _tool_list_files(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel = str(args.get("path", ".") or ".")
        max_depth = max(1, min(int(args.get("max_depth", 2)), 5))
        root = self._resolve_safe_path(rel)
        if not root.exists():
            return ToolExecutionResult({"error": f"Не найден: {rel}"})
        if root.is_file():
            return ToolExecutionResult({"files": [self._rel(root)]})

        entries: list[str] = []
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack and len(entries) < 120:
            cur, depth = stack.pop()
            try:
                children = sorted(os.scandir(cur), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
            except OSError:
                continue
            subdirs: list[tuple[Path, int]] = []
            for entry in children:
                if entry.name in IGNORED_DIRS:
                    continue
                p = Path(entry.path)
                is_dir = entry.is_dir(follow_symlinks=False)
                entries.append(f"{self._rel(p)}/" if is_dir else self._rel(p))
                if len(entries) >= 120:
                    break
                if is_dir and depth + 1 < max_depth:
                    subdirs.append((p, depth + 1))
            for sub in reversed(subdirs):
                stack.append(sub)
        return ToolExecutionResult({"count": len(entries), "entries": entries})

    def _tool_read_file(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel = str(args.get("path", "")).strip()
        target = self._resolve_safe_path(rel)
        if not target.exists() or not target.is_file():
            return ToolExecutionResult({"error": f"Файл не найден: {rel}"})
        if is_binary_file(target):
            return ToolExecutionResult({"error": f"Бинарный файл: {rel}"})
        start = max(1, int(args.get("start_line", 1)))
        req_end = args.get("end_line")
        end = int(req_end) if req_end is not None else start + 119
        end = min(max(end, start), start + MAX_READ_LINES - 1)
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        numbered = [f"{i:4d}|{ln}" for i, ln in enumerate(lines[start - 1 : end], start=start)]
        return ToolExecutionResult(
            {
                "path": self._rel(target),
                "total": len(lines),
                "range": f"{start}-{min(end, len(lines))}",
                "content": truncate_text("\n".join(numbered)),
            }
        )

    def _tool_search_text(self, args: dict[str, Any]) -> ToolExecutionResult:
        query = str(args.get("query", ""))
        if not query:
            return ToolExecutionResult({"error": "Пустой query."})
        root = self._resolve_safe_path(str(args.get("path", ".") or "."))
        pattern = re.compile(query if args.get("is_regex") else re.escape(query), re.IGNORECASE)
        files: list[Path] = [root] if root.is_file() else []
        if not files:
            stack = [root]
            while stack:
                cur = stack.pop()
                try:
                    with os.scandir(cur) as it:
                        for e in it:
                            if e.name in IGNORED_DIRS:
                                continue
                            p = Path(e.path)
                            if e.is_dir(follow_symlinks=False):
                                stack.append(p)
                            elif e.is_file(follow_symlinks=False):
                                files.append(p)
                except OSError:
                    continue
        matches: list[str] = []
        for fp in sorted(files):
            if len(matches) >= 40:
                break
            try:
                if fp.stat().st_size > 1_000_000 or is_binary_file(fp):
                    continue
                for idx, line in enumerate(fp.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if pattern.search(line):
                        matches.append(f"{self._rel(fp)}:{idx}: {line.strip()[:120]}")
                        if len(matches) >= 40:
                            break
            except OSError:
                continue
        return ToolExecutionResult({"count": len(matches), "matches": matches})

    def _tool_edit_file(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel, old_text, new_text = str(args.get("path", "")).strip(), str(args.get("old_text", "")), str(args.get("new_text", ""))
        target = self._resolve_safe_path(rel)
        if not target.exists() or not target.is_file():
            return ToolExecutionResult({"error": f"Файл не найден: {rel}"})
        original = target.read_text(encoding="utf-8")
        cnt = original.count(old_text)
        if cnt != 1:
            return ToolExecutionResult({"error": f"old_text найден {cnt} раз(а); нужен ровно 1."})
        updated = original.replace(old_text, new_text, 1)
        rel_lbl = self._rel(target)
        self.ui.diff(original, updated, rel_lbl)
        ok, reason = self._confirm(f"Применить правку в {rel_lbl}?", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        self._atomic_write(target, updated)
        return ToolExecutionResult({"ok": True, "path": rel_lbl})

    def _tool_write_file(self, args: dict[str, Any]) -> ToolExecutionResult:
        rel, content = str(args.get("path", "")).strip(), str(args.get("content", ""))
        if not rel:
            return ToolExecutionResult({"error": "Параметр path обязателен."})
        target = self._resolve_safe_path(rel)
        if target.exists() and target.is_dir():
            return ToolExecutionResult({"error": f"Путь является директорией: {rel}"})
        rel_lbl = self._rel(target)
        old_content = target.read_text(encoding="utf-8", errors="replace") if target.exists() and target.is_file() else ""
        self.ui.diff(old_content, content, rel_lbl)
        ok, reason = self._confirm(f"Записать {rel_lbl}?", risk="moderate")
        if not ok:
            return ToolExecutionResult({"rejected": reason})
        self._atomic_write(target, content)
        return ToolExecutionResult({"ok": True, "path": rel_lbl})

    def _tool_run_command(self, args: dict[str, Any]) -> ToolExecutionResult:
        command = str(args.get("command", "")).strip()
        if not command:
            return ToolExecutionResult({"error": "Пустая команда."})
        for bad_pat in DANGEROUS_CMD_PATTERNS:
            if bad_pat.search(command):
                return ToolExecutionResult({"error": "Команда заблокирована фильтром безопасности."})
        timeout = max(1, min(int(args.get("timeout", DEFAULT_CMD_TIMEOUT)), 120))
        ok, reason = self._confirm(f"Выполнить в Termux:\n$ {command}", risk="dangerous")
        if not ok:
            return ToolExecutionResult({"rejected": reason})

        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out_box: list[tuple[str, str]] = []
        err_box: list[BaseException] = []

        def _comm() -> None:
            try:
                o, e = proc.communicate(timeout=timeout)
                out_box.append((o or "", e or ""))
            except BaseException as exc:  # noqa: BLE001
                err_box.append(exc)

        t = threading.Thread(target=_comm, daemon=True)
        t.start()
        while t.is_alive():
            if self.active_monitor is not None and self.active_monitor.is_cancelled():
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                raise RequestCancelledError("Команда отменена пользователем.")
            t.join(timeout=0.05)

        if self.active_monitor is not None and self.active_monitor.is_cancelled():
            raise RequestCancelledError("Команда отменена пользователем.")
        if err_box:
            if isinstance(err_box[0], subprocess.TimeoutExpired):
                return ToolExecutionResult({"error": f"Команда превысила таймаут {timeout} с."})
            raise err_box[0]

        raw_out, raw_err = out_box[0] if out_box else ("", "")
        stdout = truncate_text(raw_out.strip(), MAX_TOOL_OUTPUT_CHARS // 2)
        stderr = truncate_text(raw_err.strip(), MAX_TOOL_OUTPUT_CHARS // 2)
        self.phone.invalidate_adb_cache()
        return ToolExecutionResult({"code": proc.returncode, "stdout": stdout, "stderr": stderr})

    def _tool_web_fetch(self, args: dict[str, Any]) -> ToolExecutionResult:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return ToolExecutionResult({"error": "Нужен http:// или https://"})
        status, ctype, body = _http_get_text(url)
        if "html" in ctype.lower():
            parser = _HtmlTextExtractor()
            parser.feed(body)
            clean_text = parser.get_text()
        else:
            clean_text = body
        return ToolExecutionResult({"status": status, "content": truncate_text(clean_text, 4_000)})

    def _tool_memory(self, args: dict[str, Any]) -> ToolExecutionResult:
        action = str(args.get("action", "list")).lower()
        if action == "list":
            return ToolExecutionResult({"memory": self.memory.data})
        if action == "set":
            k, v = str(args.get("key", "")).strip(), str(args.get("value", "")).strip()
            if not k or not v:
                return ToolExecutionResult({"error": "Нужны key и value."})
            self.memory.set(k, v)
            return ToolExecutionResult({"saved": k})
        if action == "delete":
            k = str(args.get("key", "")).strip()
            return ToolExecutionResult({"deleted": self.memory.delete(k), "key": k})
        return ToolExecutionResult({"error": "action: list | set | delete"})


# ---------------------------------------------------------------------------
# Ядро цикла агента с древовидным выводом и контролем бюджета токенов
# ---------------------------------------------------------------------------


@dataclass
class PhoneAgent:
    client: BaseLLMClient
    toolkit: AgentToolkit
    store: ConfigStore
    memory: MemoryStore
    ui: ConsoleUI
    notifier: PhoneNotifier = field(default_factory=PhoneNotifier)
    max_steps: int = DEFAULT_MAX_STEPS
    turn_token_budget: int = DEFAULT_TURN_TOKEN_BUDGET
    usage: TokenUsage = field(default_factory=TokenUsage)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    last_turn_meta: dict[str, Any] = field(default_factory=dict)

    def clear_history(self) -> None:
        self.client.clear()
        self.transcript.clear()

    def switch_client(self, new_client: BaseLLMClient, *, preserve_context: bool = True) -> None:
        self.client = new_client
        if preserve_context and self.transcript:
            self.client.seed_transcript(self.transcript)

    def _build_system_prompt(self) -> str:
        if not self.memory.data:
            return SYSTEM_PROMPT
        mem_lines = [f"- {k}: {v}" for k, v in self.memory.data.items()]
        return SYSTEM_PROMPT + "\nUser memory:\n" + "\n".join(mem_lines)

    def ask(self, user_prompt: str) -> str:
        marker = self.client.snapshot()
        self.client.add_user_message(user_prompt)
        specs = self.toolkit.tool_specs()
        sys_prompt = self._build_system_prompt()
        turn_tokens = 0
        budget_limit = self.turn_token_budget
        started_at = time.perf_counter()
        self.last_turn_meta = {}
        self.notifier.notify_working(user_prompt)

        monitor = CancellationMonitor()
        self.toolkit.active_monitor = monitor

        def _check_cancel() -> None:
            if monitor.is_cancelled():
                raise RequestCancelledError(monitor.reason or "Отменено пользователем.")

        try:
            with monitor:
                for step in range(1, self.max_steps + 1):
                    _check_cancel()
                    sub = f"шаг {step}/{self.max_steps}"
                    if turn_tokens > 0:
                        sub += f" · {format_tokens(turn_tokens)} ток."
                    with self.ui.thinking("Думаю…", sub):
                        turn = self.client.generate_step(
                            tool_specs=specs,
                            system_instruction=sys_prompt,
                            cancel_event=monitor.event,
                        )
                    _check_cancel()

                    step_total = turn.total_tokens or (turn.prompt_tokens + turn.completion_tokens)
                    turn_tokens += step_total
                    self.usage.add(
                        prompt=turn.prompt_tokens,
                        completion=turn.completion_tokens,
                        total=step_total,
                    )

                    if not turn.tool_calls:
                        elapsed = time.perf_counter() - started_at
                        self.client.finalize_turn()
                        self.transcript.append(("user", user_prompt))
                        if turn.text:
                            self.transcript.append(("assistant", turn.text))
                        self.last_turn_meta = {
                            "status": "done",
                            "steps": step,
                            "tokens": turn_tokens,
                            "elapsed": elapsed,
                        }
                        self.notifier.notify_done(
                            turn.text or "Задача выполнена",
                            steps=step,
                            tokens=turn_tokens,
                            elapsed=elapsed,
                        )
                        if turn.prompt_tokens > 30_000:
                            self.ui.warn(
                                f"  Совет: контекст вырос до {format_tokens(turn.prompt_tokens)} токенов — "
                                "введите /clear, чтобы не переплачивать."
                            )
                        return turn.text

                    if budget_limit > 0 and turn_tokens >= budget_limit and not self.toolkit.policy.non_interactive:
                        self.ui.warn(
                            f"\n⚠️ Лимит экономии: запрос потратил {format_tokens(turn_tokens)} токенов "
                            f"(бюджет: {format_tokens(budget_limit)})."
                        )
                        self.notifier.notify_attention(
                            f"Лимит экономии: {format_tokens(turn_tokens)} ток. Подтвердите продолжение в Termux."
                        )
                        with monitor.suspend():
                            ans = input("  Продолжить ещё на один блок? [y/N] ❯ ").strip().lower()
                        if ans not in ("y", "yes", "д", "да"):
                            elapsed = time.perf_counter() - started_at
                            self.client.finalize_turn()
                            self.last_turn_meta = {
                                "status": "stopped",
                                "steps": step,
                                "tokens": turn_tokens,
                                "elapsed": elapsed,
                            }
                            self.notifier.clear()
                            return "Остановлено по лимиту токенов (/budget)."
                        budget_limit += self.turn_token_budget
                        self.notifier.notify_working(user_prompt)

                    if turn.text:
                        self.ui.dim(turn.text)

                    results: list[tuple[ToolCall, ToolExecutionResult]] = []
                    for call in turn.tool_calls:
                        _check_cancel()
                        t0 = time.perf_counter()
                        with self.ui.running_tool(call.name, call.args):
                            res = self.toolkit.execute(call.name, call.args)
                        dt = time.perf_counter() - t0
                        _check_cancel()
                        status, summary = _summarize_tool_result(call.name, res.payload, dt)
                        if step_total > 0 and call is turn.tool_calls[-1]:
                            summary = f"{summary} · +{format_tokens(step_total)} ток."
                        self.ui.tool_tree_result(call.name, call.args, status, summary)
                        results.append((call, res))

                    self.client.add_tool_results(results)

                elapsed = time.perf_counter() - started_at
                self.client.finalize_turn()
                self.last_turn_meta = {
                    "status": "limit",
                    "steps": self.max_steps,
                    "tokens": turn_tokens,
                    "elapsed": elapsed,
                }
                self.notifier.notify_attention(
                    f"Достигнут лимит шагов ({self.max_steps}). Напишите «продолжай», если нужно закончить."
                )
                return (
                    f"Достигнут лимит шагов ({self.max_steps}, потрачено {format_tokens(turn_tokens)} ток.). "
                    "Напишите «продолжай», если нужно довести задачу до конца."
                )
        except (KeyboardInterrupt, RequestCancelledError) as exc:
            self.client.rollback(marker)
            self.notifier.clear()
            reason = str(exc) if isinstance(exc, RequestCancelledError) and str(exc) else "Запрос отменён"
            self.last_turn_meta = {
                "status": "cancelled",
                "steps": 0,
                "tokens": turn_tokens,
                "elapsed": time.perf_counter() - started_at,
                "reason": reason,
            }
            raise RequestCancelledError(reason) from exc
        except ApiError as exc:
            self.client.rollback(marker)
            self.notifier.notify_error(str(exc))
            raise
        finally:
            self.toolkit.active_monitor = None


# ---------------------------------------------------------------------------
# Интерактивные мастера (включая /login по подписке и /bridge) и CLI
# ---------------------------------------------------------------------------


def ensure_api_key(
    cfg: ProviderConfig,
    store: ConfigStore,
    *,
    cli_key: str | None = None,
    non_interactive: bool = False,
) -> str:
    if cfg.provider_type == "cli" or not cfg.requires_key:
        return ""
    if cli_key and cli_key.strip():
        return cli_key.strip()
    key = cfg.effective_api_key()
    if key:
        return key
    if non_interactive:
        raise SystemExit(f"Ошибка: для '{cfg.name}' не задан API-ключ ({store.path}).")

    print(f"Для провайдера '{cfg.name}' ({cfg.description}) не задан API-ключ.")
    try:
        entered = getpass.getpass("Вставьте API-ключ (скрыт, Enter для сохранения): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nВыход.")
    if not entered:
        raise SystemExit("API-ключ не указан.")
    cfg.api_key = entered
    store.save()
    print(f"✓ Ключ сохранён в {store.path}")
    return entered


def handle_bridge_command(agent: PhoneAgent, arg: str) -> None:
    ui = agent.ui
    bridge = agent.toolkit.phone.bridge
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "token" and rest:
        bridge.save_token(rest)
        agent.toolkit.phone.invalidate_adb_cache()
        ok = bridge.is_available(force=True)
        ui.success(f"✓ Токен Bridge сохранён в {bridge.token_path} ({mask_secret(rest)}).")
        if ok:
            ui.success(f"✓ Мост {bridge.base_url()} отвечает!")
        elif bridge._last_unauthorized:
            ui.warn(
                f"Мост {bridge.base_url()} запущен, но вернул 401 (неверный токен). "
                "Нажмите «2. Скопировать команду для GemPhone» в приложении GemPhone Bridge и вставьте сюда."
            )
        else:
            ui.warn("Мост пока не отвечает: откройте приложение GemPhone Bridge и включите его в Спец. возможностях.")
        return

    if sub == "check":
        ok = bridge.is_available(force=True)
        if ok:
            info = bridge.status_info()
            ui.success(
                f"✓ GemPhone Bridge активен на {bridge.base_url()} "
                f"(экран: {info.get('screen_width', '?')}x{info.get('screen_height', '?')}, "
                f"окно: {info.get('foreground_package', '?')})"
            )
        elif bridge._last_unauthorized:
            ui.warn(
                f"Мост {bridge.base_url()} отвечает, но отклонил токен (401 Unauthorized). "
                "Обновите токен через `/bridge token <токен>` из приложения GemPhone Bridge."
            )
        else:
            ui.warn(f"GemPhone Bridge не отвечает на {bridge.base_url()}.")
        return

    active = bridge.is_available(force=True)
    status_line = "● АКТИВЕН" if active else ("! 401 неверный токен" if bridge._last_unauthorized else "○ не подключён")
    ui.panel(
        "AccessibilityService Bridge (работа без ADB и Wi-Fi)",
        f"Статус      : {status_line}\n"
        f"Адрес       : {bridge.base_url()}\n"
        f"Токен       : {mask_secret(bridge.token)} ({bridge.token_path})\n\n"
        "1. Установите APK GemPhone Bridge (собранный из папки bridge/).\n"
        "2. Включите GemPhone Bridge в Настройки -> Спец. возможности\n"
        "   (на Android 13+ сначала через кнопку 0 и меню ⋮ -> Разрешить огр. настройки).\n"
        "3. Скопируйте в приложении команду `/bridge token <токен>` и вставьте сюда.\n"
        "   Проверка связи: `/bridge check`",
        style="green" if active else "yellow",
    )


def interactive_subscription_login(agent: PhoneAgent, target_name: str = "") -> None:
    ui = agent.ui
    name = target_name.lower().strip()
    if not name:
        ui.panel(
            "Подписки (OAuth без API-ключа)",
            "  /login gemini-sub   — Google / Gemini AI Pro\n"
            "  /login claude-sub   — Claude Pro / Max\n"
            "  /login chatgpt-sub  — ChatGPT Plus / Pro\n\n"
            "Нужен Node.js в Termux: pkg install nodejs -y",
        )
        return

    cfg = agent.store.get(name)
    if cfg is None or cfg.provider_type != "cli":
        ui.warn(f"'{name}' не является CLI-подпиской. Используйте: gemini-sub, claude-sub или chatgpt-sub.")
        return

    binary = cfg.cli_binary()
    hint = SUBSCRIPTION_INSTALL_HINTS.get(name)
    if shutil.which(binary) is None:
        ui.warn(f"Утилита `{binary}` пока не установлена.")
        if shutil.which("npm") is None:
            ui.info("Сначала установите Node.js в Termux: pkg install nodejs -y")
        if hint:
            ui.info(f"Команда установки: {hint[0]}")
            try:
                ans = input("Установить сейчас через npm? [y/N] ❯ ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if ans in ("y", "yes", "д", "да"):
                if shutil.which("npm") is None:
                    ui.error("npm не найден. Выполните `pkg install nodejs -y` и повторите `/login`.")
                    return
                res = subprocess.run(shlex.split(hint[0]))
                if res.returncode != 0:
                    ui.error("Не удалось установить через npm.")
                    return
            else:
                return

    login_cmd = hint[1] if hint else binary
    ui.info(f"Запускаю OAuth (`{login_cmd}`)…")
    try:
        subprocess.run(shlex.split(login_cmd))
    except Exception as exc:
        ui.error(f"Ошибка запуска `{login_cmd}`: {exc}")
        return

    agent.store.active_provider = name
    agent.store.save()
    agent.switch_client(create_llm_client(cfg, ""), preserve_context=True)
    ui.success(f"✓ Подписка '{name}' активирована.")


def print_status_report(agent: PhoneAgent) -> None:
    phone = agent.toolkit.phone
    bridge_ok = phone.has_bridge(force=True)
    active_adb = phone.active_adb_serial(force=True)
    n = agent.notifier
    notify_state = (
        f"ВКЛ (вибр/тост/шторка{', TTS' if n.tts else ''})"
        if n.enabled and n.is_available()
        else ("ВКЛ (только терминал)" if n.enabled else "ВЫКЛ")
    )
    subs_ready = [
        name
        for name in ("gemini-sub", "claude-sub", "chatgpt-sub")
        if (c := agent.store.get(name)) and shutil.which(c.cli_binary()) is not None
    ]
    bridge_status_str = (
        "АКТИВЕН"
        if bridge_ok
        else ("401 неверный токен" if phone.bridge._last_unauthorized else f"нет (токен: {mask_secret(phone.bridge.token)})")
    )
    rows = [
        ["API / Подписка", f"{agent.client.provider_name} ({agent.client.config.provider_type})"],
        ["Модель", f"{agent.client.model} (Vision: {'да' if agent.client.config.supports_vision else 'нет'})"],
        ["Подписки CLI", ", ".join(subs_ready) if subs_ready else "не установлены (/login)"],
        ["Качество скринов", f"{phone.screenshot_max_dim}px, JPEG {phone.screenshot_quality}% (/quality)"],
        ["Режим инструментов", f"{agent.toolkit.mode} ({len(agent.toolkit.tool_specs())} шт.)"],
        ["Бюджет / Шаги", f"{format_tokens(agent.turn_token_budget)} ток. / макс {agent.max_steps} шагов"],
        ["Оповещения (/notify)", notify_state],
        ["Bridge (127.0.0.1:7331)", bridge_status_str],
        ["ADB (резерв)", f"Подключён: {active_adb}" if active_adb else ("Установлен, нет устройств (/adb)" if phone.has_adb() else "НЕ установлен")],
        ["Termux:API", "Доступен" if phone.has_termux_api() else "НЕ найден"],
        ["Библиотеки", f"rich={'OK' if HAS_RICH else '-'}, httpx={'OK' if HAS_HTTPX else '-'}, PIL={'OK' if HAS_PIL else '-'}, ptk={'OK' if HAS_PROMPT_TOOLKIT else '-'}"],
        ["Память фактов", f"{len(agent.memory.data)} записей"],
        ["Рабочая папка", str(agent.toolkit.workspace)],
    ]
    agent.ui.table(f"Диагностика GemPhone {APP_VERSION}", ["Компонент", "Статус"], rows)


def interactive_adb_wizard(agent: PhoneAgent) -> None:
    ui = agent.ui
    phone = agent.toolkit.phone
    if not phone.has_adb():
        ui.error("ADB не установлен: pkg install android-tools")
        return

    ui.panel(
        "Мастер подключения ADB (Wireless Debugging)",
        "1. Включите Wi-Fi и откройте: Настройки -> Для разработчиков -> Отладка по Wi-Fi.\n"
        "2. Откройте Настройки и Termux в разделённом экране.\n"
        "3. Сначала [1] Сопряжение (pair), затем [2] Подключение (connect).",
        style="yellow",
    )
    devs = phone.adb_devices(force=True)
    if devs:
        ui.info("Устройства ADB: " + ", ".join(f"{d['serial']} ({d['state']})" for d in devs))

    print("  [1] Сопряжение (adb pair 127.0.0.1:PORT + код)")
    print("  [2] Подключение (adb connect 127.0.0.1:PORT)")
    print("  [3] Перезапустить ADB-сервер")
    print("  [Enter] Выход")
    try:
        choice = input("  ❯ ").strip()
        if choice == "1":
            port = input("  Порт сопряжения: ").strip()
            code = input("  6-значный код: ").strip()
            if port and code:
                addr = port if ":" in port else f"127.0.0.1:{port}"
                proc = subprocess.run(["adb", "pair", addr, code], capture_output=True, text=True, timeout=15)
                phone.invalidate_adb_cache()
                ui.info((proc.stdout + "\n" + proc.stderr).strip())
        elif choice == "2":
            port = input("  Основной порт: ").strip()
            if port:
                addr = port if ":" in port else f"127.0.0.1:{port}"
                proc = subprocess.run(["adb", "connect", addr], capture_output=True, text=True, timeout=15)
                phone.invalidate_adb_cache()
                ui.info((proc.stdout + "\n" + proc.stderr).strip())
        elif choice == "3":
            subprocess.run(["adb", "kill-server"], capture_output=True, text=True, timeout=10)
            subprocess.run(["adb", "start-server"], capture_output=True, text=True, timeout=10)
            phone.invalidate_adb_cache()
            ui.success("Сервер ADB перезапущен.")
    except (EOFError, KeyboardInterrupt):
        print()


def interactive_add_provider(agent: PhoneAgent) -> None:
    ui = agent.ui
    ui.info("Добавление API-провайдера или CLI-подписки (пустая строка — отмена):")
    try:
        name = re.sub(r"[^a-z0-9_-]", "", input("  1. Короткое имя (напр. dp, nvidia, mycli): ").strip().lower())
        if not name:
            return
        ptype = input("  2. Протокол [openai / gemini / anthropic / cli] (по умолч. openai): ").strip().lower() or "openai"
        if ptype not in ("openai", "gemini", "anthropic", "cli"):
            ui.warn("Поддерживаются только openai, gemini, anthropic, cli.")
            return

        if ptype == "cli":
            cli_cmd = input("  3. Команда CLI (напр. claude -p --output-format json): ").strip()
            if not cli_cmd:
                return
            model = input("  4. Модель (или Enter для default): ").strip() or "default"
            cfg = ProviderConfig(
                name=name,
                provider_type="cli",
                base_url=f"cli://{cli_cmd.split()[0]}",
                cli_command=cli_cmd,
                model=model,
                api_key="",
                requires_key=False,
                supports_vision=False,
                description=f"CLI-подписка ({cli_cmd})",
            )
            agent.store.providers[name] = cfg
            agent.store.active_provider = name
            agent.store.save()
            agent.switch_client(create_llm_client(cfg, ""), preserve_context=True)
            ui.success(f"✓ Подписка '{name}' сохранена и активирована.")
            return

        def_url = (
            "https://generativelanguage.googleapis.com/v1beta/models"
            if ptype == "gemini"
            else "https://api.anthropic.com/v1"
            if ptype == "anthropic"
            else "https://api.openai.com/v1"
        )
        base_url = input(f"  3. Base URL ({def_url}): ").strip() or def_url
        model = input("  4. ID модели: ").strip()
        if not model:
            return
        api_key = getpass.getpass("  5. API-ключ (скрыт; Enter, если локальный без ключа): ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    cfg = ProviderConfig(
        name=name,
        provider_type=ptype,
        base_url=base_url,
        model=model,
        api_key=api_key,
        requires_key=bool(api_key),
        supports_vision=True,
        description=f"Свой API ({base_url})",
    )
    agent.store.providers[name] = cfg
    agent.store.active_provider = name
    agent.store.save()
    agent.switch_client(create_llm_client(cfg, api_key), preserve_context=True)
    ui.success(f"✓ Провайдер '{name}' сохранён и активирован.")


def handle_slash_command(line: str, agent: PhoneAgent) -> bool:
    ui = agent.ui
    parts = line.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/exit", "/quit", "/q"):
        return False

    if cmd == "/help":
        lines = [f"{c:<12} — {d}" for c, d in SLASH_COMMANDS_META]
        ui.panel(f"Команды GemPhone {APP_VERSION}", "\n".join(lines))
        return True

    if cmd == "/bridge":
        handle_bridge_command(agent, arg)
        return True

    if cmd == "/login":
        interactive_subscription_login(agent, arg)
        return True

    if cmd == "/steps":
        if not arg:
            ui.info(f"Максимум шагов на запрос: {agent.max_steps}.")
        else:
            try:
                agent.max_steps = max(1, int(arg))
                ui.success(f"Новый лимит шагов: {agent.max_steps}.")
            except ValueError:
                ui.warn("Укажите целое число, например: /steps 30")
        return True

    if cmd == "/quality":
        phone = agent.toolkit.phone
        sub = arg.lower()
        if not sub:
            ui.info(f"Качество скриншотов: {phone.screenshot_max_dim}px, JPEG {phone.screenshot_quality}%.")
            ui.dim("Пресеты: /quality eco | /quality normal | /quality hd")
        elif sub == "eco":
            phone.screenshot_max_dim, phone.screenshot_quality = 720, 65
            ui.success("Качество скриншотов: ECO (720px, JPEG 65%).")
        elif sub == "normal":
            phone.screenshot_max_dim, phone.screenshot_quality = 1280, 85
            ui.success("Качество скриншотов: NORMAL (1280px, JPEG 85%).")
        elif sub == "hd":
            phone.screenshot_max_dim, phone.screenshot_quality = 1920, 92
            ui.success("Качество скриншотов: HD (1920px, JPEG 92%).")
        else:
            ui.warn("Выберите: /quality eco | /quality normal | /quality hd")
        return True

    if cmd == "/notify":
        n = agent.notifier
        sub = arg.lower()
        if not sub:
            state = "ВКЛ" if n.enabled else "ВЫКЛ"
            tts_state = "ВКЛ" if n.tts else "ВЫКЛ"
            ui.info(f"Оповещения: {state} | TTS: {tts_state} | Termux:API: {'OK' if n.is_available() else 'нет'}")
            ui.dim("Использование: /notify on | /notify off | /notify tts | /notify test")
        elif sub in ("on", "вкл", "1"):
            n.enabled = True
            ui.success("Оповещения ВКЛЮЧЕНЫ.")
        elif sub in ("off", "выкл", "0"):
            n.enabled = False
            n.clear()
            ui.info("Оповещения ВЫКЛЮЧЕНЫ.")
        elif sub == "tts":
            n.tts = not n.tts
            ui.info(f"Голосовое «Готово» (TTS): {'ВКЛ' if n.tts else 'ВЫКЛ'}")
        elif sub == "test":
            n.notify_done("Тестовое оповещение: ИИ завершил задачу!", steps=1, tokens=1200, elapsed=1.5)
            ui.done_banner(steps=1, tokens=1200, elapsed=1.5, status="done")
        else:
            ui.warn("Варианты: /notify on | /notify off | /notify tts | /notify test")
        return True

    if cmd == "/mode":
        valid: tuple[ToolMode, ...] = ("all", "phone", "code", "chat")
        if not arg:
            ui.info(f"Режим: {agent.toolkit.mode} ({len(agent.toolkit.tool_specs())} инстр.). Доступны: all, phone, code, chat.")
        elif arg.lower() in valid:
            agent.toolkit.mode = arg.lower()  # type: ignore[assignment]
            ui.success(f"Режим переключён на '{agent.toolkit.mode}' ({len(agent.toolkit.tool_specs())} инстр.).")
        else:
            ui.warn("Выберите: /mode all | /mode phone | /mode code | /mode chat")
        return True

    if cmd == "/budget":
        if not arg:
            ui.info(f"Лимит на один запрос: {format_tokens(agent.turn_token_budget)} токенов (0 = выкл).")
        else:
            try:
                val = int(arg.lower().replace("k", "000").replace("к", "000"))
                agent.turn_token_budget = max(0, val)
                ui.success(f"Новый лимит на один запрос: {format_tokens(agent.turn_token_budget)} токенов.")
            except ValueError:
                ui.warn("Укажите число, например: /budget 40000 или /budget 50k")
        return True

    if cmd == "/status":
        print_status_report(agent)
        return True

    if cmd == "/adb":
        interactive_adb_wizard(agent)
        return True

    if cmd == "/screen":
        try:
            dump = agent.toolkit.phone.dump_ui(only_interactive=True)
            via_lbl = dump.get("via", "auto")
            ui.panel(f"Экран ({dump['app']}, {dump['screen']}, {via_lbl})", dump["ui"] or "(пусто)")
        except Exception as exc:
            ui.error(f"Ошибка: {exc}")
        return True

    if cmd in ("/providers", "/p"):
        rows: list[list[str]] = []
        for name, cfg in agent.store.providers.items():
            if cfg.provider_type == "cli":
                bin_name = cfg.cli_binary()
                status_str = f"CLI OK ({bin_name})" if shutil.which(bin_name) else f"нет `{bin_name}` (/login)"
            elif not cfg.requires_key:
                status_str = "без ключа"
            else:
                status_str = "OK" if cfg.effective_api_key() else "нет"
            rows.append(
                [
                    "●" if name == agent.client.provider_name else "",
                    name,
                    cfg.provider_type,
                    status_str,
                    cfg.model,
                ]
            )
        ui.table("ИИ-провайдеры и подписки", ["", "Имя", "Тип", "Доступ", "Модель"], rows)
        ui.dim("Переключить: /use <имя>  ·  Подписка: /login <имя>  ·  Ключ: /key <имя>")
        return True

    if cmd == "/add":
        interactive_add_provider(agent)
        return True

    if cmd == "/key":
        target = arg.lower() if arg else agent.client.provider_name
        cfg = agent.store.get(target)
        if cfg is None:
            ui.warn(f"Провайдер '{target}' не найден.")
            return True
        if cfg.provider_type == "cli":
            ui.info(f"'{target}' работает по подписке через OAuth — используйте `/login {target}`.")
            return True
        try:
            new_key = getpass.getpass(f"Ключ для '{target}' (скрыт, Enter — отмена): ").strip()
        except (EOFError, KeyboardInterrupt):
            return True
        if new_key:
            cfg.api_key = new_key
            agent.store.save()
            if target == agent.client.provider_name:
                agent.client.api_key = new_key
            ui.success(f"✓ Ключ сохранён ({mask_secret(new_key)}).")
        return True

    if cmd in ("/use", "/provider"):
        if not arg:
            return handle_slash_command("/providers", agent)
        target = arg.lower()
        cfg = agent.store.get(target)
        if cfg is None:
            ui.warn(f"Провайдер '{target}' не найден.")
            return True
        if cfg.provider_type == "cli" and shutil.which(cfg.cli_binary()) is None:
            ui.warn(f"Для подписки '{target}' нужно установить `{cfg.cli_binary()}`.")
            interactive_subscription_login(agent, target)
            return True
        try:
            key = ensure_api_key(cfg, agent.store)
            agent.store.active_provider = target
            agent.store.save()
            agent.switch_client(create_llm_client(cfg, key), preserve_context=True)
            ui.success(f"Активный провайдер: {target} · {cfg.model}")
        except (SystemExit, ValueError) as exc:
            ui.error(str(exc))
        return True

    if cmd == "/model":
        if not arg:
            ui.info(f"Модель ({agent.client.provider_name}): {agent.client.model}")
        else:
            agent.client.model = arg
            agent.client.config.model = arg
            agent.store.save()
            ui.success(f"Модель сохранена: {arg}")
        return True

    if cmd == "/vision":
        cfg = agent.client.config
        cfg.supports_vision = not cfg.supports_vision
        agent.store.save()
        ui.info(f"Vision для '{cfg.name}': {'ВКЛ' if cfg.supports_vision else 'ВЫКЛ'}")
        return True

    if cmd == "/auto":
        pol = agent.toolkit.policy
        pol.auto_approve_moderate = not pol.auto_approve_moderate
        ui.info(f"Автоподтверждение: {'ВКЛ' if pol.auto_approve_moderate else 'ВЫКЛ'}")
        return True

    if cmd == "/memory":
        if not agent.memory.data:
            ui.dim("Память пуста.")
        else:
            ui.table("Память", ["Ключ", "Значение"], [[k, v] for k, v in agent.memory.data.items()])
        return True

    if cmd == "/save":
        name = re.sub(r"[^a-zA-Z0-9_-]", "", arg) or "last"
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSIONS_DIR / f"{name}.json"
        path.write_text(json.dumps(agent.transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        ui.success(f"✓ Сохранено: {path}")
        return True

    if cmd == "/load":
        name = re.sub(r"[^a-zA-Z0-9_-]", "", arg) or "last"
        path = SESSIONS_DIR / f"{name}.json"
        if not path.exists():
            ui.warn(f"Не найдено: {path}")
            return True
        loaded = json.loads(path.read_text(encoding="utf-8"))
        agent.transcript = [(str(r), str(t)) for r, t in loaded]
        agent.client.seed_transcript(agent.transcript)
        ui.success(f"✓ Загружено сообщений: {len(agent.transcript)}")
        return True

    if cmd == "/tokens":
        u = agent.usage
        ui.info(
            f"Сессия: ввод={format_tokens(u.prompt_tokens)}, "
            f"вывод={format_tokens(u.completion_tokens)}, "
            f"всего={format_tokens(u.total_tokens)}"
        )
        return True

    if cmd == "/clear":
        agent.clear_history()
        ui.success("История очищена.")
        return True

    ui.warn(f"Неизвестная команда: {cmd}. Введите /help.")
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gemphone", description=f"GemPhone {APP_VERSION} — Android AI Agent.")
    parser.add_argument("-d", "--dir", type=Path, default=Path.cwd(), help="Рабочая директория.")
    parser.add_argument("-P", "--provider", default=None, help="Провайдер или подписка.")
    parser.add_argument("-m", "--model", default=None, help="ID модели.")
    parser.add_argument("--mode", choices=("all", "phone", "code", "chat"), default="all", help="Набор инструментов.")
    parser.add_argument("--budget", type=int, default=DEFAULT_TURN_TOKEN_BUDGET, help="Лимит токенов на 1 запрос.")
    parser.add_argument("--api-key", default=None, help="API-ключ.")
    parser.add_argument("--bridge-port", type=int, default=BRIDGE_DEFAULT_PORT, help="Порт локального Bridge.")
    parser.add_argument("--adb-serial", default=None, help="Serial устройства ADB.")
    parser.add_argument("-p", "--prompt", default=None, help="Одноразовый запрос.")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="Максимум шагов.")
    parser.add_argument("--auto", action="store_true", help="Автоподтверждение действий.")
    parser.add_argument("--yolo", action="store_true", help="Полное автоподтверждение.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _src_dir = str(Path(__file__).resolve().parent)
    if _src_dir not in sys.path:
        sys.path.insert(0, _src_dir)
    try:
        import fast_transport as _fast_transport
        _fast_transport.patch_gemphone_runtime(sys.modules[__name__])
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = args.dir.resolve()
    if not workspace.exists() or not workspace.is_dir():
        print(f"Ошибка: папка не найдена: {workspace}", file=sys.stderr)
        return 1

    ui = ConsoleUI()
    store = ConfigStore()
    memory = MemoryStore()

    cfg = store.get(args.provider.lower()) if args.provider else store.get_active()
    if cfg is None:
        ui.error(f"Провайдер '{args.provider}' не найден.")
        return 1

    runtime_cfg = ProviderConfig.from_dict(cfg.name, cfg.to_dict())
    if args.model:
        runtime_cfg.model = args.model

    non_interactive = args.prompt is not None
    api_key = ensure_api_key(cfg, store, cli_key=args.api_key, non_interactive=non_interactive)

    try:
        client = create_llm_client(runtime_cfg, api_key)
    except ValueError as exc:
        ui.error(str(exc))
        return 1

    bridge = AccessibilityBridgeClient(host=BRIDGE_DEFAULT_HOST, port=max(1, int(args.bridge_port)))
    phone = PhoneController(target_serial=args.adb_serial, bridge=bridge)
    notifier = PhoneNotifier(enabled=not non_interactive)
    policy = ApprovalPolicy(
        auto_approve_moderate=args.auto or args.yolo,
        auto_approve_dangerous=args.yolo,
        non_interactive=non_interactive,
    )
    toolkit = AgentToolkit(
        workspace=workspace,
        phone=phone,
        memory=memory,
        ui=ui,
        policy=policy,
        mode=args.mode,
        notifier=notifier,
    )
    agent = PhoneAgent(
        client=client,
        toolkit=toolkit,
        store=store,
        memory=memory,
        ui=ui,
        notifier=notifier,
        max_steps=max(1, args.max_steps),
        turn_token_budget=max(0, args.budget),
    )

    if args.prompt is not None:
        try:
            ans = agent.ask(args.prompt)
            if ans:
                ui.markdown(ans)
            return 0
        except ApiError as exc:
            ui.error(f"Ошибка API: {exc}")
            return 1
        except (KeyboardInterrupt, RequestCancelledError):
            return 130

    transport_state = phone.transport_badge(force=True)
    ui.header_card(
        provider=client.provider_name,
        model=client.model,
        workspace=str(workspace),
        transport_state=transport_state,
        mode=toolkit.mode,
        tools_count=len(toolkit.tool_specs()),
    )

    prompt_session = None
    pt_style = None
    if HAS_PROMPT_TOOLKIT and PromptSession is not None and FileHistory is not None and PTStyle is not None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            pt_style = PTStyle.from_dict(
                {
                    "prompt": f"bold {ACCENT}",
                    "bottom-toolbar": "bg:#1c1c1c #9e9e9e",
                    "completion-menu.completion": "bg:#222222 #e0e0e0",
                    "completion-menu.completion.current": f"bg:{ACCENT} #111111 bold",
                    "completion-menu.meta.completion": "bg:#1a1a1a #888888",
                    "completion-menu.meta.completion.current": f"bg:{ACCENT} #111111",
                }
            )
            prompt_session = PromptSession(
                history=FileHistory(str(HISTORY_PATH)),
                completer=_SlashCompleter(),
                complete_while_typing=True,
                style=pt_style,
            )
        except Exception:
            prompt_session = None

    def _bottom_toolbar() -> Any:
        if not HAS_PROMPT_TOOLKIT or HTML is None:
            return ""
        safe_model = html_escape(agent.client.model)
        safe_mode = html_escape(agent.toolkit.mode)
        safe_transport = html_escape(phone.transport_badge(force=False))
        auto_lbl = " · AUTO" if policy.auto_approve_moderate else ""
        return HTML(
            f" <b>{safe_model}</b> | {safe_mode} | "
            f"{format_tokens(agent.usage.total_tokens)} ток. | {safe_transport}{auto_lbl} | /help "
        )

    while True:
        try:
            ui.rule()
            if prompt_session is not None and HTML is not None:
                user_input = prompt_session.prompt(
                    HTML("<prompt>❯ </prompt>"),
                    bottom_toolbar=_bottom_toolbar,
                ).strip()
            else:
                user_input = input("❯ ").strip()
        except EOFError:
            ui.info("\nДо встречи!")
            return 0
        except KeyboardInterrupt:
            ui.dim("\n(Для выхода введите /exit или Ctrl+D)")
            continue

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not handle_slash_command(user_input, agent):
                ui.info("До встречи!")
                return 0
            continue

        try:
            answer = agent.ask(user_input)
            if answer:
                ui.markdown(answer)
            meta = agent.last_turn_meta
            if meta:
                ui.done_banner(
                    steps=int(meta.get("steps", 1)),
                    tokens=int(meta.get("tokens", 0)),
                    elapsed=float(meta.get("elapsed", 0.0)),
                    status=str(meta.get("status", "done")),
                )
        except (KeyboardInterrupt, RequestCancelledError) as exc:
            msg = str(exc) if str(exc) else "Запрос отменён"
            ui.warn(f"\n⏹ {msg} (история возвращена в исходное состояние).")
        except ApiError as exc:
            ui.error(f"\nОшибка API: {exc}")


if __name__ == "__main__":
    sys.exit(main())