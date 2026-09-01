import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import grok_register_ttk as app


class ConfigPermissionsTests(unittest.TestCase):
    def test_save_config_limits_file_to_current_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            with patch.object(app, "CONFIG_FILE", str(config_path)):
                app.save_config()

            mode = stat.S_IMODE(config_path.stat().st_mode)
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
