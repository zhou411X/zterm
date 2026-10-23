"""Terminal preferences and editable GTK keyboard shortcuts."""

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from model import save_json


SHORTCUTS = {
    "new_session": ("新会话", "<Control><Shift>t"),
    "close_session": ("关闭当前会话", "<Control><Shift>w"),
    "copy": ("复制", "<Control><Shift>c"),
    "paste": ("粘贴", "<Control><Shift>v"),
    "search": ("搜索终端", "<Control><Shift>f"),
    "previous_tab": ("上一个标签", "<Control>Page_Up"),
    "next_tab": ("下一个标签", "<Control>Page_Down"),
    "zoom_in": ("放大字体", "<Control>equal"),
    "zoom_out": ("缩小字体", "<Control>minus"),
    "zoom_reset": ("重置字体大小", "<Control>0"),
    **{f"tab_{index}": (f"切换到标签 {index}", f"<Alt>{index}") for index in range(1, 10)},
}


def shortcut_key(key, modifiers):
    modifiers = int(modifiers) & int(Gtk.accelerator_get_default_mod_mask())
    key = Gdk.keyval_to_lower(key)
    # The same physical zoom key produces '=' or '+' depending on Shift.
    if key == Gdk.KEY_plus:
        key = Gdk.KEY_equal
        modifiers &= ~int(Gdk.ModifierType.SHIFT_MASK)
    return key, modifiers


def default_preferences():
    return {"scrollback_lines": 9999,
            "shortcuts": {action: binding for action, (_, binding) in SHORTCUTS.items()}}


class PreferencesStore:
    def __init__(self, path):
        self.path = Path(path)

    def validate(self, data):
        if not isinstance(data, dict):
            raise ValueError("终端设置必须为对象。")
        lines = data.get("scrollback_lines", 9999)
        if type(lines) is not int or not 0 <= lines <= 1000000:
            raise ValueError("滚动缓冲区须为 0–1000000 行的整数。")
        supplied = data.get("shortcuts", {})
        if not isinstance(supplied, dict) or supplied.keys() - SHORTCUTS.keys():
            raise ValueError("快捷键配置包含未知操作。")
        shortcuts = default_preferences()["shortcuts"] | supplied
        used = {}
        for action, binding in shortcuts.items():
            if not isinstance(binding, str):
                raise ValueError("快捷键必须为字符串。")
            if not binding:
                continue
            key, modifiers = Gtk.accelerator_parse(binding)
            key, modifiers = shortcut_key(key, modifiers)
            # Keep ordinary typing and terminal Escape/Enter available.
            modified = modifiers & ~int(Gdk.ModifierType.SHIFT_MASK)
            if (not Gtk.accelerator_valid(key, Gdk.ModifierType(modifiers))
                    or not (modified or Gdk.KEY_F1 <= key <= Gdk.KEY_F35)):
                raise ValueError("请使用 Ctrl、Alt、Super 组合键或功能键，避免占用普通输入。")
            pair = (key, modifiers)
            if pair in used:
                raise ValueError(f"快捷键冲突：{SHORTCUTS[used[pair]][0]} / {SHORTCUTS[action][0]}。")
            used[pair] = action
            shortcuts[action] = Gtk.accelerator_name(key, Gdk.ModifierType(modifiers))
        return {"scrollback_lines": lines, "shortcuts": shortcuts}

    def load(self):
        if not self.path.exists():
            return self.validate(default_preferences())
        return self.validate(json.loads(self.path.read_text()))

    def save(self, data):
        validated = self.validate(data)
        save_json(self.path, validated)
        return validated


class PreferencesDialog(Gtk.Dialog):
    def __init__(self, window):
        super().__init__(title="终端设置", transient_for=window, modal=False,
                         destroy_with_parent=True)
        self.owner = window
        window.preferences_dialog = self
        window.interface_style.attach(self)
        self.connect("destroy", lambda *_: setattr(window, "preferences_dialog", None))
        self.set_default_size(580, 580)
        self.add_buttons("恢复默认", Gtk.ResponseType.REJECT, "取消", Gtk.ResponseType.CANCEL,
                         "保存并应用", Gtk.ResponseType.OK)
        content = self.get_content_area()
        content.set_border_width(12)
        content.set_spacing(10)
        self.notebook = Gtk.Notebook()
        content.pack_start(self.notebook, True, True, 0)
        terminal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Label(label="滚动缓冲区行数", xalign=0), True, True, 0)
        self.scrollback = Gtk.SpinButton.new_with_range(0, 1000000, 1)
        self.scrollback.set_numeric(True)
        row.pack_end(self.scrollback, False, False, 0)
        terminal.pack_start(row, False, False, 0)
        terminal.pack_start(Gtk.Label(label="默认 9999 行；0 表示不保留滚动历史。\n"
                                           "保存后应用到全部会话。减少行数会丢弃超出的旧历史。\n"
                                           "终端响铃已关闭。", xalign=0, wrap=True), False, False, 0)
        self.notebook.append_page(terminal, Gtk.Label(label="终端滚动"))
        shortcuts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=12)
        shortcuts.pack_start(Gtk.Label(label="点击快捷键后按下新组合键；Backspace 清除，Esc 取消录入。\n"
                                            "保存后生效；Ctrl+= 与 Ctrl++ 共用放大键。",
                                      xalign=0, wrap=True), False, False, 0)
        self.bindings = Gtk.ListStore(str, str, int, int)
        tree = Gtk.TreeView(model=self.bindings)
        action_column = Gtk.TreeViewColumn("操作", Gtk.CellRendererText(), text=1)
        action_column.set_min_width(160)
        tree.append_column(action_column)
        self.accel = Gtk.CellRendererAccel(editable=True, accel_mode=Gtk.CellRendererAccelMode.GTK)
        self.accel.connect("accel-edited", self.edited)
        self.accel.connect("accel-cleared", self.cleared)
        tree.append_column(Gtk.TreeViewColumn("快捷键", self.accel, accel_key=2, accel_mods=3))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        shortcuts.pack_start(scroll, True, True, 0)
        self.notebook.append_page(shortcuts, Gtk.Label(label="快捷键"))
        self.error = Gtk.Label(xalign=0, wrap=True)
        content.pack_start(self.error, False, False, 0)
        self.set_values(window.preferences)
        self.connect("response", self.respond)
        self.show_all()

    def set_values(self, settings):
        self.scrollback.set_value(settings["scrollback_lines"])
        self.bindings.clear()
        for action, (label, _) in SHORTCUTS.items():
            key, mods = Gtk.accelerator_parse(settings["shortcuts"][action])
            self.bindings.append((action, label, key, int(mods)))
        self.error.set_text("")

    def values(self):
        self.scrollback.update()
        return {"scrollback_lines": self.scrollback.get_value_as_int(),
                "shortcuts": {row[0]: Gtk.accelerator_name(row[2], Gdk.ModifierType(row[3]))
                              if row[2] else "" for row in self.bindings}}

    def edited(self, renderer, path, key, mods, hardware_keycode):
        row = self.bindings[path]
        previous = row[2], row[3]
        row[2], row[3] = shortcut_key(key, mods)
        try:
            self.owner.preferences_store.validate(self.values())
        except ValueError as error:
            row[2], row[3] = previous
            self.error.set_text(str(error))
        else:
            self.error.set_text("")

    def cleared(self, renderer, path):
        self.bindings[path][2:4] = [0, 0]
        self.error.set_text("")

    def respond(self, dialog, response):
        if response == Gtk.ResponseType.REJECT:
            self.set_values(default_preferences())
        elif response == Gtk.ResponseType.OK:
            if self.owner.preferences_error:
                self.error.set_text("请先修复终端配置并重启，避免覆盖原有设置。\n" +
                                    self.owner.preferences_error)
                return
            try:
                settings = self.owner.preferences_store.save(self.values())
            except (OSError, ValueError) as error:
                self.error.set_text("保存失败：" + str(error))
                return
            self.owner.set_preferences(settings)
            self.destroy()
        else:
            self.destroy()
