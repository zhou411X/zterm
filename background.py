"""Background-only transparency and asynchronous, bounded wallpaper processing."""

import logging
import threading
import warnings
import weakref

import cairo
import gi
from PIL import Image, ImageFilter, ImageOps

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from model import interface_colors

LOG = logging.getLogger("zterm.background")


def enabled(settings):
    return settings.get("background_opacity", 1) < 1 or bool(settings.get("background_image"))


def load_image(path, blur):
    # Decode only in the worker. Bound both decoded input and retained RGBA data.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("图片超过 4000 万像素，请缩小后再试")
            source.thumbnail((2560, 2560))
            image = ImageOps.exif_transpose(source).convert("RGBA")
        if blur:
            image = image.filter(ImageFilter.GaussianBlur(blur))
        return image.width, image.height, image.tobytes()


class BackgroundRenderer:
    """One worker per window; rapid changes replace pending work, never queue it."""

    def __init__(self):
        self.pixbuf = None
        self.error = ""
        self.loading = False
        self.closed = False
        self.key = None
        self.generation = 0
        self.pending = None
        self.condition = threading.Condition()
        self.widgets = weakref.WeakSet()
        self.changed = None
        self.worker = None

    def request(self, settings):
        key = (settings.get("background_image", ""), settings.get("background_blur", 0))
        if self.closed or key == self.key:
            return
        self.key = key
        self.generation += 1
        self.pixbuf, self.error = None, ""
        self.loading = bool(key[0])
        with self.condition:
            self.pending = (self.generation, key) if key[0] else None
            if key[0] and self.worker is None:
                self.worker = threading.Thread(target=self.run, name="wallpaper", daemon=True)
                self.worker.start()
            self.condition.notify()
        self.notify()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                generation, (path, blur) = self.pending
                self.pending = None
            try:
                result, error = load_image(path, blur), ""
            except (OSError, ValueError, Image.DecompressionBombError,
                    Image.DecompressionBombWarning) as exc:
                result, error = None, "无法读取背景图片，请选择有效的 PNG、JPEG 或 WebP 图片（不超过 4000 万像素）。"
                LOG.warning("Background load failed type=%s", type(exc).__name__)
            with self.condition:
                if not self.closed and generation == self.generation:
                    GLib.idle_add(self.deliver, generation, result, error)

    def deliver(self, generation, result, error):
        if self.closed or generation != self.generation:
            return False
        self.loading, self.error = False, error
        if result is not None:
            width, height, pixels = result
            self.pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB, True, 8, width, height, width * 4)
        self.notify()
        return False

    def notify(self):
        for widget in self.widgets:
            widget.queue_draw()
        if self.changed:
            self.changed()

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = None
            self.condition.notify()
        self.changed = None
        self.widgets.clear()
        self.pixbuf = None


class BackgroundBox(Gtk.Box):
    """Paint a backdrop before children, keeping foreground widgets fully opaque."""

    def __init__(self, renderer, settings, terminal=False, anchor=None, **kwargs):
        super().__init__(**kwargs)
        self.renderer, self.settings, self.terminal = renderer, settings.copy(), terminal
        self.anchor = weakref.ref(anchor) if anchor is not None else None
        renderer.widgets.add(self)

    def update(self, settings):
        self.settings = settings.copy()
        self.queue_draw()

    def do_draw(self, cr):
        settings = self.settings
        if enabled(settings):
            width, height = self.get_allocated_width(), self.get_allocated_height()
            colors = interface_colors(settings)
            if self.terminal:
                color = settings["background"]
            elif colors:
                color = colors["background"]
            else:
                found, value = self.get_style_context().lookup_color("theme_bg_color")
                color = value.to_string() if found else settings["background"]
            rgba = Gdk.RGBA()
            rgba.parse(color)
            cr.save()
            cr.rectangle(0, 0, width, height)
            cr.clip()
            cr.push_group()
            Gdk.cairo_set_source_rgba(cr, rgba)
            cr.paint()
            pixbuf = self.renderer.pixbuf
            if pixbuf and (not self.terminal or settings.get("background_on_terminal", False)):
                image_width, image_height, x, y = width, height, 0, 0
                anchor = self.anchor() if self.anchor else None
                if anchor is not None:
                    x, y = self.translate_coordinates(anchor, 0, 0)
                    image_width, image_height = anchor.get_allocated_width(), anchor.get_allocated_height()
                scale = max(image_width / pixbuf.get_width(), image_height / pixbuf.get_height())
                cr.save()
                cr.translate((image_width - pixbuf.get_width() * scale) / 2 - x,
                             (image_height - pixbuf.get_height() * scale) / 2 - y)
                cr.scale(scale, scale)
                Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                cr.paint_with_alpha(settings.get("background_image_opacity", 0.25))
                cr.restore()
            pattern = cr.pop_group()
            # Replace this region rather than letting the parent's wallpaper
            # show through terminals whose "apply image" switch is off.
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_source(pattern)
            cr.paint_with_alpha(settings.get("background_opacity", 1))
            cr.restore()
        return Gtk.Box.do_draw(self, cr)
