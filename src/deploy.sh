#!/data/data/com.termux/files/usr/bin/bash
# Синхронизирует исходники GemPhone 3.9 в $PREFIX/bin.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${PREFIX:-/data/data/com.termux/files/usr}/bin"

mkdir -p "$DEST"

for f in gemphone.py gemcode.py safety.py fast_transport.py gemphone_daemon.py; do
    src="$REPO/src/$f"
    [ -f "$src" ] || { echo "!! нет файла $src"; exit 1; }
    python3 -m py_compile "$src"
    install -m 700 "$src" "$DEST/$f"
    echo "✓ $f -> $DEST/$f"
done

if [ -f "$REPO/bin/gemphone-bg.sh" ]; then
    install -m 700 "$REPO/bin/gemphone-bg.sh" "$DEST/gemphone-bg"
    echo "✓ gemphone-bg -> $DEST/gemphone-bg"
fi

if [ -f "$REPO/bin/gemagent.sh" ]; then
    install -m 700 "$REPO/bin/gemagent.sh" "$DEST/gemagent"
    echo "✓ gemagent -> $DEST/gemagent"
fi

for name_pair in "gemphone:gemphone.py" "gemcode:gemcode.py" "gemphone-daemon:gemphone_daemon.py"; do
    name="${name_pair%%:*}"
    target="${name_pair##*:}"
    printf '#!/data/data/com.termux/files/usr/bin/bash\nexec python3 "%s/%s" "$@"\n' "$DEST" "$target" > "$DEST/$name"
    chmod 700 "$DEST/$name"
done

for rc in "$HOME/.bashrc" "$HOME/.profile" "$HOME/.bash_profile"; do
    [ -f "$rc" ] || continue
    if grep -qE '^[[:space:]]*alias[[:space:]]+(gemphone|gemcode)=' "$rc"; then
        sed -i -E '/^[[:space:]]*alias[[:space:]]+(gemphone|gemcode)=/d' "$rc"
        echo "✓ удалены конфликтующие alias из $rc"
    fi
done

echo "Готово. Запуск: gemphone | gemphone-bg start"
