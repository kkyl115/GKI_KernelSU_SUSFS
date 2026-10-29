"""Pinned AOSP baseline sync and artifact audit; never operates on a phone."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE_COMMIT = "6f2f96be86b93de6ed58a2c46b456fb49734c382"
RELEASE_PREFIX = "5.15.178-android13-8"
REQUIRED_PROJECTS = {
    "common", "build/kernel", "prebuilts/clang/host/linux-x86",
    "prebuilts/gcc/linux-x86/host/x86_64-linux-glibc2.17-4.8",
    "prebuilts/build-tools", "prebuilts/kernel-build-tools",
    "tools/mkbootimg", "prebuilts/ndk-r23",
}


def relative_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value:
        raise ValueError(f"Unsafe relative path: {value!r}")
    return p


def load_lock(path=HERE / "source-lock.json"):
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock["source_commit"] != SOURCE_COMMIT:
        raise ValueError("Unexpected baseline source commit")
    projects = lock["projects"]
    paths = [p["path"] for p in projects]
    if len(paths) != len(set(paths)) or set(paths) != REQUIRED_PROJECTS:
        raise ValueError("Missing or duplicate pinned project")
    for p in projects:
        relative_path(p["path"])
        relative_path(p["name"])
        if not re.fullmatch(r"[0-9a-f]{40}", p["revision"]):
            raise ValueError("Every source/toolchain revision must be a full SHA")
        if p["path"] == "common" and p["revision"] != SOURCE_COMMIT:
            raise ValueError("Common source does not match baseline")
        for link in p["linkfiles"]:
            relative_path(link["src"])
            relative_path(link["dest"])
    return lock


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def verify_sources(root, lock):
    resolved = {}
    for p in lock["projects"]:
        dest = root / p["path"]
        actual = git(dest, "rev-parse", "HEAD")
        if actual != p["revision"]:
            raise ValueError(f"Wrong checkout: {p['path']}: {actual}")
        if git(dest, "status", "--porcelain", "--untracked-files=no"):
            raise ValueError(f"Tracked source was modified: {p['path']}")
        resolved[p["path"]] = actual
    return resolved


def sync(root, lock):
    root.mkdir(parents=True, exist_ok=True)
    for p in lock["projects"]:
        dest = root / p["path"]
        url = "https://android.googlesource.com/" + p["name"]
        print(f"Sync {p['path']} @ {p['revision']}", flush=True)
        created = not dest.exists()
        if dest.exists():
            # Do not reset a reused/modified checkout. Fail for inspection instead.
            if not (dest / ".git").is_dir():
                raise ValueError(f"Existing non-Git directory: {dest}")
            if git(dest, "remote", "get-url", "origin") != url:
                raise ValueError(f"Unexpected remote: {dest}")
            if git(dest, "status", "--porcelain"):
                raise ValueError(f"Nonempty or modified checkout: {dest}")
        else:
            dest.mkdir(parents=True)
            subprocess.run(["git", "init", str(dest)], check=True)
            git(dest, "remote", "add", "origin", url)
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth=1", "--no-tags",
                        "origin", p["revision"]], check=True)
        if created:
            subprocess.run(["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"],
                           check=True)
        if git(dest, "rev-parse", "--verify", "HEAD") != p["revision"]:
            raise ValueError(f"Reused checkout has another HEAD: {dest}")
    verify_sources(root, lock)
    for p in lock["projects"]:
        for link in p["linkfiles"]:
            source = root / p["path"] / link["src"]
            target = root / link["dest"]
            if not source.exists():
                raise ValueError(f"Missing link source: {source}")
            if target.is_symlink() and target.resolve() == source.resolve():
                continue
            if target.exists() or target.is_symlink():
                raise ValueError(f"Refusing to overwrite: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=source.is_dir())
    (root / "resolved-sources.json").write_text(
        json.dumps(verify_sources(root, lock), indent=2) + "\n", encoding="utf-8")


def parse_config(text):
    result = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            result[line[2:-11]] = "n"
    return result


def validate_config(text):
    cfg = parse_config(text)
    for name in ("ARM64", "MODVERSIONS", "CFI_CLANG", "SHADOW_CALL_STACK",
                 "TRIM_UNUSED_KSYMS", "ANDROID_VENDOR_HOOKS"):
        if cfg.get("CONFIG_" + name) != "y":
            raise ValueError(f"Required compatibility/security option missing: {name}")
    for name in ("KPM", "KSU", "KSU_SUSFS"):
        if cfg.get("CONFIG_" + name, "n") != "n":
            raise ValueError(f"Unexpected extra integration in baseline: {name}")
    return cfg


def image_release(data):
    if len(data) < 64 or data[56:60] != b"ARM\x64":
        raise ValueError("Not a raw arm64 Linux Image")
    match = re.search(rb"Linux version (5\.15\.178-android13-8[^\s\x00]*)", data)
    if not match:
        raise ValueError("Image release does not match baseline kernel/KMI")
    return match.group(1).decode("ascii")


def audit(root, artifacts, lock):
    resolved = verify_sources(root, lock)
    out = root / "out/common"
    dist = root / "dist"
    config = (out / ".config").read_text()
    validate_config(config)
    image = (dist / "Image").read_bytes()
    release = image_release(image)
    if release != (out / "include/config/kernel.release").read_text().strip():
        raise ValueError("Image banner and generated kernel.release disagree")
    embedded = subprocess.check_output(
        ["bash", str(root / "common/scripts/extract-ikconfig"), str(dist / "Image")],
        text=True)
    if parse_config(embedded) != parse_config(config):
        raise ValueError("Embedded Image config differs from compiled config")
    artifacts.mkdir(parents=True, exist_ok=True)
    for name in ("Image", "Image.gz", "Image.lz4", "System.map", "vmlinux.symvers"):
        source = dist / name
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError(f"Missing build output: {name}")
        shutil.copy2(source, artifacts / name)
    (artifacts / "kernel.config").write_text(config, encoding="utf-8")
    (artifacts / "kernel.release").write_text(release + "\n", encoding="utf-8")
    report = {
        "status": "compiled-and-static-audited-not-device-validated",
        "kernel_release": release, "source_revisions": resolved,
        "source_tracked_files_unchanged": True, "kpm": False, "root": False,
        "embedded_config_matches": True,
        "upstream_defconfig_and_kmi_symbol_checks_retained": True,
        "full_abi_layout_comparison_performed": False,
        "oem_signed": False, "boot_image_packaged": False,
        "device_boot_network_vendor_modules_verified": False,
        "performance_or_power_improvement_measured": False,
    }
    (artifacts / "audit.json").write_text(json.dumps(report, indent=2) + "\n")
    (artifacts / "README.txt").write_text(
        "K70 source-matched baseline / NOT DEVICE VALIDATED\n"
        "Raw kernel outputs only. This is not a Root module or a flash ZIP.\n"
        "No KPM, KernelSU or SUSFS integration; no CPU/GPU/thermal changes.\n"
        "No OEM signature or device compatibility claim. Do not flash blindly.\n"
        "Keep the independently stored known-working recovery boot image.\n")
    checksums = []
    for p in sorted(artifacts.iterdir()):
        if p.is_file() and p.name not in ("SHA256SUMS.txt", "build.log"):
            checksums.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
    (artifacts / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync", "audit"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    lock = load_lock()
    if args.command == "sync":
        sync(args.root.resolve(), lock)
    else:
        if args.artifacts is None:
            parser.error("audit requires --artifacts")
        audit(args.root.resolve(), args.artifacts.resolve(), lock)
