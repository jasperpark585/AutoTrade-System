import unittest
from pathlib import Path


class InstallScriptTests(unittest.TestCase):
    def test_windows_installer_creates_venv_and_start_scripts(self):
        text = Path("scripts/install_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("python -m venv .venv", text)
        self.assertIn("pip install -r requirements.txt", text)
        self.assertIn("Start-AutoTrade-Engine.ps1", text)
        self.assertIn("Start-AutoTrade-UI.ps1", text)

    def test_ec2_bootstrap_installs_service_units(self):
        text = Path("scripts/bootstrap_ec2.sh").read_text(encoding="utf-8")
        self.assertIn("python3 -m venv .venv", text)
        self.assertIn("systemctl enable autotrade-engine autotrade-ui", text)
        self.assertIn("systemctl restart autotrade-engine autotrade-ui", text)


if __name__ == "__main__":
    unittest.main()
