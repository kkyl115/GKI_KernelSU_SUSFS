"""Offline regression checks for the incident in Actions run 40811190068.

Extract methods via AST so importing config.py does not perform network IO.
"""
import ast
import logging
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".github/workflows/scripts/kernel_builder.py"


def isolated_method(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KernelBuilder")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    new_class = ast.ClassDef(name="Subject", bases=[], keywords=[], body=[method], decorator_list=[])
    ns = {
        "re": re, "Path": Path, "logger": logging.getLogger("test"),
        "BBG_CONFIG": {"setup_script": "unused"},
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[new_class], type_ignores=[])), str(SOURCE), "exec"), ns)
    return getattr(ns["Subject"], name)


class RegressionTests(unittest.TestCase):
    def test_bbg_preserves_conditional_defaults_and_help(self):
        fixture = (
            'config LSM\n\tstring "Ordered list of enabled LSMs"\n'
            '\tdefault "landlock,lockdown,yama,selinux" if DEFAULT_SECURITY_SMACK\n'
            '\tdefault "landlock,lockdown,bpf,selinux"\n'
            '\thelp\n\t  A comma-separated list of LSMs. Any other text.\n'
            'config NEXT\n\tbool "keep"\n\tdefault y\n'
        )
        with tempfile.TemporaryDirectory() as d:
            common = Path(d) / "common"
            kconfig = common / "security/Kconfig"
            kconfig.parent.mkdir(parents=True)
            kconfig.write_text(fixture)
            subject = SimpleNamespace(
                config=SimpleNamespace(use_bbg=True),
                work_dir=Path(d),
                _chdir=lambda _: None,
                _run_cmd=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
            )
            method = isolated_method("add_bbg")
            method(subject)
            after = kconfig.read_text()
            self.assertEqual(after.count("lockdown,baseband_guard"), 2)
            self.assertIn("if DEFAULT_SECURITY_SMACK", after)
            self.assertIn("A comma-separated list of LSMs. Any other text.", after)
            self.assertIn('config NEXT\n\tbool "keep"\n\tdefault y', after)
            method(subject)
            self.assertEqual(kconfig.read_text(), after)

    def test_bbg_fails_closed_on_unknown_kconfig(self):
        with tempfile.TemporaryDirectory() as d:
            common = Path(d) / "common"
            kconfig = common / "security/Kconfig"
            kconfig.parent.mkdir(parents=True)
            kconfig.write_text('config LSM\n\thelp\n\t  A list\n')
            subject = SimpleNamespace(
                config=SimpleNamespace(use_bbg=True),
                work_dir=Path(d),
                _chdir=lambda _: None,
                _run_cmd=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
            )
            with self.assertRaises(RuntimeError):
                isolated_method("add_bbg")(subject)

    def test_susfs_known_namespace_context_rebase(self):
        name = "50_add_susfs_in_gki-android13-5.15.patch"
        patch = (
            "diff --git a/fs/namespace.c b/fs/namespace.c\n"
            "@@ -32,11 +32,21 @@\n"
            " #include <linux/fs_context.h>\n"
            " #include <trace/hooks/blk.h>\n"
            " \n"
            "diff --git a/fs/other.c b/fs/other.c\n"
        )
        with tempfile.TemporaryDirectory() as d:
            workspace = Path(d)
            common = workspace / "android13-5.15-178/common"
            src = common / "fs/namespace.c"
            src.parent.mkdir(parents=True)
            src.write_text('#include <linux/fs_context.h>\n\n')
            susfs = workspace / "susfs4ksu/kernel_patches"
            susfs.mkdir(parents=True)
            original = susfs / name
            original.write_text(patch)
            commands = []
            def run(cmd, **kwargs):
                commands.append((cmd, kwargs))
                return subprocess.CompletedProcess(cmd, 0)
            subject = SimpleNamespace(
                config=SimpleNamespace(android_version="android13", kernel_version="5.15",
                                       get_susfs_patch_filename=lambda: name),
                work_dir=workspace / "android13-5.15-178",
                susfs_dir=workspace / "susfs4ksu",
                _chdir=lambda _: None,
                _run_cmd=run,
            )
            isolated_method("apply_susfs_patches")(subject)
            adjusted = (common / name).read_text()
            self.assertIn("@@ -32,10 +32,20 @@", adjusted)
            self.assertNotIn("trace/hooks/blk.h", adjusted)
            self.assertIn("trace/hooks/blk.h", original.read_text())
            self.assertTrue(any("patch -p1" in cmd and opt.get("check") is True
                                for cmd, opt in commands))


if __name__ == "__main__":
    unittest.main()
