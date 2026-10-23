"""Persistent preferences and real VTE search/keyboard behavior (xvfb-run)."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from model import AliasStore
from preferences import PreferencesStore, SHORTCUTS, default_preferences, shortcut_key
from test_workbench import pump_until


class PreferencesStoreTests(unittest.TestCase):
    def test_defaults_roundtrip_and_partial_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "terminal.json")
            self.assertEqual(store.load()["scrollback_lines"], 9999)
            self.assertFalse(store.path.exists())
            settings = store.save({"scrollback_lines": 1234, "shortcuts": {"search": "F6", "copy": ""}})
            self.assertEqual(store.load(), settings)
            self.assertEqual(settings["shortcuts"]["copy"], "")
            self.assertEqual(set(settings["shortcuts"]), set(SHORTCUTS))

    def test_invalid_values_and_conflicts_preserve_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "terminal.json")
            store.save(default_preferences())
            before = store.path.read_bytes()
            for value in (-1, True, 1.5, "9999", 1000001):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    store.save({"scrollback_lines": value})
            for shortcuts in ({"search": "x"}, {"search": "<Shift>x"}, {"search": "Escape"},
                              {"search": "NotAKey"}, {"search": []}, {"unknown": "F7"},
                              {"search": "<Control><Shift>t"}, {"search": "<Control>plus"}):
                with self.subTest(shortcuts=shortcuts), self.assertRaises(ValueError):
                    store.save({"shortcuts": shortcuts})
            self.assertEqual(store.path.read_bytes(), before)

    def test_corrupt_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PreferencesStore(Path(directory) / "terminal.json")
            store.path.write_text("{broken")
            with self.assertRaises(ValueError):
                store.load()
            self.assertEqual(store.path.read_text(), "{broken")


class TerminalPreferencesTests(unittest.TestCase):
    def setUp(self):
        from app import Window
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = AliasStore(Path(self.directory.name) / "servers.json")
        self.store.save(["other"])
        self.window = Window(store=self.store, enable_quota=False, command_factory=lambda _: ["/bin/cat"])
        self.window.confirm = lambda *_: True
        self.addCleanup(self.close_window)
        self.window.server_list.select_row(self.window.rows[""])
        pump_until(lambda: self.window.current_session().alive)
        self.terminal = self.window.current_session().terminal

    def close_window(self):
        from app import Gtk
        self.window.close_window()
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)

    def feed(self, terminal, text):
        from app import Vte
        terminal.feed(text.encode())
        marker = text.splitlines()[-1]
        pump_until(lambda: marker in terminal.get_text_format(Vte.Format.TEXT))

    def selected(self, terminal=None):
        from app import Vte
        return (terminal or self.terminal).get_text_selected(Vte.Format.TEXT) or ""

    def search(self, text, ignore_case=True):
        self.window.terminal_search_button.set_active(True)
        self.window.search_ignore_case.set_active(ignore_case)
        self.window.terminal_search_entry.set_text(text)
        self.window.sync_terminal_search(force=True)

    def event(self, key, mods=0):
        from app import Gdk
        return SimpleNamespace(keyval=Gdk.keyval_from_name(key), state=Gdk.ModifierType(mods))

    def test_search_literal_case_unicode_navigation_and_clear(self):
        self.feed(self.terminal, "Alpha\r\nalpha\r\nALPHA\r\na.b [x] 中文\r\nSEARCH-READY\r\n")
        self.search("alpha")
        self.assertEqual(self.selected().lower(), "alpha")
        self.window.find_terminal(True)
        self.assertEqual(self.selected(), "alpha")
        self.window.find_terminal(False)
        self.assertEqual(self.selected(), "Alpha")
        self.window.search_ignore_case.set_active(False)
        self.assertEqual(self.selected(), "alpha")
        self.search("a.b [x] 中文", False)
        self.assertEqual(self.selected(), "a.b [x] 中文")
        self.window.find_terminal(False)
        self.assertEqual(self.selected(), "a.b [x] 中文")
        self.window.find_terminal(True)
        self.assertEqual(self.selected(), "a.b [x] 中文")
        self.search("DOES-NOT-EXIST")
        self.assertEqual(self.window.terminal_search_status.get_text(), "未找到匹配")
        self.assertFalse(self.terminal.get_has_selection())
        self.search("Alpha", False)
        self.assertTrue(self.terminal.get_has_selection())
        self.search("")
        self.assertFalse(self.terminal.get_has_selection())
        self.search("Alpha", False)
        self.window.terminal_search_button.set_active(False)
        self.assertFalse(self.window.terminal_search_bar.get_visible())
        self.assertFalse(self.terminal.get_has_selection())
        self.assertIsNone(self.terminal.search_get_regex())

    def test_search_history_and_session_switch(self):
        self.feed(self.terminal, "HISTORY-NEEDLE\r\n" + "history filler\r\n" * 150 + "HISTORY-READY\r\n")
        self.search("HISTORY-NEEDLE")
        self.assertEqual(self.selected(), "HISTORY-NEEDLE")
        second = self.window.new_session()
        pump_until(lambda: second.alive)
        self.assertIs(self.window.search_terminal, second.terminal)
        self.assertFalse(self.terminal.get_has_selection())
        self.feed(second.terminal, "SECOND-ONLY\r\nSECOND-READY\r\n")
        self.search("SECOND-ONLY")
        self.assertEqual(self.selected(second.terminal), "SECOND-ONLY")
        self.window.groups[""].set_current_page(0)
        self.assertIs(self.window.search_terminal, self.terminal)
        self.assertEqual(self.window.terminal_search_status.get_text(), "未找到匹配")
        self.window.server_list.select_row(self.window.rows["other"])
        pump_until(lambda: self.window.current_session().alive)
        self.assertIs(self.window.search_terminal, self.window.current_session().terminal)
        self.window.close_session(self.window.current_session())
        self.assertIsNone(self.window.search_terminal)
        self.assertEqual(self.window.terminal_search_status.get_text(), "当前没有会话")

    def test_scrollback_applies_to_all_sessions_and_new_windows_with_bell_off(self):
        from app import Gtk, Window, AppearanceDialog
        self.assertEqual(self.terminal.get_scrollback_lines(), 9999)
        self.assertFalse(self.terminal.get_audible_bell())
        self.window.server_list.select_row(self.window.rows["other"])
        pump_until(lambda: self.window.current_session().alive)
        remote = self.window.current_session()
        dialog = self.window.show_terminal_settings()
        dialog.scrollback.set_value(1234)
        dialog.response(Gtk.ResponseType.OK)
        self.assertEqual(self.terminal.get_scrollback_lines(), 1234)
        self.assertEqual(remote.terminal.get_scrollback_lines(), 1234)
        fresh = self.window.new_session()
        pump_until(lambda: fresh.alive)
        self.assertEqual(fresh.terminal.get_scrollback_lines(), 1234)
        self.assertFalse(fresh.terminal.get_audible_bell())
        other = Window(store=self.store, enable_quota=False, command_factory=lambda _: ["/bin/cat"])
        try:
            self.assertEqual(other.preferences["scrollback_lines"], 1234)
            preview = AppearanceDialog(other)
            self.assertFalse(preview.preview.get_audible_bell())
            preview.destroy()
        finally:
            other.close_window()
            other.destroy()

    def test_reducing_scrollback_discards_old_history(self):
        from app import Gtk
        self.feed(self.terminal, "OLD-NEEDLE\r\n" + "filler\r\n" * 150 + "RECENT-READY\r\n")
        self.search("OLD-NEEDLE")
        self.assertEqual(self.selected(), "OLD-NEEDLE")
        dialog = self.window.show_terminal_settings()
        dialog.scrollback.set_value(0)
        dialog.response(Gtk.ResponseType.OK)
        self.search("OLD-NEEDLE")
        self.assertEqual(self.window.terminal_search_status.get_text(), "未找到匹配")

    def test_keyboard_event_opens_search_and_zoom_aliases(self):
        from app import Gdk, Gtk
        window = self.window
        window.present()
        self.terminal.grab_focus()
        pump_until(lambda: self.terminal.has_focus())
        event = Gdk.Event.new(Gdk.EventType.KEY_PRESS)
        event.window = window.get_window()
        event.send_event = True
        event.time = Gdk.CURRENT_TIME
        event.keyval = Gdk.KEY_F6
        event.state = Gdk.ModifierType(0)
        keymap = Gdk.Keymap.get_for_display(window.get_display())
        success, entries = keymap.get_entries_for_keyval(event.keyval)
        self.assertTrue(success)
        event.hardware_keycode = entries[0].keycode
        event.group = entries[0].group
        event.set_device(window.get_display().get_default_seat().get_keyboard())
        window.set_preferences(window.preferences_store.save({"shortcuts": {"search": "F6"}}))
        Gtk.main_do_event(event)
        self.assertTrue(window.terminal_search_bar.get_visible())
        window.terminal_search_entry.emit("stop-search")
        self.assertFalse(window.terminal_search_bar.get_visible())
        ctrl = Gdk.ModifierType.CONTROL_MASK
        self.assertTrue(window.key_pressed(window, self.event("plus", ctrl | Gdk.ModifierType.SHIFT_MASK)))
        self.assertAlmostEqual(self.terminal.get_font_scale(), 1.1)
        self.assertTrue(window.key_pressed(window, self.event("equal", ctrl)))
        self.assertAlmostEqual(self.terminal.get_font_scale(), 1.2)

    def test_preferences_cancel_defaults_conflicts_and_save_failure(self):
        from app import Gtk, Gdk
        dialog = self.window.show_shortcuts()
        self.assertIs(self.window.show_terminal_settings(), dialog)
        self.assertEqual(self.window.preferences["scrollback_lines"], 9999)
        dialog.scrollback.set_value(99)
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assertEqual(self.terminal.get_scrollback_lines(), 9999)
        self.assertFalse(self.window.preferences_store.path.exists())
        dialog = self.window.show_shortcuts()
        search_row = str(list(SHORTCUTS).index("search"))
        old = tuple(dialog.bindings[search_row][2:4])
        dialog.edited(dialog.accel, search_row, Gdk.KEY_t,
                      Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, 0)
        self.assertIn("冲突", dialog.error.get_text())
        self.assertEqual(tuple(dialog.bindings[search_row][2:4]), old)
        dialog.cleared(dialog.accel, search_row)
        self.assertEqual(dialog.values()["shortcuts"]["search"], "")
        dialog.response(Gtk.ResponseType.REJECT)
        self.assertEqual(shortcut_key(*Gtk.accelerator_parse(dialog.values()["shortcuts"]["search"])),
                         shortcut_key(*Gtk.accelerator_parse(SHORTCUTS["search"][1])))
        dialog.scrollback.set_value(99)
        with patch.object(self.window.preferences_store, "save", side_effect=OSError("read only")):
            dialog.response(Gtk.ResponseType.OK)
        self.assertIn("保存失败", dialog.error.get_text())
        self.assertEqual(self.terminal.get_scrollback_lines(), 9999)
        self.assertIs(self.window.preferences_dialog, dialog)

    def test_custom_shortcuts_dispatch_disable_and_preserve_entry_editing(self):
        from app import Gdk, Gtk
        dialog = self.window.show_shortcuts()
        index = str(list(SHORTCUTS).index("search"))
        dialog.edited(dialog.accel, index, Gdk.KEY_F6, Gdk.ModifierType(0), 0)
        dialog.response(Gtk.ResponseType.OK)
        ctrlshift = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        self.assertFalse(self.window.key_pressed(self.window, self.event("f", ctrlshift)))
        self.assertTrue(self.window.key_pressed(self.window, self.event("F6")))
        self.assertTrue(self.window.terminal_search_bar.get_visible())
        self.window.terminal_search_entry.grab_focus()
        with patch.object(self.terminal, "paste_clipboard") as paste:
            self.assertFalse(self.window.key_pressed(self.window, self.event("v", ctrlshift)))
            paste.assert_not_called()
        settings = self.window.preferences_store.save({"shortcuts": {"new_session": "F7", "close_session": ""}})
        self.window.set_preferences(settings)
        self.assertFalse(self.window.key_pressed(self.window, self.event("w", ctrlshift)))
        self.assertTrue(self.window.key_pressed(self.window, self.event("F7")))
        self.assertEqual(self.window.groups[""].get_n_pages(), 2)
        self.assertFalse(self.window.key_pressed(self.window, self.event("c", Gdk.ModifierType.CONTROL_MASK)))
        self.assertFalse(self.window.key_pressed(self.window, self.event("1", Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SHIFT_MASK)))
        self.assertFalse(self.window.key_pressed(self.window, self.event("x")))


if __name__ == "__main__":
    unittest.main()
