"""Real GTK/VTE background rendering and asynchronous image regressions."""

from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import cairo
from PIL import Image

from app import AppearanceDialog, Gdk, Gtk, Window
from background import load_image
from model import AliasStore, AppearanceStore, BACKGROUND_DEFAULTS, default_appearance
from test_workbench import pump_until


def pixel(widget, x=30, y=90):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, widget.get_allocated_width(),
                                 widget.get_allocated_height())
    widget.draw(cairo.Context(surface))
    surface.flush()
    offset = y * surface.get_stride() + x * 4
    blue, green, red, alpha = bytes(surface.get_data()[offset:offset + 4])
    return red, green, blue, alpha


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zterm-background-")
        self.root = Path(self.temporary.name)
        self.path = self.root / 'wallpaper "中文".png'
        Image.new("RGB", (120, 120), (240, 80, 40)).save(self.path)
        self.window = Window(store=AliasStore(self.root / "servers.json"), enable_quota=False,
                             command_factory=lambda _: ["/bin/sh", "-c", "read line"])
        self.window.confirm = lambda *_: True

    def tearDown(self):
        self.window.close_window()
        self.window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        self.temporary.cleanup()

    def test_legacy_defaults_validation_and_persistence(self):
        store = AppearanceStore(self.root / "appearance.json")
        old = {key: value for key, value in default_appearance().items() if key not in BACKGROUND_DEFAULTS}
        store.save(old)
        self.assertEqual(store.load(), default_appearance())
        before = store.path.read_bytes()
        for changes in ({"background_opacity": 0}, {"background_opacity": True},
                        {"background_blur": float("nan")}, {"background_blur": 41},
                        {"background_image_opacity": "50"}, {"background_on_terminal": 1},
                        {"background_image": "relative.png"}):
            with self.assertRaises(ValueError):
                store.save(dict(default_appearance(), **changes))
            self.assertEqual(store.path.read_bytes(), before)

    def test_image_terminal_opt_in_transparency_cancel_save_restart(self):
        window = self.window
        window.server_list.select_row(window.rows[""])
        session = window.current_session()
        pump_until(lambda: session.alive)
        original, pid = window.appearance.copy(), session.pid
        dialog = AppearanceDialog(window)
        dialog.effects["background_image_opacity"].set_value(100)
        dialog.set_background_image(str(self.path))
        pump_until(lambda: window.background_renderer.pixbuf is not None)
        pump_until(lambda: session.background.get_allocated_height() > 100)
        # With image opt-in off, an opaque terminal keeps its own theme color.
        red, green, blue, alpha = pixel(session.background)
        self.assertEqual((red, green, blue, alpha), (48, 10, 36, 255))
        dialog.background_on_terminal.set_active(True)
        pump_until(lambda: pixel(session.background) == (240, 80, 40, 255))
        self.assertEqual(pixel(session.background), (240, 80, 40, 255))
        dialog.effects["background_opacity"].set_value(50)
        pump_until(lambda: abs(pixel(session.background)[3] - 128) <= 1)
        red, green, blue, alpha = pixel(session.background)
        self.assertTrue(119 <= red <= 121 and 39 <= green <= 41 and 19 <= blue <= 21)
        self.assertEqual(session.pid, pid)
        self.assertEqual(session.terminal.get_opacity(), 1)
        # Explicit ANSI backgrounds stay opaque despite the translucent default
        # background, as do the foreground glyphs rendered by VTE.
        session.terminal.feed(b"\x1b[H\x1b[48;2;10;180;70m   \x1b[0m")
        terminal = session.terminal
        padding = terminal.get_style_context().get_padding(Gtk.StateFlags.NORMAL)
        top = padding.top
        if hasattr(terminal, "get_yalign") and terminal.get_yalign().value_nick == "end":
            top = (terminal.get_allocated_height() - padding.bottom
                   - terminal.get_row_count() * terminal.get_char_height())
        pump_until(lambda: pixel(terminal, 5, top + 2) == (10, 180, 70, 255))
        self.assertEqual(window.appearance, original)
        session.terminal.feed_child(b"background-keeps-pty-alive\n")
        pump_until(lambda: not session.active)
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assertEqual(window.background_settings, BACKGROUND_DEFAULTS)
        self.assertEqual(window.appearance, original)
        self.assertFalse(window.appearance_store.path.exists())

        dialog = AppearanceDialog(window)
        dialog.set_background_image(str(self.path))
        dialog.effects["background_blur"].set_value(12)
        dialog.background_on_terminal.set_active(True)
        pump_until(lambda: not window.background_renderer.loading)
        expected = dialog.values()
        dialog.response(Gtk.ResponseType.OK)
        self.assertEqual(window.appearance_store.load(), expected)
        window.destroy()
        self.window = Window(store=AliasStore(self.root / "servers.json"), enable_quota=False)
        self.assertEqual(self.window.appearance, expected)
        pump_until(lambda: self.window.background_renderer.pixbuf is not None)
        self.assertFalse(self.window.files.panel.get_visible())

    def test_image_errors_clear_and_chooser_is_nonmodal(self):
        dialog = AppearanceDialog(self.window)
        dialog.set_background_image(str(self.root / "missing.png"))
        pump_until(lambda: not self.window.background_renderer.loading)
        self.assertIn("无法读取背景图片", dialog.error.get_text())
        self.assertFalse(dialog.get_widget_for_response(Gtk.ResponseType.OK).get_sensitive())
        dialog.response(Gtk.ResponseType.OK)
        self.assertTrue(dialog.get_visible())
        self.assertFalse(self.window.appearance_store.path.exists())
        dialog.set_background_image("")
        self.assertTrue(dialog.get_widget_for_response(Gtk.ResponseType.OK).get_sensitive())
        settings = Gtk.Settings.get_default()
        previous = settings.get_property("gtk-dialogs-use-header")
        settings.set_property("gtk-dialogs-use-header", True)
        try:
            dialog.choose_background()
            chooser = dialog.image_dialog
            pump_until(lambda: chooser.get_mapped())
            self.assertFalse(chooser.get_modal())
            self.assertIsNone(Gtk.grab_get_current())
            for response in (Gtk.ResponseType.CANCEL, Gtk.ResponseType.OK):
                self.assertTrue(chooser.get_widget_for_response(response).get_mapped())
            dialog.choose_background()
            self.assertIs(dialog.image_dialog, chooser)
            dialog.destroy()
            self.assertIsNone(dialog.image_dialog)
        finally:
            settings.set_property("gtk-dialogs-use-header", previous)

    def test_system_theme_effects_are_scoped_and_cancel_restores(self):
        dialog = AppearanceDialog(self.window)
        dialog.interface_theme.set_active_id("system")
        dialog.effects["background_opacity"].set_value(60)
        self.assertTrue(self.window.get_style_context().has_class("zterm-backdrop"))
        self.assertFalse(dialog.get_style_context().has_class("zterm-backdrop"))
        header = self.window.header.get_style_context().get_background_color(Gtk.StateFlags.NORMAL)
        self.assertAlmostEqual(header.alpha, 0.6, places=2)
        dialog.response(Gtk.ResponseType.CANCEL)
        self.assertFalse(self.window.get_style_context().has_class("zterm-backdrop"))
        self.assertEqual(self.window.background_settings, BACKGROUND_DEFAULTS)

    def test_blur_bounds_and_worker_discards_stale_results(self):
        image = Image.new("RGB", (100, 100), "black")
        image.paste("white", (50, 0, 100, 100))
        image.save(self.path)
        _, _, sharp = load_image(str(self.path), 0)
        _, _, blurred = load_image(str(self.path), 10)
        self.assertEqual(sharp[(50 * 100 + 45) * 4], 0)
        self.assertTrue(0 < blurred[(50 * 100 + 45) * 4] < 255)
        entered, release = threading.Event(), threading.Event()
        actual = load_image
        def slow(path, blur):
            entered.set()
            release.wait(5)
            return actual(path, blur)
        renderer = self.window.background_renderer
        with patch("background.load_image", side_effect=slow):
            renderer.request(dict(default_appearance(), background_image=str(self.path)))
            pump_until(entered.is_set)
            # An in-flight decode must never restore an image that was cleared.
            renderer.request(default_appearance())
            release.set()
            pump_until(lambda: renderer.pending is None)
        renderer.deliver(renderer.generation - 1, (1, 1, b"\xff" * 4), "")
        self.assertIsNone(renderer.pixbuf)
        self.assertFalse(renderer.loading)
        renderer.close()
        renderer.deliver(renderer.generation, (1, 1, b"\xff" * 4), "")
        self.assertIsNone(renderer.pixbuf)


if __name__ == "__main__":
    unittest.main()
