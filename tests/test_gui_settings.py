import json
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import grok_register_ttk as app
from gui_settings import SettingsPanel, apply_import_target, infer_import_target


class ImportTargetTests(unittest.TestCase):
    def test_legacy_flags_are_inferred_without_changing_destinations(self):
        for cpa, local, remote, expected in (
            (True, False, False, "cpa"),
            (False, True, False, "grok2api"),
            (False, False, True, "grok2api"),
            (True, False, True, "both"),
            (False, False, False, "none"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(infer_import_target({
                    "cpa_export_enabled": cpa,
                    "grok2api_auto_add_local": local,
                    "grok2api_auto_add_remote": remote,
                }), expected)

    def test_cpa_only_disables_grok_but_retains_connection_settings(self):
        original = {
            "grok2api_auto_add_local": False,
            "grok2api_auto_add_remote": True,
            "grok2api_remote_app_key": "test-key",
        }
        result = apply_import_target(original, "cpa")
        self.assertTrue(result["cpa_export_enabled"])
        self.assertFalse(result["grok2api_auto_add_remote"])
        self.assertNotIn("gui_grok2api_auto_add_remote", result)
        self.assertEqual(result["grok2api_remote_app_key"], "test-key")
        self.assertTrue(original["grok2api_auto_add_remote"])


class GuiSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display unavailable: {exc}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp.name) / "config.json"
        self.original_config = app.config
        self.values = dict(app.DEFAULT_CONFIG, register_count=5, concurrent_count=2,
                           grok2api_auto_add_local=False, cpa_management_auto_upload=True,
                           cpa_management_base="https://cpa.example.com",
                           cpa_management_key="test-management-key", extra_extension={"keep": True})
        self.config_path.write_text(json.dumps(self.values))
        self.file_patch = patch.object(app, "CONFIG_FILE", str(self.config_path))
        self.file_patch.start()
        self.gui = app.GrokRegisterGUI(self.root)

    def tearDown(self):
        for after_id in self.root.tk.splitlist(self.root.tk.call("after", "info")):
            self.root.after_cancel(after_id)
        for widget in self.root.winfo_children():
            widget.destroy()
        self.file_patch.stop()
        app.config = self.original_config
        self.temp.cleanup()

    def test_cpa_config_loads_and_secrets_are_masked(self):
        self.assertEqual(self.gui.settings.target_var.get(), "CPA")
        key = self.gui.settings.fields["cpa_management_key"][0][0]
        self.assertEqual(key.cget("show"), "•")
        self.assertTrue(self.gui.settings.import_frames["cpa"].grid_info())
        self.assertFalse(self.gui.settings.import_frames["grok2api"].grid_info())

    def test_all_import_modes_generate_correct_runtime_flags(self):
        panel = self.gui.settings
        panel.variables["grok2api_auto_add_remote"].set(True)
        panel.variables["grok2api_remote_base"].set("https://grok.example.com")
        panel.variables["grok2api_remote_app_key"].set("test-key")
        for label, expected in (("CPA", (True, False)), ("grok2api", (False, True)),
                                ("CPA + grok2api", (True, True)), ("不自动导入", (False, False))):
            with self.subTest(label=label):
                panel.target_var.set(label)
                values = panel.collect()
                self.assertEqual((values["cpa_export_enabled"], values["grok2api_auto_add_remote"]), expected)

    def test_save_during_run_keeps_active_config_and_reloads_draft(self):
        self.gui._set_running_ui(True)
        self.gui.settings.variables["concurrent_count"].set("3")
        self.assertTrue(self.gui.save_settings())
        self.assertEqual(app.config["concurrent_count"], 2)
        saved = json.loads(self.config_path.read_text())
        self.assertEqual(saved["concurrent_count"], 3)
        self.assertEqual(saved["extra_extension"], {"keep": True})
        panel = SettingsPanel(self.root, saved, lambda: None)
        self.assertEqual(panel.variables["concurrent_count"].get(), "3")
        self.assertEqual(panel.target_var.get(), "CPA")
        panel.destroy()

    def test_reloaded_switches_match_file_instead_of_old_gui_preferences(self):
        panel = self.gui.settings
        panel.variables["grok2api_auto_add_remote"].set(True)
        values = panel.collect()
        values["gui_grok2api_auto_add_remote"] = True
        reloaded = SettingsPanel(self.root, values, lambda: None)
        self.assertFalse(reloaded.variables["grok2api_auto_add_remote"].get())
        self.assertEqual(reloaded.target_var.get(), "CPA")
        reloaded.destroy()

    def test_external_file_edit_refreshes_clean_form_without_changing_active_batch(self):
        self.gui._set_running_ui(True)
        changed = dict(self.values, concurrent_count=4, log_level="debug")
        self.config_path.write_text(json.dumps(changed))
        self.assertTrue(self.gui._check_external_config())
        self.assertEqual(self.gui.settings.variables["concurrent_count"].get(), "4")
        self.assertEqual(self.gui.settings.variables["log_level"].get(), "debug")
        self.assertEqual(app.config["concurrent_count"], 2)
        self.assertFalse(self.gui.settings_dirty)

    def test_external_change_cannot_be_overwritten_by_stale_form(self):
        self.gui.settings.variables["concurrent_count"].set("3")
        changed = dict(self.values, concurrent_count=4)
        self.config_path.write_text(json.dumps(changed))
        with patch.object(app.messagebox, "showerror") as error:
            self.assertFalse(self.gui.save_settings())
        error.assert_called_once()
        self.assertEqual(json.loads(self.config_path.read_text())["concurrent_count"], 4)
        self.assertEqual(self.gui.settings.variables["concurrent_count"].get(), "3")

    def test_reload_can_keep_or_discard_unsaved_changes(self):
        self.gui.settings.variables["concurrent_count"].set("3")
        with patch.object(app.messagebox, "askyesno", return_value=False):
            self.assertFalse(self.gui.reload_settings())
        self.assertEqual(self.gui.settings.variables["concurrent_count"].get(), "3")
        with patch.object(app.messagebox, "askyesno", return_value=True):
            self.assertTrue(self.gui.reload_settings())
        self.assertEqual(self.gui.settings.variables["concurrent_count"].get(), "2")

    def test_invalid_external_json_keeps_form_and_prevents_save_and_start(self):
        self.config_path.write_text('{"concurrent_count": broken}')
        self.assertFalse(self.gui._check_external_config())
        self.assertEqual(self.gui.settings.variables["concurrent_count"].get(), "2")
        with patch.object(app.messagebox, "showerror"), patch.object(app.threading, "Thread") as thread:
            self.assertFalse(self.gui.save_settings())
            self.gui.start_registration()
        thread.assert_not_called()
        self.assertEqual(self.config_path.read_text(), '{"concurrent_count": broken}')

    def test_deleted_config_does_not_replace_form_with_defaults(self):
        self.config_path.unlink()
        self.assertFalse(self.gui._check_external_config())
        self.assertEqual(self.gui.settings.variables["concurrent_count"].get(), "2")
        with patch.object(app.messagebox, "showerror"):
            self.assertFalse(self.gui.save_settings())
        self.assertFalse(self.config_path.exists())

    def test_save_normalizes_every_displayed_field_to_written_file(self):
        self.gui.settings.variables["grok2api_auto_add_remote"].set(True)
        self.assertTrue(self.gui.save_settings())
        saved = json.loads(self.config_path.read_text())
        for key, variable in self.gui.settings.variables.items():
            with self.subTest(key=key):
                expected = saved[key]
                actual = variable.get()
                self.assertEqual(actual, expected if isinstance(expected, bool) else str(expected))

    def test_navigation_switching_does_not_edit_configuration(self):
        for index in (1, 3, 2, 0):
            self.gui.settings.nav_buttons[index].invoke()
            self.assertEqual(self.gui.settings.notebook.index("current"), index)
            self.assertEqual(self.gui.settings.nav_buttons[index].cget("style"), "Selected.Nav.TButton")
            self.assertFalse(self.gui.settings_dirty)

    def test_pane_sash_can_resize_log_area(self):
        self.root.deiconify()
        try:
            self.root.update()
            panes = self.gui.panes
            initial = panes.sash_coord(0)[1]
            x = panes.winfo_width() // 2
            panes.event_generate("<ButtonPress-1>", x=x, y=initial + 3)
            self.root.update()
            panes.event_generate("<B1-Motion>", x=x, y=initial - 77)
            self.root.update()
            panes.event_generate("<ButtonRelease-1>", x=x, y=initial - 77)
            self.root.update_idletasks()
            self.assertLess(panes.sash_coord(0)[1], initial)
            for button in self.gui.settings.nav_buttons:
                self.assertEqual(button.winfo_y(), self.gui.settings.nav_buttons[0].winfo_y())
                self.assertEqual(button.winfo_height(), self.gui.settings.nav_buttons[0].winfo_height())
        finally:
            self.root.withdraw()

    def test_invalid_values_do_not_write_or_start(self):
        before = self.config_path.read_text()
        for invalid in ("0", "-1", "abc", "1.5", ""):
            with self.subTest(invalid=invalid), patch.object(app.messagebox, "showerror") as error:
                self.gui.settings.variables["register_count"].set(invalid)
                self.assertFalse(self.gui.save_settings())
                error.assert_called_once()
                self.assertEqual(self.config_path.read_text(), before)
                self.assertFalse(self.gui.is_running)

    def test_concurrency_above_batch_size_is_rejected(self):
        self.gui.settings.variables["concurrent_count"].set("6")
        with self.assertRaisesRegex(ValueError, "并发数"):
            self.gui.settings.collect()

    def test_cpa_upload_requires_address_and_key_only_when_enabled(self):
        panel = self.gui.settings
        panel.variables["cpa_management_key"].set("")
        with self.assertRaisesRegex(ValueError, "Management Key"):
            panel.collect()
        panel.variables["cpa_management_auto_upload"].set(False)
        panel.collect()
        self.assertEqual(str(panel.fields["cpa_management_key"][0][0].cget("state")), "disabled")

    def test_grok_selection_requires_an_import_method(self):
        self.gui.settings.target_var.set("grok2api")
        with self.assertRaisesRegex(ValueError, "入池方式"):
            self.gui.settings.collect()

    def test_start_uses_edited_values_without_network(self):
        self.gui.settings.variables["concurrent_count"].set("3")
        self.gui.settings.target_var.set("不自动导入")
        with patch.object(app.threading, "Thread") as thread:
            self.gui.start_registration()
        self.assertTrue(self.gui.is_running)
        self.assertEqual(app.config["concurrent_count"], 3)
        self.assertFalse(app.config["cpa_export_enabled"])
        thread.return_value.start.assert_called_once()

    def test_background_log_and_state_changes_wait_for_main_thread(self):
        self.gui._set_running_ui(True)
        def background():
            self.gui.log("[+] background test")
            self.gui._set_running_ui(False)
        worker = threading.Thread(target=background)
        worker.start()
        worker.join()
        self.assertTrue(self.gui.is_running)
        self.assertNotIn("background test", self.gui.log_text.get("1.0", "end"))
        self.gui._drain_ui_queue()
        self.assertFalse(self.gui.is_running)
        self.assertIn("background test", self.gui.log_text.get("1.0", "end"))

    def test_save_failure_preserves_old_file_and_does_not_start(self):
        before = self.config_path.read_text()
        with patch.object(app.os, "replace", side_effect=OSError("test failure")), \
                patch.object(app.messagebox, "showerror") as error:
            self.gui.start_registration()
        self.assertFalse(self.gui.is_running)
        self.assertEqual(self.config_path.read_text(), before)
        self.assertEqual(list(self.config_path.parent.glob(".config-*")), [])
        error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
