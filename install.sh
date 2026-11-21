#!/data/data/com.termux/files/usr/bin/bash
# GemPhone 3.9 — быстрая установка в Termux
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${GEMPHONE_SRC:-$SCRIPT_DIR}"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"

echo "==> GemPhone 3.9 installer"

command -v python >/dev/null 2>&1 || { echo "Нужен python: pkg install python"; exit 1; }

echo "==> Опциональные зависимости (httpx, prompt_toolkit, Pillow, rich)"
python -m pip install --quiet --upgrade httpx prompt_toolkit Pillow rich || \
    echo "!! pip не смог поставить опциональные пакеты — GemPhone работает и без них"

echo "==> Проверка синтаксиса Python-модулей"
for pyf in gemphone.py gemcode.py safety.py fast_transport.py gemphone_daemon.py; do
    [ -f "$SRC_DIR/src/$pyf" ] && python -m py_compile "$SRC_DIR/src/$pyf"
done

echo "==> Устанавливаю команды в $BIN_DIR"
install_py_launcher() {
    local name="$1" file="$2"
    printf '#!/data/data/com.termux/files/usr/bin/bash\nexec python "%s" "$@"\n' "$SRC_DIR/src/$file" > "$BIN_DIR/$name"
    chmod +x "$BIN_DIR/$name" "$SRC_DIR/src/$file"
}

install_py_launcher gemphone        gemphone.py
install_py_launcher gemcode         gemcode.py
install_py_launcher gemphone-daemon gemphone_daemon.py

if [ -f "$SRC_DIR/bin/gemphone-bg.sh" ]; then
    printf '#!/data/data/com.termux/files/usr/bin/bash\nexec bash "%s" "$@"\n' "$SRC_DIR/bin/gemphone-bg.sh" > "$BIN_DIR/gemphone-bg"
    chmod +x "$BIN_DIR/gemphone-bg" "$SRC_DIR/bin/gemphone-bg.sh"
fi

for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.bash_profile"; do
    [ -f "$rc" ] || continue
    if grep -qE '^[[:space:]]*alias[[:space:]]+(gemphone|gemcode)=' "$rc"; then
        sed -i -E '/^[[:space:]]*alias[[:space:]]+(gemphone|gemcode)=/d' "$rc"
        echo "✓ удалены конфликтующие alias из $rc"
    fi
done

CFG_DIR="$HOME/.gemphone"
CFG_FILE="$CFG_DIR/config.json"
if [ ! -f "$CFG_FILE" ] && [ -f "$SRC_DIR/config.example.json" ]; then
    mkdir -p "$CFG_DIR"
    (umask 077 && cp "$SRC_DIR/config.example.json" "$CFG_FILE")
    echo "==> Создан $CFG_FILE — впиши API-ключи (или задай GEMINI_API_KEY)"
fi

echo
echo "Готово!"
echo "  Интерактивно : gemphone"
echo "  Фоновый режим: gemphone-bg start | gemphone-bg ask \"...\" | gemphone-bg widget"
