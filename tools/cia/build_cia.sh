#!/bin/sh
# Package one spm3ds test app as an installable CIA.
#   tools/cia/build_cia.sh ELF OUT.cia UNIQUE_ID PRODUCT_CODE "Title" "Description" LETTER RRGGBB
# Needs makerom and bannertool on PATH (CI downloads them from their GitHub releases).
set -eu
elf=$1; out=$2; uid=$3; code=$4; title=$5; desc=$6; letter=$7; colour=$8
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
python3 "$here/make_assets.py" "$work" "$letter" "$colour"
bannertool makebanner -i "$work/banner.png" -a "$work/banner.wav" -o "$work/banner.bnr"
bannertool makesmdh -s "$title" -l "$desc" -p "spm3ds test build" -i "$work/icon.png" \
    -o "$work/icon.smdh"
makerom -f cia -o "$out" -elf "$elf" -rsf "$here/app.rsf" -target t -exefslogo \
    -icon "$work/icon.smdh" -banner "$work/banner.bnr" \
    -DAPP_TITLE="$title" -DAPP_PRODUCT_CODE="$code" -DAPP_UNIQUE_ID="$uid"
test -s "$out"
echo "built $out ($(wc -c < "$out") bytes, unique ID $uid)"
rm -rf "$work"
