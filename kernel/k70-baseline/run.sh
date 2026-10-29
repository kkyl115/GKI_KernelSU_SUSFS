#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_root="$(realpath -m -- "${1:?build root required}")"
artifacts="$(realpath -m -- "${2:?artifact directory required}")"
mkdir -p "$build_root" "$artifacts"
cp "$script_dir/source-lock.json" "$artifacts/source-lock.json"
date -u +%FT%TZ > "$artifacts/build-started-utc.txt"
available_kib="$(df -Pk "$build_root" | awk 'NR==2 {print $4}')"
if (( available_kib < 35 * 1024 * 1024 )); then
    echo "At least 35 GiB free space is required; found ${available_kib} KiB."
    exit 1
fi
python3 "$script_dir/driver.py" sync --root "$build_root"
cd "$build_root"
test -x prebuilts/clang/host/linux-x86/clang-r450784e/bin/clang
prebuilts/clang/host/linux-x86/clang-r450784e/bin/clang --version \
    > "$artifacts/compiler-version.txt"
# Source and official defconfig remain unmodified. Keep KMI trimming/strict checks,
# Android vendor hooks, CFI and existing thermal/CPU/GPU driver behavior.
# This invokes the official legacy build supported by this Android 13 branch.
BUILD_CONFIG=common/build.config.gki.aarch64 \
    OUT_DIR="$build_root/out" DIST_DIR="$build_root/dist" \
    bash build/build.sh -j2
python3 "$script_dir/driver.py" audit --root "$build_root" --artifacts "$artifacts"
date -u +%FT%TZ > "$artifacts/build-finished-utc.txt"
