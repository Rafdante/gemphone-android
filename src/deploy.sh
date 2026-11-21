#!/data/data/com.termux/files/usr/bin/bash
# Синхронизирует исходники репозитория GemPhone в $PREFIX/bin (единственный способ установки).
# Использование:  ./deploy.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${PREFIX:-/data/data/com.termux/files/usr}/bin"

mkdir -p "$DEST"

for f in gemphone.py gemcode.py safety.py; do
    src="$REPO/src/$f"
    [ -f "$src" ] || { echo "!! нет файла $src"; exit 1; }
    python3 -m py_compile "$src"
    install -m 700 "$src" "$DEST/$f"
    echo "✓ $f → $DEST/$f"
done

# Убираем конкурирующие alias/копии, чтобы запускалась ровно установленная версия.
for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.bash_profile"; do
    [ -f "$rc" ] || continue
    if grep -qE '^[[:space:]]*alias[[:space:]]+(gemphone|gemcode)=' "$rc"; then
        sed -i -E '/^[[:space:]]*alias[[:space:]]+(gemphone|gemcode)=/d' "$rc"
        echo "✓ удалены конфликтующие alias из $rc"
    fi
done
if [ -e "$HOME/bin/gemphone.py" ]; then
    echo "! ВНИМАНИЕ: старая копия $HOME/bin/gemphone.py — удалите:  rm -f ~/bin/gemphone.py*"
fi

echo "Готово. Запуск: gemphone  (сейчас: $(command -v gemphone || echo 'не в PATH'))"
