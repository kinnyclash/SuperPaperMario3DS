#!/bin/sh
# Download prebuilt Linux x86_64 makerom, ctrtool and bannertool into DIR (CI only).
# Asset names change between releases, so this lists each release's assets and picks the
# Linux build by pattern. Needs gh (GH_TOKEN set), jq and unzip.
set -eu
dir=$1; mkdir -p "$dir"; dl=$(mktemp -d)

pick() {  # pick REPO REGEX -> prints "tag asset" of the newest release with a matching asset
  gh api "repos/$1/releases?per_page=30" | jq -r --arg re "$2" \
    '[.[] | . as $r | .assets[] | select(.name|test($re;"i")) | "\($r.tag_name) \(.name)"][0] // empty'
}

fetch() {  # fetch REPO REGEX BINARY
  set -- "$1" "$2" "$3" "$(pick "$1" "$2")"
  [ -n "$4" ] || { echo "no asset matching $2 in $1"; gh api "repos/$1/releases?per_page=5" \
      | jq -r '.[] | .tag_name + ": " + ([.assets[].name]|join(", "))'; return 1; }
  tag=${4%% *}; asset=${4#* }
  echo "$1: $tag / $asset"
  mkdir -p "$dl/$3"
  gh release download "$tag" -R "$1" -p "$asset" -D "$dl/$3"
  case "$asset" in
    *.zip) unzip -q -o "$dl/$3/$asset" -d "$dl/$3/x" ;;
    *.tar.*|*.tgz) mkdir -p "$dl/$3/x"; tar -xf "$dl/$3/$asset" -C "$dl/$3/x" ;;
    *) mkdir -p "$dl/$3/x"; cp "$dl/$3/$asset" "$dl/$3/x/$3" ;;
  esac
  # Several platform builds can share one zip: prefer a path that says linux.
  bin=$(find "$dl/$3/x" -type f -name "$3" | grep -i linux | head -n1 || true)
  [ -n "$bin" ] || bin=$(find "$dl/$3/x" -type f -name "$3" | head -n1)
  [ -n "$bin" ] || { echo "no $3 inside $asset"; find "$dl/$3/x"; return 1; }
  cp "$bin" "$dir/$3"; chmod +x "$dir/$3"
}

fetch 3DSGuy/Project_CTR 'makerom.*(ubuntu|linux).*\.(zip|tar\.gz)$' makerom
fetch 3DSGuy/Project_CTR 'ctrtool.*(ubuntu|linux).*\.(zip|tar\.gz)$' ctrtool || echo "ctrtool unavailable (optional)"
fetch Epicpkmn11/bannertool 'bannertool.*\.zip$' bannertool
ls -l "$dir"
