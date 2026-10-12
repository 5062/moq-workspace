#!/usr/bin/env bash
# Check that the forks' standalone pins build the code the workspace pins.
#
# A relay host builds each fork on its own, from the revisions the fork names
# itself: Cargo lockfiles, git revs in Cargo manifests and configs, and
# moxygen's mvfst-rev.txt. Inside the workspace those are replaced by the
# submodules, so a stale standalone pin goes unnoticed until a host builds
# against an older contract or a commit GitHub no longer keeps.
#
# Every standalone pin must be in its submodule's checked-out history, so it is
# on a branch and not ahead of the pin, and must leave the files a dependent
# builds unchanged from the pin. Exact equality cannot hold: moq-bench lives in
# moq-trace and locks moq-dev-moq, whose lock names moq-trace, so updating one
# lock always outdates the other by a lockfile-only commit.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

# Standalone pins: Cargo lockfiles, then manifests and configs with git revs.
lockfiles=(
  relays/moq-dev-moq/Cargo.lock
  relays/cloudflare-moq-rs/Cargo.lock
  moq-trace/moq-bench/Cargo.lock
)
manifests=(
  relays/moq-dev-moq/rs/trace.toml
  moq-trace/moq-bench/Cargo.toml
)

# The submodule each fork repository is pinned at, and the paths a dependent
# builds from it. A path list of . means the whole tree.
submodule() {
  case $1 in
    moq-trace) echo moq-trace ;;
    moq) echo relays/moq-dev-moq ;;
    moq-rs) echo relays/cloudflare-moq-rs ;;
    quinn) echo deps/quinn ;;
    web-transport) echo deps/web-transport ;;
    mvfst) echo deps/mvfst ;;
    *) return 1 ;;
  esac
}
built_paths() {
  case $1 in
    moq-trace) echo crates Cargo.toml ;;
    moq) echo rs Cargo.toml ;;
    *) echo . ;;
  esac
}

failures=0
checked=0
check() {
  local file=$1 repo=$2 rev=$3
  local sub pin
  if ! sub=$(submodule "$repo"); then
    echo "$file: 5062/$repo is not a workspace submodule" >&2
    failures=$((failures + 1))
    return
  fi
  pin=$(git -C "$sub" rev-parse HEAD)
  checked=$((checked + 1))
  if ! git -C "$sub" cat-file -e "$rev^{commit}" 2>/dev/null; then
    echo "$file: 5062/$repo ${rev:0:12} is not in $sub; fetch it or repin to ${pin:0:12}" >&2
    failures=$((failures + 1))
  elif ! git -C "$sub" merge-base --is-ancestor "$rev" "$pin"; then
    echo "$file: 5062/$repo ${rev:0:12} is not in the history of the pin ${pin:0:12}" >&2
    failures=$((failures + 1))
  elif ! git -C "$sub" diff --quiet "$rev" "$pin" -- $(built_paths "$repo"); then
    echo "$file: 5062/$repo ${rev:0:12} builds different code than the pin ${pin:0:12}" >&2
    failures=$((failures + 1))
  fi
}

for file in "${lockfiles[@]}"; do
  while read -r repo rev; do
    check "$file" "$repo" "$rev"
  done < <(grep -oE 'git\+https://github\.com/5062/[^?#"]+[^#"]*#[0-9a-f]{40}' "$file" |
    sed -E 's|git\+https://github\.com/5062/([^?#]+)[^#]*#([0-9a-f]{40})|\1 \2|' | sort -u)
done
for file in "${manifests[@]}"; do
  while read -r repo rev; do
    check "$file" "$repo" "$rev"
  done < <(grep -oE 'github\.com/5062/[^"]+", rev = "[0-9a-f]{40}"' "$file" |
    sed -E 's|github\.com/5062/([^"]+)", rev = "([0-9a-f]{40})"|\1 \2|' | sort -u)
done
check relays/facebook-moxygen/moq_trace/mvfst-rev.txt mvfst \
  "$(tr -d '[:space:]' <relays/facebook-moxygen/moq_trace/mvfst-rev.txt)"

if [ "$failures" -gt 0 ]; then
  echo "$failures of $checked standalone pins disagree with the workspace pins" >&2
  exit 1
fi
echo "All $checked standalone pins build the pinned code"
