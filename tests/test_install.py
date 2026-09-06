import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class InstallerTests(unittest.TestCase):
    def test_installer_exposes_short_and_full_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            temporary = Path(folder)
            environment = os.environ.copy()
            environment.update({
                "BUILDBRAKE_INSTALL_ROOT": str(temporary / "share"),
                "BUILDBRAKE_BIN_DIR": str(temporary / "bin"),
            })
            result = subprocess.run(
                [str(ROOT / "install.sh")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for command in ("bb", "buildbrake"):
                executable = temporary / "bin" / command
                self.assertTrue(executable.exists(), command)
                help_result = subprocess.run(
                    [str(executable), "--help"], text=True, capture_output=True,
                )
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn("Put an outcome and time limit", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
