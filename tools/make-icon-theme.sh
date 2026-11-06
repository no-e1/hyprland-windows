#!/usr/bin/env bash
# Builds the "Win11-modern" icon theme: Windows 11 icons for folders, files and
# system UI, but every app keeps its own up-to-date icon (from hicolor).
set -euo pipefail
BASE="${1:-$HOME/.local/share/icons/Win11-dark}"
OUT="$HOME/.local/share/icons/Win11-modern"
[ -f "$BASE/index.theme" ] || { echo "Base theme not found: $BASE" >&2; exit 1; }

rm -rf "$OUT"; mkdir -p "$OUT"
for d in "$BASE"/*/; do
    name=$(basename "$d")
    case "$name" in apps|apps@2x) continue ;; esac
    ln -s "$d" "$OUT/$name"
done

# Keep the Windows 11 look for the few generic app icons the shell uses
mkdir -p "$OUT/apps/scalable"
for i in system-file-manager utilities-terminal; do
    f=$(find -L "$BASE/apps/scalable" -name "$i.svg" | head -n1)
    [ -n "$f" ] && ln -s "$f" "$OUT/apps/scalable/$i.svg"
done

awk -v dirs_keep=1 '
    /^Name=/       { print "Name=Win11-modern"; next }
    /^Inherits=/   { print "Inherits=hicolor,breeze,Adwaita"; next }
    /^Directories=/ || /^ScaledDirectories=/ {
        split(substr($0, index($0, "=") + 1), a, ",")
        key = substr($0, 1, index($0, "=") - 1); out = ""
        for (i in a) if (a[i] !~ /^apps/ && a[i] != "") out = out (out ? "," : "") a[i]
        print key "=" out (key == "Directories" ? ",apps/scalable" : ""); next
    }
    { print }
' "$BASE/index.theme" > "$OUT/index.theme"
grep -q '^\[apps/scalable\]' "$OUT/index.theme" || printf '\n[apps/scalable]\nContext=Applications\nSize=48\nMinSize=8\nMaxSize=512\nType=Scalable\n' >> "$OUT/index.theme"
gtk-update-icon-cache -q -f "$OUT" 2>/dev/null || true
echo "Built $OUT"
