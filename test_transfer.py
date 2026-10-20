"""Real SFTP subsystem tests using isolated local files, no user servers."""

import os
from pathlib import Path
import pwd
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from transfer import Endpoint, SFTPConnection, TransferManager, endpoint_for, entry_name


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("SFTP job did not finish")


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zterm-sftp-test-")
        self.root = Path(self.directory.name)
        self.remote = self.root / "remote files"
        self.remote.mkdir()
        self.local = self.root / "local"
        self.local.mkdir()
        self.endpoint = Endpoint("test", "隔离 SFTP",
                                 ("/usr/lib/openssh/sftp-server", "-d", str(self.remote)))
        self.updates = []
        self.manager = TransferManager(self.updates.append, lambda callback, *args: callback(*args))

    def tearDown(self):
        self.manager.close()
        self.manager.worker.join(timeout=5)
        self.assertFalse(self.manager.worker.is_alive())
        self.directory.cleanup()

    def job(self, action, source, target="", overwrite=False):
        job = self.manager.submit(self.endpoint, action, str(source), str(target), overwrite)
        wait_for(lambda: job.state in ("done", "failed", "conflict", "cancelled"))
        return job

    def test_connection_reaps_process_and_browses_binary_protocol(self):
        (self.remote / "中文 file;$(touch nope)").write_text("content")
        (self.remote / "empty").mkdir()
        connection = SFTPConnection(self.endpoint, threading.Event())
        with connection as client:
            self.assertEqual(client.normalize("."), str(self.remote))
            self.assertIn("中文 file;$(touch nope)", client.listdir("."))
        self.assertIsNotNone(connection.process.poll())
        result = []
        self.manager.browse(self.endpoint, ".", lambda *args: result.append(args))
        wait_for(lambda: bool(result))
        _, listing, error = result[0]
        self.assertEqual(error, "")
        self.assertEqual(listing[0], str(self.remote))
        self.assertEqual(listing[1][0][0], "empty")

    def test_upload_download_tree_and_zero_byte_files(self):
        source = self.local / "tree 空格"
        source.mkdir()
        (source / "empty").mkdir()
        (source / ".hidden").write_bytes(b"")
        (source / "data ' ;$.bin").write_bytes(bytes(range(256)) * 2048)
        target = self.remote / source.name
        uploaded = self.job("upload", source, target)
        self.assertEqual(uploaded.state, "done", uploaded.error)
        self.assertEqual(uploaded.done, 256 * 2048)
        self.assertEqual((target / "data ' ;$.bin").read_bytes(), (source / "data ' ;$.bin").read_bytes())
        downloaded = self.job("download", target, self.local / "download")
        self.assertEqual(downloaded.state, "done", downloaded.error)
        self.assertTrue((self.local / "download/empty").is_dir())
        self.assertEqual((self.local / "download/.hidden").read_bytes(), b"")
        self.assertEqual((self.local / "download/data ' ;$.bin").read_bytes(),
                         (source / "data ' ;$.bin").read_bytes())
        self.assertEqual(list(self.remote.rglob(".zterm-*.part")), [])
        self.assertEqual(list(self.local.rglob(".zterm-*.part")), [])

    def test_conflict_preserves_original_and_overwrite_is_explicit(self):
        source = self.local / "file"
        source.write_text("replacement")
        target = self.remote / "file"
        target.write_text("original")
        first = self.job("upload", source, target)
        self.assertEqual(first.state, "conflict")
        self.assertEqual(target.read_text(), "original")
        second = self.manager.retry(first, overwrite=True)
        wait_for(lambda: second.state in ("done", "failed"))
        self.assertEqual(second.state, "done", second.error)
        self.assertEqual(target.read_text(), "replacement")
        source.write_text("keep local")
        download = self.job("download", target, source)
        self.assertEqual(download.state, "conflict")
        self.assertEqual(source.read_text(), "keep local")
        renamed = self.manager.retry(download, target=str(self.local / "other"))
        wait_for(lambda: renamed.state in ("done", "failed"))
        self.assertEqual(renamed.state, "done", renamed.error)
        self.assertEqual((self.local / "other").read_text(), "replacement")
        overwrite = self.job("download", target, source, overwrite=True)
        self.assertEqual(overwrite.state, "done", overwrite.error)
        self.assertEqual(source.read_text(), "replacement")

    def test_directory_conflict_is_detected_before_any_copy(self):
        source, target = self.local / "tree", self.remote / "tree"
        source.mkdir()
        target.mkdir()
        (source / "a-new").write_text("new")
        (source / "z-existing").write_text("replacement")
        (target / "z-existing").write_text("keep")
        job = self.job("upload", source, target)
        self.assertEqual(job.state, "conflict")
        self.assertFalse((target / "a-new").exists())
        self.assertEqual((target / "z-existing").read_text(), "keep")

    def test_symlinks_are_not_followed_and_unsafe_names_are_rejected(self):
        real = self.local / "real"
        real.write_text("private")
        (self.local / "link").symlink_to(real)
        upload = self.job("upload", self.local / "link", self.remote / "copy")
        self.assertEqual(upload.state, "failed")
        (self.remote / "link").symlink_to(real)
        download = self.job("download", self.remote / "link", self.local / "copy")
        self.assertEqual(download.state, "failed")
        self.assertFalse((self.local / "copy").exists())
        for name in ("../escape", ".", "..", "/absolute", "a/b", "", "a\0b"):
            with self.assertRaises(ValueError):
                entry_name(name)
        self.assertEqual(entry_name("正常 ' ;$ 文件"), "正常 ' ;$ 文件")

    def test_cancel_mid_upload_never_publishes_partial_target(self):
        source = self.local / "large"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        original_notify = self.manager.notify

        def notify(job, force=True):
            original_notify(job, force)
            if not force and job.done:
                job.cancel()
        self.manager.notify = notify
        (self.remote / "large").write_text("keep original")
        job = self.job("upload", source, self.remote / "large", overwrite=True)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual((self.remote / "large").read_text(), "keep original")
        self.assertGreater(job.done, 0)
        if job.partial:
            self.assertTrue(Path(job.partial).name.startswith(".zterm-"))

    def test_cancel_download_cleans_local_partial(self):
        remote = self.remote / "large"
        remote.write_bytes(b"x" * (2 * 1024 * 1024))
        original_notify = self.manager.notify

        def notify(job, force=True):
            original_notify(job, force)
            if not force and job.done:
                job.cancel()
        self.manager.notify = notify
        job = self.job("download", remote, self.local / "large")
        self.assertEqual(job.state, "cancelled")
        self.assertFalse((self.local / "large").exists())
        self.assertEqual(list(self.local.glob(".zterm-*.part")), [])

    def test_directory_actions_do_not_overwrite_or_recursively_delete(self):
        directory = self.remote / "new"
        self.assertEqual(self.job("mkdir", directory).state, "done")
        (directory / "file").write_text("keep")
        deleted = self.job("delete", directory)
        self.assertEqual(deleted.state, "failed")
        self.assertTrue((directory / "file").exists())
        renamed = self.job("rename", directory / "file", directory / "renamed")
        self.assertEqual(renamed.state, "done", renamed.error)
        (directory / "existing").write_text("old")
        collision = self.job("rename", directory / "renamed", directory / "existing")
        self.assertEqual(collision.state, "failed")
        self.assertEqual((directory / "existing").read_text(), "old")
        self.assertEqual(self.job("delete", directory / "renamed").state, "done")
        self.assertEqual(self.job("delete", directory / "existing").state, "done")
        self.assertEqual(self.job("delete", directory).state, "done")

    def test_failed_connection_reports_error_and_retry_runs(self):
        bad = Endpoint("bad", "invalid", ("/bin/sh", "-c", "echo test-diagnostic >&2; exit 3"))
        job = self.manager.submit(bad, "upload", str(self.local / "file"), "/unused")
        wait_for(lambda: job.state == "failed")
        self.assertIn("test-diagnostic", job.error)
        self.assertEqual(self.job("mkdir", self.remote / "after-error").state, "done")

    def test_endpoint_preserves_auth_configuration_and_forces_subsystem(self):
        profile = dict(name="server", host="192.0.2.1", username="test", port=2222,
                       password_saved=True, credential_id="test-key")
        endpoint = endpoint_for("server-id", profile)
        self.assertEqual(dict(endpoint.environment)["ZTERM_SERVER_ID"], "test-key")
        profile["host"] = "192.0.2.2"
        self.assertNotIn("192.0.2.2", endpoint.argv)
        config = self.root / "ssh_config"
        config.write_text("Host *\n RequestTTY force\n StrictHostKeyChecking no\n"
                          " RemoteCommand echo wrong\n IdentityFile /tmp/test-key\n ProxyJump jump-alias\n")
        result = subprocess.run([endpoint.argv[0], "-G", "-F", str(config), *endpoint.argv[1:]],
                                capture_output=True, text=True, check=True)
        values = dict(line.split(" ", 1) for line in result.stdout.splitlines())
        self.assertEqual(values["requesttty"], "false")
        self.assertEqual(values["stricthostkeychecking"], "ask")
        self.assertEqual(values["hostname"], "192.0.2.1")
        self.assertEqual(values["port"], "2222")
        self.assertEqual(values["sessiontype"], "subsystem")
        self.assertEqual(values["identityfile"], "/tmp/test-key")
        self.assertEqual(values["proxyjump"], "jump-alias")


@unittest.skipUnless(Path("/usr/sbin/sshd").exists(), "openssh-server is required for transport tests")
class SSHTransportTests(unittest.TestCase):
    def test_openssh_login_transfer_and_changed_host_key_rejection(self):
        # sshd StrictModes rejects an authorized_keys path below world-writable /tmp.
        with tempfile.TemporaryDirectory(prefix=".zterm-ssh-test-", dir=Path.home()) as directory:
            root = Path(directory)
            for name in ("host", "client", "wrong-host"):
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                                "-f", str(root / name)], check=True, capture_output=True)
            (root / "authorized_keys").write_bytes((root / "client.pub").read_bytes())
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            config = root / "sshd_config"
            config.write_text(
                f"Port {port}\nListenAddress 127.0.0.1\nHostKey {root}/host\n"
                f"PidFile {root}/pid\nAuthorizedKeysFile {root}/authorized_keys\n"
                "PasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nLogLevel VERBOSE\n"
                f"Subsystem sftp /usr/lib/openssh/sftp-server -d {root}\n")
            with (root / "server.log").open("w+") as log:
                daemon = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
                                          stdout=log, stderr=log)
                try:
                    def started():
                        if daemon.poll() is not None:
                            log.seek(0)
                            raise AssertionError("Isolated sshd failed: " + log.read())
                        try:
                            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                                return True
                        except OSError:
                            return False
                    wait_for(started)
                    known = root / "known_hosts"
                    known.write_text(f"[127.0.0.1]:{port} " + (root / "host.pub").read_text())
                    client_config = root / "ssh_config"
                    client_config.write_text(
                        f"Host *\n IdentityFile {root}/client\n IdentitiesOnly yes\n"
                        f" UserKnownHostsFile {known}\n GlobalKnownHostsFile /dev/null\n"
                        " BatchMode yes\n")
                    profile = dict(name="isolated", host="127.0.0.1",
                                   username=pwd.getpwuid(os.getuid()).pw_name, port=port)
                    original = endpoint_for("isolated", profile)
                    endpoint = Endpoint(original.server_id, original.label,
                                        (original.argv[0], "-F", str(client_config), *original.argv[1:]),
                                        original.environment)
                    with SFTPConnection(endpoint, threading.Event()) as sftp:
                        self.assertEqual(sftp.normalize("."), str(root))
                        with sftp.open(str(root / "SSH 上传.txt"), "w") as stream:
                            stream.write(b"real SSH + SFTP")
                    self.assertEqual((root / "SSH 上传.txt").read_bytes(), b"real SSH + SFTP")
                    known.write_text(f"[127.0.0.1]:{port} " + (root / "wrong-host.pub").read_text())
                    with self.assertRaisesRegex(RuntimeError, "HOST IDENTIFICATION HAS CHANGED"):
                        with SFTPConnection(endpoint, threading.Event()):
                            self.fail("Changed host key must not connect")
                except Exception:
                    log.flush()
                    log.seek(0)
                    print("Isolated SSH diagnostic:\n" + log.read())
                    raise
                finally:
                    daemon.terminate()
                    daemon.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
