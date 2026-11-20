"""Общие правила безопасности для gemphone и gemcode.

Важно: блок-лист команд и проверка URL — это защита от типичных ошибок и
случайных действий модели, а не полноценная песочница. Основная граница
безопасности — подтверждение действия пользователем (см. _confirm).
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import socket
from typing import Final
from urllib.parse import urlsplit

# Деструктивные команды, которые run_command не выполняет даже после подтверждения.
_DESTRUCTIVE_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # rm с любым рекурсивным флагом (-rf, -r -f, -fr, --recursive) и корнем/домом
    # в качестве цели. Кавычки и ${HOME} нормализуются в _normalize().
    ("рекурсивное удаление корня/дома", re.compile(
        r"\brm\b(?=[^;&|]*(?:\s-[a-zA-Z]*[rR][a-zA-Z]*\b|\s--recursive\b))"
        r"[^;&|]*\s(?:/\*|/|~/|~|\$HOME)(?=\s|/|\*|;|&|\||$)"
    )),
    ("dd на устройство", re.compile(r"\bdd\b[^;&|]*\bof=/dev/(block|sd|mmcblk|zero|sda)")),
    ("fork bomb", re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:")),
    ("форматирование/mkfs", re.compile(r"\bmkfs(\.[a-z0-9]+)?\b|\bmke2fs\b")),
    ("перезапись диска", re.compile(r"\b(shred|wipefs)\b[^;&|]*/dev/")),
    ("терминальный chmod/chown на /", re.compile(r"\bch(mod|own)\s+-R\s+[^;&|]*\s/(\s|$)")),
    ("history wipe", re.compile(r"\brm\b[^;&|]*(\.bash_history|\.zsh_history)")),
    # Только в начале команды или после разделителя, чтобы не ловить `echo reboot`.
    ("poweroff/reboot", re.compile(r"(?:^|[;&|(]\s*)(?:su\s+-c\s+)?(?:reboot|poweroff|halt)\b")),
    # am start -d/--data с не-http схемой открывает диплинки в обход подтверждения open_url.
    ("ADB intent с не-http схемой", re.compile(
        r"\badb\b[^;&|]*\bshell\b[^;&|]*\b(am|monkey)\b[^;&|]*"
        r"(-d|--data|data)[ =]*(?!https?://)[a-z][a-z0-9+.-]*:"
    )),
    ("ADB intent с компонентом/flags", re.compile(
        r"\badb\b[^;&|]*\bshell\b[^;&|]*\bam\s+start\b[^;&|]*(-n\s|--component|-f\s|--flags|--user\s)"
    )),
)


def _normalize(command: str) -> str:
    """Убирает кавычки и приводит ${HOME} к $HOME, чтобы правила не обходились кавычками."""
    text = command.replace('"', " ").replace("'", " ").replace("${HOME}", "$HOME")
    return " ".join(text.split())


def detect_destructive_command(command: str) -> str | None:
    """Возвращает описание сработавшего правила или None, если команда допустима."""
    normalized = _normalize(command)
    for label, pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(normalized):
            return label
    return None


_BLOCKED_NETS_NOTE: Final[str] = "локальный или приватный адрес"


def check_fetch_url(url: str) -> str | None:
    """Проверяет URL для web_fetch. Возвращает причину отказа или None."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return "разрешены только http(s)"
    host = parts.hostname
    if not host:
        return "не указан хост"
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return _BLOCKED_NETS_NOTE
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return "не удалось определить адрес хоста"
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            return f"{_BLOCKED_NETS_NOTE} ({addr})"
    return None


def write_private(path: Path, text: str) -> None:
    """Пишет файл с правами 0600 с самого начала (без окна с правами по umask)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)
