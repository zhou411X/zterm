"""GTK file browser and transfer queue, independent of terminal PTYs."""

from datetime import datetime
import logging
from pathlib import Path
import posixpath
import stat

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from transfer import TransferManager, endpoint_for, entry_name

LOG = logging.getLogger("zterm.files")


def size_text(value):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


def action_button(label, callback):
    widget = Gtk.Button(label=label)
    widget.connect("clicked", lambda *_: callback())
    return widget


class FilePanel(Gtk.Box):
    def __init__(self, controller):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        self.controller = controller
        self.endpoint = None
        self.paths = {}
        self.location = ""
        self.generation = 0
        self.listing = None
        self.closed = False
        self.set_size_request(330, -1)
        self.server_label = Gtk.Label(xalign=0)
        self.server_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.pack_start(self.server_label, False, False, 0)
        navigation = Gtk.Box(spacing=4)
        navigation.pack_start(action_button("↑", self.parent_directory), False, False, 0)
        self.path = Gtk.Entry(placeholder_text="远端目录（首次进入登录目录）")
        self.path.connect("activate", lambda *_: self.load(self.path.get_text() or "."))
        navigation.pack_start(self.path, True, True, 0)
        navigation.pack_end(action_button("刷新", self.refresh), False, False, 0)
        self.pack_start(navigation, False, False, 0)
        self.rows = Gtk.ListStore(str, str, str, str, int)
        self.tree = Gtk.TreeView(model=self.rows)
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        name = Gtk.TreeViewColumn("名称")
        icon = Gtk.CellRendererPixbuf()
        name.pack_start(icon, False)
        name.add_attribute(icon, "icon-name", 0)
        text = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        name.pack_start(text, True)
        name.add_attribute(text, "text", 1)
        name.set_expand(True)
        name.set_resizable(True)
        self.tree.append_column(name)
        self.tree.append_column(Gtk.TreeViewColumn("大小", Gtk.CellRendererText(), text=2))
        self.tree.append_column(Gtk.TreeViewColumn("修改时间", Gtk.CellRendererText(), text=3))
        self.tree.set_tooltip_column(1)
        self.tree.connect("row-activated", self.activate)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.tree)
        self.pack_start(scroll, True, True, 0)
        self.tools = Gtk.Box(spacing=4)
        for label, callback in (("上传", self.upload_files), ("上传目录", self.upload_directory),
                                ("下载", self.download)):
            self.tools.pack_start(action_button(label, callback), True, True, 0)
        self.pack_start(self.tools, False, False, 0)
        self.edit_tools = Gtk.Box(spacing=4)
        for label, callback in (("新建目录", self.mkdir), ("重命名", self.rename), ("删除", self.delete)):
            self.edit_tools.pack_start(action_button(label, callback), True, True, 0)
        self.pack_start(self.edit_tools, False, False, 0)
        self.status = Gtk.Label(label="选择远程服务器后浏览文件。", xalign=0, wrap=True,
                                max_width_chars=44)
        self.status.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.pack_start(self.status, False, False, 0)
        self.drag_dest_set(Gtk.DestDefaults.ALL,
                           [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.dropped)
        self.ready(False)

    def ready(self, enabled):
        self.tree.set_sensitive(enabled)
        self.tools.set_sensitive(enabled)
        self.edit_tools.set_sensitive(enabled)

    def set_server(self, endpoint):
        if endpoint == self.endpoint:
            return
        self.generation += 1
        if self.listing is not None:
            self.listing.cancel()
            self.listing = None
        self.endpoint = endpoint
        self.location = self.paths.get(endpoint, "") if endpoint else ""
        self.path.set_text(self.location)
        self.rows.clear()
        self.ready(False)
        self.server_label.set_text(endpoint.label if endpoint else "本地 / 未选择服务器")
        self.status.set_text("点击刷新连接。" if endpoint else "请选择远程服务器。")

    def refresh(self):
        self.load(self.location or ".")

    def parent_directory(self):
        if self.location:
            self.load(posixpath.dirname(self.location.rstrip("/")) or "/")

    def load(self, path):
        if self.endpoint is None or self.closed:
            return
        self.generation += 1
        generation, endpoint = self.generation, self.endpoint
        LOG.debug("Directory request generation=%s", generation)
        if self.listing is not None:
            self.listing.cancel()
        self.ready(False)
        self.status.set_text("正在连接 / 读取目录…")

        def finished(job, result, error):
            if self.closed or generation != self.generation or endpoint != self.endpoint:
                LOG.debug("Discarded stale directory response generation=%s", generation)
                return
            self.listing = None
            if error:
                self.status.set_text(error)
                self.path.set_text(self.location)
                return
            self.location, entries = result
            self.paths[endpoint] = self.location
            self.path.set_text(self.location)
            self.rows.clear()
            for name, mode, size, modified in entries:
                icon = ("folder" if stat.S_ISDIR(mode) else
                        "emblem-symbolic-link" if stat.S_ISLNK(mode) else "text-x-generic")
                self.rows.append([icon, name, "—" if stat.S_ISDIR(mode) else size_text(size),
                                  datetime.fromtimestamp(modified).strftime("%m-%d %H:%M"), mode])
            self.status.set_text(f"{len(entries)} 项 · 点“上传”后双击文件即可上传，也可拖入")
            self.ready(True)
        self.listing = self.controller.manager.browse(endpoint, path, finished)

    def selected(self):
        model, rows = self.tree.get_selection().get_selected_rows()
        return [(model[row][1], model[row][4]) for row in rows]

    def activate(self, _, path, column):
        name, mode = self.rows[path][1], self.rows[path][4]
        if stat.S_ISDIR(mode):
            self.load(posixpath.join(self.location, name))
        elif stat.S_ISREG(mode):
            self.download()
        else:
            self.status.set_text("首版不跟随符号链接；请直接进入实际目录或选择普通文件。")

    def choose(self, title, action, on_accept, multiple=False, selection=()):
        # Keep response buttons in the action area even when GNOME enables
        # gtk-dialogs-use-header; our independent titlebar must not replace them.
        dialog = Gtk.FileChooserDialog(title=title, action=action, use_header_bar=False)
        accept = "上传" if action == Gtk.FileChooserAction.OPEN else "选择"
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, accept, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_select_multiple(multiple)
        endpoint, location = self.endpoint, self.location
        return self.controller.present_dialog(
            dialog, dialog.get_filenames, on_accept,
            identity=(endpoint, location, tuple(selection)),
            description=f"{endpoint.label} · {location}" if endpoint else "")

    def upload_files(self):
        endpoint, location = self.endpoint, self.location
        return self.choose("选择上传的文件", Gtk.FileChooserAction.OPEN,
                           lambda paths: self.upload_paths(endpoint, location, paths), True)

    def upload_directory(self):
        endpoint, location = self.endpoint, self.location
        return self.choose("选择上传的目录", Gtk.FileChooserAction.SELECT_FOLDER,
                           lambda paths: self.upload_paths(endpoint, location, paths))

    def upload_paths(self, endpoint, location, paths):
        if endpoint is None or not location:
            return
        for path in paths:
            source = str(Path(path).absolute())
            self.controller.submit(endpoint, "upload", source,
                                   posixpath.join(location, Path(source).name))

    def download(self):
        endpoint, location, selected = self.endpoint, self.location, self.selected()
        if endpoint is None or not selected:
            self.status.set_text("请先选择要下载的文件或目录。")
            return
        def accepted(paths):
            if not paths:
                return
            for name, _ in selected:
                self.controller.submit(endpoint, "download", posixpath.join(location, name),
                                       str(Path(paths[0]) / name))
        return self.choose("选择下载到的本地目录", Gtk.FileChooserAction.SELECT_FOLDER, accepted,
                           selection=selected)

    def mkdir(self):
        endpoint, location = self.endpoint, self.location
        def accepted(name):
            if endpoint and location:
                self.controller.submit(endpoint, "mkdir", posixpath.join(location, name))
        return self.controller.ask_name(
            "新建远端目录", "", accepted, identity=(endpoint, location),
            description=f"{endpoint.label} · {location}" if endpoint else "")

    def rename(self):
        selected = self.selected()
        if len(selected) != 1:
            self.status.set_text("重命名时请选择一项。")
            return
        endpoint, location = self.endpoint, self.location
        old = selected[0][0]
        def accepted(name):
            if name != old:
                self.controller.submit(endpoint, "rename", posixpath.join(location, old),
                                       posixpath.join(location, name))
        return self.controller.ask_name(
            "重命名（不会覆盖已有目标）", old, accepted, identity=(endpoint, location, old),
            description=f"{endpoint.label} · {posixpath.join(location, old)}")

    def delete(self):
        endpoint, location, selected = self.endpoint, self.location, self.selected()
        if not selected:
            self.status.set_text("请先选择要删除的文件或空目录。")
            return
        if self.controller.owner.confirm("删除远端文件？",
                                         "将永久删除所选文件或空目录，不会移入回收站。\n" +
                                         "\n".join(name for name, _ in selected)):
            for name, _ in selected:
                self.controller.submit(endpoint, "delete", posixpath.join(location, name))

    def dropped(self, _, context, x, y, data, info, timestamp):
        paths = [Gio.File.new_for_uri(uri).get_path() for uri in data.get_uris()]
        accepted = bool(self.endpoint and self.location and self.tools.get_sensitive()
                        and paths and all(paths))
        if accepted:
            self.upload_paths(self.endpoint, self.location, paths)
        Gtk.drag_finish(context, accepted, False, timestamp)

    def close(self):
        self.closed = True
        if self.listing is not None:
            self.listing.cancel()


class FileTransfers:
    STATES = dict(queued="等待", connecting="连接 / 认证", running="传输中", done="完成",
                  failed="失败", cancelled="已取消", conflict="等待同名处理",
                  skipped="已跳过", retried="已重新排队")
    ACTIONS = dict(upload="上传", download="下载", mkdir="新建目录", rename="重命名", delete="删除")

    def __init__(self, owner, connection_factory=None):
        self.owner, self.closed = owner, False
        self.dialogs = {}
        self.sidebar_width = 335
        self.pane_width = None
        self.restoring_width = False
        options = {"connection_factory": connection_factory} if connection_factory else {}
        self.manager = TransferManager(self.changed, self.dispatch, **options)
        self.panel = FilePanel(self)
        self.queue = Gtk.Expander(label="文件传输")
        self.queue.set_no_show_all(True)
        self.task_rows = {}
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(110)
        scroll.set_max_content_height(190)
        scroll.set_propagate_natural_height(True)
        scroll.add(self.listbox)
        self.queue.add(scroll)
        self.panel.pack_end(self.queue, False, False, 0)
        scroll.show_all()
        owner.connect("destroy", self.close)

    def dispatch(self, callback, *args):
        def deliver():
            if not self.closed:
                callback(*args)
            return False
        GLib.idle_add(deliver)

    def select_server(self, alias):
        endpoint = endpoint_for(alias, self.owner.profiles.get(alias)) if alias else None
        changed = endpoint != self.panel.endpoint
        self.panel.set_server(endpoint)
        if self.owner.files_toggle.get_active() and (changed or not self.panel.location):
            self.panel.refresh()

    def show_server(self, alias):
        # File browsing alone must not create a terminal session.
        self.owner.files_toggle.handler_block_by_func(self.toggle)
        try:
            self.owner.files_toggle.set_active(True)
        finally:
            self.owner.files_toggle.handler_unblock_by_func(self.toggle)
        self.set_visible(True)
        self.panel.set_server(endpoint_for(alias, self.owner.profiles.get(alias)))
        self.panel.refresh()

    def toggle(self, toggle):
        self.set_visible(toggle.get_active())
        if toggle.get_active():
            self.select_server(self.owner.current)
            if self.panel.location and self.panel.listing is None:
                self.panel.refresh()
        else:
            # Hiding the panel leaves queued transfers running.
            self.panel.generation += 1
            if self.panel.listing is not None:
                self.panel.listing.cancel()
                self.panel.listing = None

    def bind_pane(self):
        pane = self.owner.file_pane
        self.pane_width = pane.get_allocated_width()
        pane.connect("size-allocate", self.pane_resized)
        pane.connect("notify::position", self.pane_position_changed)

    def set_visible(self, visible):
        pane = self.owner.file_pane
        was_visible = self.panel.get_visible()
        if not visible and was_visible and self.panel.get_allocated_width() > 0:
            self.sidebar_width = pane.get_allocated_width() - pane.get_position()
        self.panel.set_visible(visible)
        self.owner.files_toggle.set_tooltip_text("收起文件栏（SFTP）" if visible else "展开文件栏（SFTP）")
        if visible != was_visible:
            GLib.idle_add(self.restore_width)

    def pane_resized(self, pane, allocation):
        resized = allocation.width != self.pane_width
        self.pane_width = allocation.width
        if resized:
            self.restore_width()

    def pane_position_changed(self, pane, _property):
        if not self.panel.get_visible() and not self.restoring_width:
            self.restore_width()
        if (self.panel.get_visible() and not self.restoring_width and
                pane.get_allocated_width() == self.pane_width):
            self.sidebar_width = pane.get_allocated_width() - pane.get_position()

    def restore_width(self):
        if not self.closed:
            pane = self.owner.file_pane
            width = self.sidebar_width if self.panel.get_visible() else self.owner.file_dock.get_size_request()[0]
            self.restoring_width = True
            try:
                pane.set_position(max(0, pane.get_allocated_width() - width))
            finally:
                self.restoring_width = False
        return False

    def submit(self, endpoint, action, source, target=""):
        self.queue.show()
        self.queue.set_expanded(True)
        return self.manager.submit(endpoint, action, source, target)

    def ask_name(self, title, initial, on_accept, *, identity=None, description=""):
        dialog = Gtk.Dialog(title=title, use_header_bar=False)
        dialog.add_buttons("取消", Gtk.ResponseType.CANCEL, "确定", Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=initial, activates_default=True)
        entry.set_hexpand(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.get_content_area().pack_start(entry, True, True, 10)
        def accepted(name):
            try:
                entry_name(name)
            except ValueError:
                self.owner.info("名称无效", "请输入单个文件或目录名，不能包含 /，也不能为 . 或 ..。")
                return
            on_accept(name)
        return self.present_dialog(dialog, entry.get_text, accepted,
                                   identity=identity, description=description)

    def present_dialog(self, dialog, read_value, on_accept, *, identity=None, description=""):
        # No nested Gtk.Dialog.run(): a separate window must never block input
        # to the terminal, even if the chooser is behind it or minimized.
        title = dialog.get_title()
        key = (title, identity)
        if self.closed:
            dialog.destroy()
            return None
        if key in self.dialogs:
            dialog.destroy()
            self.dialogs[key].present()
            return self.dialogs[key]
        self.dialogs[key] = dialog
        dialog.set_application(self.owner.get_application())
        dialog.set_modal(False)
        dialog.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        dialog.set_icon(self.owner.get_icon())
        header = Gtk.HeaderBar(title=dialog.get_title(), show_close_button=True)
        header.set_decoration_layout(":minimize,maximize,close")
        dialog.set_titlebar(header)
        if description:
            context = Gtk.Label(label=description, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            context.set_max_width_chars(60)
            context.set_tooltip_text(description)
            if isinstance(dialog, Gtk.FileChooserDialog):
                dialog.set_extra_widget(context)
            else:
                dialog.get_content_area().pack_start(context, False, False, 6)
        self.owner.interface_style.attach(dialog)

        def responded(_, response):
            LOG.info("File dialog response dialog=%s title=%s response=%s", id(dialog), title, response)
            try:
                value = read_value() if response == Gtk.ResponseType.OK else None
            finally:
                dialog.destroy()
            if response == Gtk.ResponseType.OK and not self.closed:
                on_accept(value)

        def destroyed(*_):
            self.dialogs.pop(key, None)
            LOG.info("File dialog closed dialog=%s title=%s", id(dialog), title)

        dialog.connect("response", responded)
        dialog.connect("destroy", destroyed)
        dialog.show_all()
        dialog.present()
        LOG.info("File dialog opened dialog=%s title=%s modal=%s", id(dialog), title, dialog.get_modal())
        return dialog

    def changed(self, update):
        if self.closed:
            return
        job = next(job for job in self.manager.jobs if job.id == update["id"])
        if job.id not in self.task_rows:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin=6)
            details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            title = Gtk.Label(xalign=0)
            title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            details.pack_start(title, False, False, 0)
            progress = Gtk.ProgressBar(show_text=True)
            progress.set_ellipsize(Pango.EllipsizeMode.END)
            details.pack_start(progress, False, False, 0)
            row.pack_start(details, True, True, 0)
            actions = Gtk.Box(spacing=4)
            row.pack_start(actions, False, False, 0)
            buttons = {}
            for label, callback in (
                    ("取消", lambda: self.cancel(job)),
                    ("重试", lambda: self.retry(job)),
                    ("覆盖", lambda: self.retry(job, overwrite=True)),
                    ("另存", lambda: self.save_as(job)),
                    ("跳过", lambda: self.skip(job))):
                buttons[label] = action_button(label, callback)
                actions.pack_start(buttons[label], False, False, 0)
            self.listbox.add(row)
            row.show_all()
            self.task_rows[job.id] = title, progress, buttons
        title, progress, buttons = self.task_rows[job.id]
        state = update["state"]
        title.set_text(f"{update['server']} · {self.ACTIONS[update['action']]} · " +
                       (posixpath.basename(update["source"]) or update["source"]))
        detail = f"{update['source']}\n→ {update['target']}\n{update['error']}"
        if update["partial"]:
            detail += "\n未完成的临时文件：" + update["partial"]
        title.set_tooltip_text(detail)
        amount = min(1.0, update["done"] / update["total"]) if update["total"] else 0
        progress.set_fraction(1 if state == "done" else amount)
        progress.set_text(self.STATES[state] + (f" · {size_text(update['done'])} / "
                                              f"{size_text(update['total'])} · "
                                              f"{size_text(update['speed'])}/s"
                                              if state == "running" else
                                              " · " + update["error"].splitlines()[0]
                                              if update["error"] else ""))
        progress.set_tooltip_text(detail)
        buttons["取消"].set_visible(state in ("queued", "connecting", "running"))
        buttons["重试"].set_visible(state in ("failed", "cancelled"))
        for label in ("覆盖", "另存", "跳过"):
            buttons[label].set_visible(state == "conflict")
        active = len(self.manager.active())
        self.queue.set_label(f"文件传输 · {active} 项进行中 · 共 {len(self.manager.jobs)} 项")
        if (state == "done" and job.action != "download" and job.endpoint == self.panel.endpoint
                and self.panel.get_visible() and self.panel.listing is None):
            self.panel.refresh()

    def cancel(self, job):
        job.cancel()
        if job.state == "queued":
            job.state = "cancelled"
            self.manager.notify(job)

    def retry(self, job, overwrite=False, target=None):
        if overwrite and not self.owner.confirm("覆盖已有文件？",
                                                "将替换该任务内所有同名文件：\n" + job.target):
            return
        self.manager.retry(job, overwrite=overwrite, target=target)
        job.state = "retried"
        self.manager.notify(job)

    def save_as(self, job):
        return self.ask_name(
            "另存为", posixpath.basename(job.target),
            lambda name: self.retry(job, target=posixpath.join(posixpath.dirname(job.target), name)),
            identity=job.id, description=f"{job.endpoint.label} · {job.target}")

    def skip(self, job):
        job.state = "skipped"
        self.manager.notify(job)

    def close(self, *_):
        if self.closed:
            return
        self.closed = True
        for dialog in list(self.dialogs.values()):
            dialog.destroy()
        self.panel.close()
        self.manager.close()
