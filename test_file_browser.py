"""SFTP GTK integration checks, run under xvfb-run."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app import AliasStore, Gdk, GLib, Gtk, Window
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
        with patch.object(self.files.panel, "choose", return_value=[str(local)]):
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
        with patch.object(self.files.panel, "choose", return_value=[str(destination)]):
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
        observed = []

        def descendants(widget):
            yield widget
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    yield from descendants(child)

        def activate():
            dialog = next(widget for widget in Gtk.Window.list_toplevels()
                          if widget.get_title() == "选择上传的文件")
            dialog.set_filename(str(source))

            def when_selected():
                if dialog.get_filenames() != [str(source)]:
                    return True
                chooser = next(widget for widget in descendants(dialog)
                               if isinstance(widget, Gtk.FileChooserWidget))
                observed.append("activated")
                chooser.emit("file-activated")
                return False

            selection_timer[0] = GLib.timeout_add(20, when_selected)
            return False

        def timeout():
            for widget in Gtk.Window.list_toplevels():
                if widget.get_title() == "选择上传的文件":
                    widget.response(Gtk.ResponseType.CANCEL)
            observed.append("timeout")
            return False

        selection_timer = [None]
        watchdog = GLib.timeout_add_seconds(3, timeout)
        GLib.idle_add(activate)
        self.files.panel.upload_files()
        if "timeout" not in observed:
            GLib.source_remove(watchdog)
        elif "activated" not in observed and selection_timer[0] is not None:
            GLib.source_remove(selection_timer[0])
        self.assertEqual(observed, ["activated"])
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
        observed = []

        def accept():
            dialog = next(widget for widget in Gtk.Window.list_toplevels()
                          if widget.get_title() == "测试文件名")
            observed.append((dialog.get_transient_for(), dialog.get_type_hint(),
                             dialog.get_titlebar().get_show_close_button()))
            dialog.response(Gtk.ResponseType.OK)
            return False
        GLib.idle_add(accept)
        self.assertEqual(self.files.ask_name("测试文件名", "中文 文件"), "中文 文件")
        self.assertEqual(observed, [(None, Gdk.WindowTypeHint.NORMAL, True)])
        GLib.idle_add(accept)
        with patch.object(self.window, "info") as info:
            self.assertIsNone(self.files.ask_name("测试文件名", "../outside"))
            info.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
