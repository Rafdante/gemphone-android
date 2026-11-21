#!/data/data/com.termux/files/usr/bin/bash
# Синхронизирует исходники репозитория GemPhone в ~/bin (устанавливает копию).
# Использование:  ./deploy.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/bin"

mkdir -p "$DEST"

for f in gemphone.py gemcode.py safety.py; do
    src="$REPO/src/$f"
    [ -f "$src" ] || { echo "!! нет файла $src"; exit 1; }
    python3 -m py_compile "$src"
    install -m 700 "$src" "$DEST/$f"
    echo "✓ $f → $DEST/$f"
done

echo "Готово. Запуск: gemphone (см. alias в ~/.bashrc)"
