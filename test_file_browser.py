"""SFTP GTK integration checks, run under xvfb-run."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import AliasStore, Gdk, GLib, Gtk, Vte, Window
from test_workbench import pump_until
from transfer import Endpoint, Job


class FileBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="zterm-file-ui-")
        self.root = Path(self.temporary.name)
        self.endpoints = {}
        store = AliasStore(self.root / "servers.json")
        for alias in ("server-a", "server-b"):
            directory = self.root / alias
            directory.mkdir()
            (directory / f"{alias}.txt").write_text(alias)
            self.endpoints[alias] = Endpoint(
                alias, "测试 " + alias, ("/usr/lib/openssh/sftp-server", "-d", str(directory)))
        store.save(list(self.endpoints))
        self.resolver = patch("file_browser.endpoint_for",
                              side_effect=lambda alias, profile=None: self.endpoints[alias])
        self.resolver.start()
        self.window = Window(store=store, enable_quota=False, command_factory=lambda _: ["/bin/cat"])
        self.window.confirm = lambda *_: True
        self.files = self.window.files

    def tearDown(self):
        self.window.close_window()
        self.window.destroy()
        self.files.manager.worker.join(timeout=5)
        pump_until(lambda: not self.files.manager.browsers)
        self.resolver.stop()
        self.temporary.cleanup()

    def listing(self, alias):
        self.files.show_server(alias)
        pump_until(lambda: self.files.panel.location == str(self.root / alias)
                   and self.files.panel.listing is None)

    def finished(self, job):
        pump_until(lambda: job.state in ("done", "failed", "conflict", "cancelled")
                   and job.id in self.files.task_rows)
        self.assertEqual(job.state, "done", job.error)

    def test_file_context_does_not_open_terminal_and_follows_server_selection(self):
        self.assertFalse(self.files.panel.get_visible())
        menu = self.window.server_context_menu("server-a")
        next(item for item in menu.get_children()
             if item.get_label() == "浏览文件（SFTP）").activate()
        menu.destroy()
        pump_until(lambda: self.files.panel.location == str(self.root / "server-a"))
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.groups, {})
        self.assertEqual(self.files.panel.rows[0][1], "server-a.txt")
        self.window.server_list.select_row(self.window.rows["server-b"])
        pump_until(lambda: self.files.panel.location == str(self.root / "server-b")
                   and self.window.current_session().alive)
        pid = self.window.current_session().pid
        self.assertEqual(self.files.panel.rows[0][1], "server-b.txt")
        self.window.files_toggle.set_active(False)
        self.assertFalse(self.files.panel.get_visible())
        self.assertEqual(self.window.current_session().pid, pid)
        self.assertFalse(self.files.manager.closed)
        self.window.files_toggle.set_active(True)
        pump_until(lambda: self.files.panel.listing is None)
        self.window.server_list.select_row(self.window.rows[""])
        self.assertIsNone(self.files.panel.endpoint)
        self.assertFalse(self.files.panel.tools.get_sensitive())

    def test_right_sidebar_remembers_width_across_hide_and_window_resize(self):
        self.assertFalse(self.files.panel.get_visible())
        self.window.show_all()
        self.assertFalse(self.files.panel.get_visible())
        self.window.resize(1200, 800)
        pump_until(lambda: self.window.get_allocated_width() >= 1200)
        self.window.files_toggle.set_active(True)
        pump_until(lambda: self.files.panel.get_allocated_width() >= 330)
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        pane = self.window.file_pane
        pane.set_position(pane.get_allocated_width() - 425)
        # A programmatic handle move needs allocation in this headless test.
        pane.size_allocate(pane.get_allocation())
        sidebar_width = pane.get_allocated_width() - pane.get_position()
        self.assertEqual(sidebar_width, 425)
        self.assertGreater(self.files.panel.get_allocated_width(), 330)

        self.window.files_toggle.set_active(False)
        self.assertFalse(self.files.panel.get_visible())
        self.window.resize(1450, 800)
        pump_until(lambda: self.window.get_allocated_width() >= 1450)
        self.window.files_toggle.set_active(True)
        pump_until(lambda: abs(pane.get_allocated_width() - pane.get_position() -
                               sidebar_width) <= 2)
        self.window.resize(1550, 800)
        pump_until(lambda: self.window.get_allocated_width() >= 1550)
        self.assertAlmostEqual(pane.get_allocated_width() - pane.get_position(),
                               sidebar_width, delta=2)

        self.assertGreater(self.window.stack.get_allocated_width(), sidebar_width)
        self.window.resize(900, 800)
        # GTK can clamp the request to the combined minimum width of the docks.
        pump_until(lambda: self.window.get_allocated_width() < 1450)
        self.assertGreater(self.window.stack.get_allocated_width(), 0)
        self.window.resize(1450, 800)
        pump_until(lambda: self.window.get_allocated_width() >= 1450)
        self.assertAlmostEqual(pane.get_allocated_width() - pane.get_position(),
                               sidebar_width, delta=2)

    def test_collapsed_docks_keep_only_their_top_icons(self):
        from app import SIDEBAR_RAIL_WIDTH

        window = self.window
        pump_until(lambda: window.get_mapped() and window.files_toggle.get_mapped())
        self.assertIs(window.sidebar_toggle.get_parent(), window.server_dock)
        self.assertIs(window.files_toggle.get_parent(), window.file_dock)
        self.assertNotIn(window.sidebar_toggle, window.header.get_children())
        self.assertNotIn(window.files_toggle, window.header.get_children())
        self.assertFalse(any(isinstance(child, Gtk.Label) and child.get_text() == "服务器"
                             for child in window.sidebar.get_children()))
        self.assertFalse(self.files.panel.get_visible())
        self.assertTrue(window.files_toggle.get_mapped())
        window.sidebar_toggle.clicked()
        pump_until(lambda: not window.sidebar.get_visible()
                   and window.server_dock.get_allocated_width() == SIDEBAR_RAIL_WIDTH)
        window.show_all()
        self.assertFalse(window.sidebar.get_visible())
        self.assertFalse(self.files.panel.get_visible())
        for dock, toggle in ((window.server_dock, window.sidebar_toggle),
                             (window.file_dock, window.files_toggle)):
            self.assertEqual([child for child in dock.get_children() if child.get_visible()], [toggle])
            self.assertTrue(toggle.get_mapped())
            self.assertEqual(dock.get_allocated_width(), SIDEBAR_RAIL_WIDTH)
        # Dragging an empty rail must not leave a wide blank sidebar.
        window.pane.set_position(150)
        window.file_pane.set_position(window.file_pane.get_allocated_width() - 180)
        self.assertEqual(window.pane.get_position(), SIDEBAR_RAIL_WIDTH)
        self.assertEqual(window.file_pane.get_allocated_width() - window.file_pane.get_position(),
                         SIDEBAR_RAIL_WIDTH)
        window.sidebar_toggle.clicked()
        window.files_toggle.clicked()
        pump_until(lambda: window.sidebar.get_mapped() and self.files.panel.get_mapped()
                   and window.server_dock.get_allocated_width() > SIDEBAR_RAIL_WIDTH
                   and window.file_dock.get_allocated_width() > SIDEBAR_RAIL_WIDTH)
        self.assertGreater(window.server_dock.get_allocated_width(), SIDEBAR_RAIL_WIDTH)
        self.assertGreater(window.file_dock.get_allocated_width(), SIDEBAR_RAIL_WIDTH)
        self.assertLess(window.sidebar_toggle.get_allocation().y, window.sidebar.get_allocation().y)
        self.assertLess(window.files_toggle.get_allocation().y, self.files.panel.get_allocation().y)

    def test_late_directory_response_cannot_replace_new_server(self):
        requests = []

        def browse(endpoint, path, callback):
            job = SimpleNamespace(cancel=Mock())
            requests.append((endpoint, callback, job))
            return job
        with patch.object(self.files.manager, "browse", side_effect=browse):
            self.files.show_server("server-a")
            self.files.show_server("server-b")
            self.assertEqual(len(requests), 2)
            requests[0][2].cancel.assert_called_once()
            requests[1][1](requests[1][2], ("/correct-b", []), "")
            requests[0][1](requests[0][2], ("/stale-a", []), "")
            self.assertEqual(self.files.panel.location, "/correct-b")
            self.assertEqual(self.files.panel.endpoint.server_id, "server-b")

    def test_upload_download_actions_queue_and_hidden_panel_keeps_transfer(self):
        self.listing("server-a")
        local = self.root / "本地 file.txt"
        local.write_text("upload content")
        with patch.object(self.files.panel, "choose",
                          side_effect=lambda title, action, accepted, *args: accepted([str(local)])):
            self.files.panel.upload_files()
        uploaded = self.files.manager.jobs[-1]
        self.window.files_toggle.set_active(False)
        self.finished(uploaded)
        self.assertEqual((self.root / "server-a" / local.name).read_text(), "upload content")
        self.assertTrue(self.files.queue.get_visible())
        self.listing("server-a")
        for row in self.files.panel.rows:
            if row[1] == local.name:
                self.files.panel.tree.get_selection().select_path(row.path)
        destination = self.root / "downloads"
        destination.mkdir()
        with patch.object(self.files.panel, "choose",
                          side_effect=lambda title, action, accepted, **kwargs: accepted([str(destination)])):
            self.files.panel.download()
        downloaded = self.files.manager.jobs[-1]
        self.listing("server-b")
        self.finished(downloaded)
        self.assertEqual((destination / local.name).read_text(), "upload content")
        self.assertEqual(downloaded.endpoint.server_id, "server-a")
        self.assertEqual(self.files.panel.endpoint.server_id, "server-b")
        self.window.resize(1280, 800)
        pump_until(lambda: self.window.get_allocated_width() >= 1280)
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        image = Gdk.pixbuf_get_from_window(self.window.get_window(), 0, 0,
                                          self.window.get_allocated_width(),
                                          self.window.get_allocated_height())
        image.savev("/tmp/zterm-sftp-preview.png", "png", [], [])

    def test_upload_file_activation_accepts_chooser_once(self):
        self.listing("server-a")
        source = self.root / "双击 上传.txt"
        source.write_text("double click upload")
        def descendants(widget):
            yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from descendants(child)

        dialog = self.files.panel.upload_files()
        pump_until(dialog.get_mapped)
        dialog.set_filename(str(source))
        pump_until(lambda: dialog.get_filenames() == [str(source)])
        chooser = next(widget for widget in descendants(dialog)
                       if isinstance(widget, Gtk.FileChooserWidget))
        chooser.emit("file-activated")
        pump_until(lambda: len(self.files.manager.jobs) == 1)
        self.assertFalse(self.files.dialogs)
        self.assertEqual(len(self.files.manager.jobs), 1)
        self.finished(self.files.manager.jobs[0])
        self.assertEqual((self.root / "server-a" / source.name).read_text(), source.read_text())

    def test_transfer_queue_stays_inside_file_panel_without_resizing_terminal(self):
        self.listing("server-a")
        self.window.resize(1280, 800)
        pump_until(lambda: self.window.get_allocated_width() >= 1280)
        self.assertIs(self.files.queue.get_parent(), self.files.panel)
        height = self.window.stack.get_allocated_height()
        source = self.root / "layout.txt"
        source.write_text("layout")
        job = self.files.submit(self.endpoints["server-a"], "upload", str(source),
                                str(self.root / "server-a" / source.name))
        self.finished(job)
        pump_until(lambda: self.files.queue.get_allocated_height() > 30)
        self.assertEqual(self.window.stack.get_allocated_height(), height)
        self.assertTrue(self.files.queue.is_drawable())
        self.window.files_toggle.set_active(False)
        pump_until(lambda: not self.files.queue.is_drawable())
        self.assertEqual(self.window.stack.get_allocated_height(), height)
        self.listing("server-a")
        pump_until(lambda: self.files.queue.is_drawable())
        self.assertEqual(self.window.stack.get_allocated_height(), height)

    def test_conflict_buttons_retry_and_queue_expansion_keep_correct_visibility(self):
        self.listing("server-a")
        source = self.root / "source"
        source.write_text("new content")
        target = str(self.root / "server-a/server-a.txt")
        job = self.files.submit(self.endpoints["server-a"], "upload", str(source), target)
        pump_until(lambda: job.state == "conflict" and job.id in self.files.task_rows
                   and self.files.task_rows[job.id][2]["覆盖"].get_visible())
        buttons = self.files.task_rows[job.id][2]
        self.assertFalse(buttons["取消"].get_visible())
        self.files.queue.set_expanded(False)
        self.files.queue.set_expanded(True)
        self.assertFalse(buttons["取消"].get_visible())
        buttons["覆盖"].clicked()
        retry = self.files.manager.jobs[-1]
        self.finished(retry)
        self.assertEqual(Path(target).read_text(), "new content")
        self.assertEqual(job.state, "retried")

    def test_file_drop_uploads_local_uris_and_rejects_remote_uris(self):
        self.listing("server-a")
        source = self.root / "drag 空格.txt"
        source.write_text("dropped")
        data = SimpleNamespace(get_uris=lambda: [source.as_uri()])
        with patch("file_browser.Gtk.drag_finish") as finish:
            self.files.panel.dropped(self.files.panel, object(), 0, 0, data, 0, 0)
            self.assertTrue(finish.call_args.args[1])
        self.finished(self.files.manager.jobs[-1])
        self.assertEqual((self.root / "server-a" / source.name).read_text(), "dropped")
        count = len(self.files.manager.jobs)
        data = SimpleNamespace(get_uris=lambda: ["sftp://unrelated.example/file"])
        with patch("file_browser.Gtk.drag_finish") as finish:
            self.files.panel.dropped(self.files.panel, object(), 0, 0, data, 0, 0)
            self.assertFalse(finish.call_args.args[1])
        self.assertEqual(len(self.files.manager.jobs), count)

    def test_active_transfers_guard_close_and_server_removal(self):
        self.window.server_list.select_row(self.window.rows["server-a"])
        pump_until(lambda: self.window.current_session().alive)
        job = Job(self.endpoints["server-a"], "upload", "/test", "/test")
        self.files.manager.jobs.append(job)
        with patch.object(self.window, "info") as info:
            self.window.remove_alias()
            info.assert_called_once()
        self.assertIn("server-a", self.window.aliases)
        with patch.object(self.window, "confirm", return_value=False):
            self.assertTrue(self.window.close_window())
        self.assertFalse(self.files.manager.closed)
        self.assertFalse(job.cancelled.is_set())
        self.window.close_window()
        self.assertTrue(self.files.manager.closed)
        self.assertTrue(job.cancelled.is_set())

    def test_file_name_dialog_is_independent_and_rejects_path_traversal(self):
        accepted = []
        dialog = self.files.ask_name("测试文件名", "中文 文件", accepted.append)
        self.assertIsNone(dialog.get_transient_for())
        self.assertEqual(dialog.get_type_hint(), Gdk.WindowTypeHint.NORMAL)
        self.assertFalse(dialog.get_modal())
        self.assertTrue(dialog.get_titlebar().get_show_close_button())
        dialog.response(Gtk.ResponseType.OK)
        self.assertEqual(accepted, ["中文 文件"])
        with patch.object(self.window, "info") as info:
            dialog = self.files.ask_name("测试文件名", "../outside", accepted.append)
            dialog.response(Gtk.ResponseType.OK)
            info.assert_called_once()
        self.assertEqual(accepted, ["中文 文件"])

    def test_gnome_header_chooser_keeps_buttons_and_terminal_interactive(self):
        settings = Gtk.Settings.get_default()
        original = settings.get_property("gtk-dialogs-use-header")
        settings.set_property("gtk-dialogs-use-header", True)
        try:
            self.listing("server-a")
            source = self.root / "directory upload"
            source.mkdir()
            (source / "content.txt").write_text("snapshot target")
            dialog = self.files.panel.upload_directory()
            pump_until(dialog.get_mapped)
            for response in (Gtk.ResponseType.OK, Gtk.ResponseType.CANCEL):
                button = dialog.get_widget_for_response(response)
                self.assertIs(button.get_toplevel(), dialog)
                self.assertTrue(button.get_mapped())
            self.assertFalse(dialog.get_modal())
            self.assertIsNone(Gtk.grab_get_current())
            self.assertIs(self.files.panel.upload_directory(), dialog)
            self.assertEqual(len(self.files.dialogs), 1)
            # The terminal remains usable while the chooser is open.
            self.window.server_list.select_row(self.window.rows[""])
            pump_until(lambda: self.window.current_session().alive)
            self.window.current_session().terminal.feed_child(b"chooser does not block\n")
            pump_until(lambda: "chooser does not block" in
                       self.window.current_session().terminal.get_text_format(
                           Vte.Format.TEXT))
            self.listing("server-b")
            other_dialog = self.files.panel.upload_directory()
            self.assertIsNot(other_dialog, dialog)
            self.assertIn("server-b", other_dialog.get_extra_widget().get_text())
            self.assertIn("server-a", dialog.get_extra_widget().get_text())
            other_dialog.response(Gtk.ResponseType.CANCEL)
            dialog.set_filename(str(source))
            pump_until(lambda: dialog.get_filenames() == [str(source)])
            dialog.get_widget_for_response(Gtk.ResponseType.OK).clicked()
            pump_until(lambda: len(self.files.manager.jobs) == 1)
            self.finished(self.files.manager.jobs[0])
            self.assertEqual(self.files.manager.jobs[0].endpoint.server_id, "server-a")
            self.assertEqual((self.root / "server-a" / source.name / "content.txt").read_text(),
                             "snapshot target")
            self.assertFalse(self.files.dialogs)
            dialog = self.files.panel.upload_files()
            dialog.response(Gtk.ResponseType.CANCEL)
            self.assertEqual(len(self.files.manager.jobs), 1)
            self.assertFalse(self.files.dialogs)
            self.files.panel.upload_files()
            self.window.destroy()
            self.assertFalse(self.files.dialogs)
        finally:
            settings.set_property("gtk-dialogs-use-header", original)

    def test_save_as_dialogs_bind_to_individual_conflict_tasks(self):
        first = Job(self.endpoints["server-a"], "upload", "/a", "/target/same.txt", state="conflict")
        second = Job(self.endpoints["server-b"], "upload", "/b", "/target/same.txt", state="conflict")
        with patch.object(self.files, "retry") as retry:
            one = self.files.save_as(first)
            two = self.files.save_as(second)
            self.assertIsNot(one, two)
            self.assertIs(self.files.save_as(first), one)
            entry = next(widget for widget in two.get_content_area().get_children()
                         if isinstance(widget, Gtk.Entry))
            entry.set_text("second.txt")
            two.response(Gtk.ResponseType.OK)
            retry.assert_called_once_with(second, target="/target/second.txt")
            self.assertEqual(len(self.files.dialogs), 1)
            one.response(Gtk.ResponseType.CANCEL)
        self.assertFalse(self.files.dialogs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
