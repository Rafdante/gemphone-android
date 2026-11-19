#!/data/data/com.termux/files/usr/bin/bash
# GemPhone — установка в Termux одной командой:
#   curl -fsSL https://raw.githubusercontent.com/Rafdante/gemphone-android/main/install.sh | bash
set -euo pipefail

REPO_URL="${GEMPHONE_REPO:-https://github.com/Rafdante/gemphone-android.git}"
SRC_DIR="${GEMPHONE_SRC:-$HOME/gemphone-android}"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"

echo "==> GemPhone installer"

command -v python >/dev/null 2>&1 || { echo "Нужен python: pkg install python"; exit 1; }
command -v git    >/dev/null 2>&1 || { echo "Нужен git: pkg install git"; exit 1; }

if [ -d "$SRC_DIR/.git" ]; then
    echo "==> Обновляю $SRC_DIR"
    git -C "$SRC_DIR" pull --ff-only
else
    echo "==> Клонирую в $SRC_DIR"
    git clone --depth 1 "$REPO_URL" "$SRC_DIR"
fi

echo "==> Опциональные зависимости (httpx, prompt_toolkit, Pillow, rich)"
python -m pip install --quiet --upgrade httpx prompt_toolkit Pillow rich || \
    echo "!! pip не смог поставить опциональные пакеты — GemPhone работает и без них"

echo "==> Устанавливаю запускалки в $BIN_DIR"
install_launcher() {  # $1 = имя, $2 = файл в src/
    printf '#!/data/data/com.termux/files/usr/bin/bash\nexec python "%s" "$@"\n' "$SRC_DIR/src/$2" > "$BIN_DIR/$1"
    chmod +x "$BIN_DIR/$1" "$SRC_DIR/src/$2"
}
install_launcher gemphone gemphone.py
install_launcher gemcode  gemcode.py

if [ ! -f "$SRC_DIR/config.json" ]; then
    cp "$SRC_DIR/config.example.json" "$SRC_DIR/config.json"
    echo "==> Создан config.json из примера — впиши API-ключи (или задай GEMINI_API_KEY и т.п.)"
fi

echo
echo "Готово. Запуск:  gemphone --help   |   export GEMINI_API_KEY=...  (ключи в git не попадают)"
