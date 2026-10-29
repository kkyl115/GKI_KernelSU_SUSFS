"""Offline regression checks for the KPM switch and baseline artifact gates."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
REPO = BASE.parents[1]
sys.path.insert(0, str(BASE))
import driver


class BaselineGuards(unittest.TestCase):
    def test_checked_in_lock_is_complete_and_pinned(self):
        driver.load_lock()

    def test_floating_revision_is_rejected(self):
        lock = driver.load_lock()
        lock["projects"][0]["revision"] = "main"
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "lock.json"
            f.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "full SHA"):
                driver.load_lock(f)

    def test_wrong_common_commit_is_rejected(self):
        lock = driver.load_lock()
        for project in lock["projects"]:
            if project["path"] == "common":
                project["revision"] = "a" * 40
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "lock.json"
            f.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError, "Common source"):
                driver.load_lock(f)

    def test_paths_cannot_escape_source_tree(self):
        for p in ("../boot", "/dev/block/boot", "x/../../boot", "C:\\boot"):
            with self.subTest(p=p), self.assertRaises(ValueError):
                driver.relative_path(p)

    def test_actual_kpm_and_missing_security_options_fail(self):
        required = "\n".join("CONFIG_" + n + "=y" for n in (
            "ARM64", "MODVERSIONS", "CFI_CLANG", "SHADOW_CALL_STACK",
            "TRIM_UNUSED_KSYMS", "ANDROID_VENDOR_HOOKS"))
        driver.validate_config(required + "\n# CONFIG_KPM is not set\n")
        with self.assertRaisesRegex(ValueError, "KPM"):
            driver.validate_config(required + "\nCONFIG_KPM=y\n")
        with self.assertRaisesRegex(ValueError, "CFI_CLANG"):
            driver.validate_config(required.replace("CONFIG_CFI_CLANG=y", ""))

    def test_renamed_file_does_not_pass_as_kernel(self):
        with self.assertRaisesRegex(ValueError, "raw arm64"):
            driver.image_release(b"Linux version 5.15.178-android13-8-fake " * 10)

    def test_kernel_must_match_android_and_kmi(self):
        raw = bytearray(64)
        raw[56:60] = b"ARM\x64"
        self.assertEqual(driver.image_release(bytes(raw) + b"Linux version %s\x00" +
            b"Linux version 5.15.178-android13-8-00001-g6f2f96be86b9 (builder)"),
            "5.15.178-android13-8-00001-g6f2f96be86b9")
        for wrong in (b"android13-9-x", b"android13-80-x", b"android13-8foo"):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "release"):
                driver.image_release(bytes(raw) + b"Linux version 5.15.178-" + wrong + b" ")


class KPMSwitchRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import legacy builder without config.py's unrelated network lookup.
        fake = types.ModuleType("config")
        for name in ("BuildConfig", "KSU_REPO_CONFIG", "SUSFS_REPO_CONFIG",
                     "SUKISU_PATCH_REPO_CONFIG", "ANYKERNEL_CONFIG",
                     "KERNEL_PATCHES_CONFIG", "BBG_CONFIG", "TOOLCHAIN_CONFIG",
                     "LEGACY_FIXES", "OP8E_PATCH_URL", "KPM_PATCH_URL"):
            setattr(fake, name, object())
        spec = importlib.util.spec_from_file_location("legacy_builder_test",
            REPO / ".github/workflows/scripts/kernel_builder.py")
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"config": fake, spec.name: cls.module}):
            spec.loader.exec_module(cls.module)

    def test_disable_removes_old_enabled_kpm_and_enable_can_be_restored(self):
        previous = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as d:
                base = Path(d)
                cfg_path = base / "common/arch/arm64/configs/gki_defconfig"
                cfg_path.parent.mkdir(parents=True)
                cfg_path.write_text("CONFIG_ARM64=y\nCONFIG_KPM=y\n")
                config = types.SimpleNamespace(use_kpm=False, kernel_version="5.15",
                    use_zram=False, set_default_bbr=False)
                builder = self.module.KernelBuilder.__new__(self.module.KernelBuilder)
                builder.config, builder.work_dir = config, base
                builder._chdir = lambda p: None
                builder.configure_kernel()
                self.assertEqual(driver.parse_config(cfg_path.read_text())["CONFIG_KPM"], "n")
                self.assertNotIn("CONFIG_KPM=y", cfg_path.read_text())
                config.use_kpm = True
                builder.configure_kernel()
                self.assertEqual(driver.parse_config(cfg_path.read_text())["CONFIG_KPM"], "y")
                self.assertEqual(cfg_path.read_text().count("CONFIG_KPM=y"), 1)
                self.assertNotIn("# CONFIG_KPM is not set", cfg_path.read_text())
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
