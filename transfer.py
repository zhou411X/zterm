"""SFTP over OpenSSH, with cancellable background jobs and staged file writes."""

from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import posixpath
import queue
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
import uuid

import credentials
from model import server_ssh_command, ssh_command


@dataclass(frozen=True)
class Endpoint:
    server_id: str
    label: str
    argv: tuple
    environment: tuple = ()


def endpoint_for(server_id, profile=None):
    command = server_ssh_command(profile) if profile else ssh_command(server_id)
    # Keep OpenSSH's config/agent/ProxyJump/known_hosts and our askpass bridge.
    # No PTY, remote shell command, local command or forwarding in a file session.
    argv = command[:1] + [
        "-T", "-oRequestTTY=no", "-oRemoteCommand=none", "-oPermitLocalCommand=no",
        "-oClearAllForwardings=yes", "-oForkAfterAuthentication=no",
        "-oStrictHostKeyChecking=ask", "-oConnectTimeout=15", "-s",
    ] + command[1:] + ["sftp"]
    environment = {
        "SSH_ASKPASS": str(Path(credentials.__file__).resolve()),
        "SSH_ASKPASS_REQUIRE": "force",
        "ZTERM_SSH_BACKGROUND": "0",
    }
    environment.update(credentials.ssh_environment(server_id, profile))
    return Endpoint(server_id, profile["name"] if profile else server_id,
                    tuple(argv), tuple(environment.items()))


class Cancelled(Exception):
    pass


class SFTPStream(socket.socket):
    """Socket transport with the channel name used by Paramiko's SFTP logger."""

    def get_name(self):
        return "openssh-sftp"


class SFTPConnection:
    """A socketpair carries the binary SFTP subsystem, never shell text."""

    def __init__(self, endpoint, cancelled):
        self.endpoint, self.cancelled = endpoint, cancelled
        self.sock = self.process = self.client = self.errors = None

    def __enter__(self):
        try:
            import paramiko
        except ImportError:
            raise RuntimeError("请先安装 SFTP 依赖：sudo apt install python3-paramiko") from None
        try:
            if self.cancelled.is_set():
                raise Cancelled()
            stream, peer = socket.socketpair()
            self.sock = SFTPStream(fileno=stream.detach())
            self.sock.settimeout(60)
            self.errors = tempfile.TemporaryFile()
            try:
                self.process = subprocess.Popen(
                    self.endpoint.argv, stdin=peer, stdout=peer, stderr=self.errors,
                    env=dict(os.environ, **dict(self.endpoint.environment)),
                    start_new_session=True)
            finally:
                peer.close()
            if self.cancelled.is_set():
                raise Cancelled()
            self.client = paramiko.SFTPClient(self.sock)
            self.sock.settimeout(30)
            return self.client
        except Exception as error:
            self.close()
            if self.cancelled.is_set():
                raise Cancelled() from None
            raise RuntimeError(self.diagnostic or str(error) or "SFTP 连接失败") from error

    def abort(self):
        self.cancelled.set()
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        if self.sock is not None:
            self.sock.close()
        if self.process is not None:
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait()
        self.diagnostic = ""
        if self.errors is not None:
            self.errors.seek(0)
            self.diagnostic = self.errors.read(8192).decode("utf-8", "replace").strip()
            self.errors.close()
            self.errors = None

    def __exit__(self, *_):
        self.close()


def entry_name(name):
    # A malicious/broken directory listing must not escape the download root.
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise ValueError("无效的文件名")
    return name


def optional_stat(operation, path):
    try:
        return operation(path)
    except OSError as error:
        if error.errno == errno.ENOENT:
            return None
        raise


@dataclass
class Job:
    endpoint: Endpoint
    action: str
    source: str
    target: str
    overwrite: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: str = "queued"
    done: int = 0
    total: int = 0
    error: str = ""
    partial: str = ""
    started: float = 0
    last_update: float = 0
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    connection: object = field(default=None, repr=False)

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def cancel(self):
        self.cancelled.set()
        if self.connection is not None:
            self.connection.abort()

    def snapshot(self):
        elapsed = time.monotonic() - self.started if self.started else 0
        return dict(id=self.id, server=self.endpoint.label, action=self.action,
                    source=self.source, target=self.target, state=self.state,
                    done=self.done, total=self.total, error=self.error, partial=self.partial,
                    speed=self.done / elapsed if elapsed > 0 and self.state == "running" else 0)


class TransferManager:
    """One transfer at a time; directory browsing has its own short-lived session."""

    def __init__(self, changed, dispatch, connection_factory=SFTPConnection):
        self.changed, self.dispatch = changed, dispatch
        self.connection_factory = connection_factory
        self.jobs, self.browsers = [], []
        self.pending = queue.Queue()
        self.closed = False
        self.worker = threading.Thread(target=self.work, daemon=True)
        self.worker.start()

    def notify(self, job, force=True):
        now = time.monotonic()
        if force or now - job.last_update >= 0.1:
            job.last_update = now
            self.dispatch(self.changed, job.snapshot())

    def submit(self, endpoint, action, source, target="", overwrite=False):
        if self.closed:
            raise RuntimeError("文件传输已关闭")
        job = Job(endpoint, action, source, target, overwrite)
        self.jobs.append(job)
        self.notify(job)
        self.pending.put(job)
        return job

    def retry(self, job, overwrite=False, target=None):
        return self.submit(job.endpoint, job.action, job.source,
                           job.target if target is None else target, overwrite)

    def active(self, server_id=None):
        return [job for job in self.jobs if job.state in ("queued", "connecting", "running", "conflict")
                and (server_id is None or job.endpoint.server_id == server_id)]

    def close(self):
        if self.closed:
            return
        self.closed = True
        for job in self.jobs + self.browsers:
            if job.state in ("queued", "connecting", "running"):
                job.cancel()
        self.pending.put(None)

    def browse(self, endpoint, path, callback):
        job = Job(endpoint, "list", path, "")
        self.browsers.append(job)

        def run():
            result, error = None, ""
            try:
                job.check()
                job.connection = self.connection_factory(endpoint, job.cancelled)
                with job.connection as client:
                    location = client.normalize(path)
                    rows = []
                    for attr in client.listdir_iter(location):
                        job.check()
                        name = entry_name(attr.filename)
                        rows.append((name, attr.st_mode, attr.st_size or 0, attr.st_mtime or 0))
                    rows.sort(key=lambda item: (not stat.S_ISDIR(item[1]), item[0].casefold()))
                    result = location, rows
            except Exception as failure:
                error = str(failure) or "目录读取已取消"
            finally:
                job.state = "cancelled" if job.cancelled.is_set() else "done"
                self.browsers.remove(job)
                self.dispatch(callback, job, result, error)
        threading.Thread(target=run, daemon=True).start()
        return job

    def work(self):
        while True:
            job = self.pending.get()
            if job is None:
                return
            try:
                job.check()
                job.state = "connecting"
                self.notify(job)
                job.connection = self.connection_factory(job.endpoint, job.cancelled)
                with job.connection as client:
                    job.check()
                    job.state, job.started = "running", time.monotonic()
                    self.notify(job)
                    if job.action in ("upload", "download"):
                        self.copy(client, job)
                    elif job.action == "mkdir":
                        client.mkdir(job.source)
                    elif job.action == "rename":
                        client.rename(job.source, job.target)
                    elif job.action == "delete":
                        attr = client.lstat(job.source)
                        if stat.S_ISDIR(attr.st_mode):
                            client.rmdir(job.source)  # Deliberately only empty directories.
                        else:
                            client.remove(job.source)
                    else:
                        raise ValueError("未知文件操作")
                    job.check()
                job.state = "done"
            except FileExistsError as error:
                job.state, job.error = "conflict", str(error)
            except Exception as error:
                job.state = "cancelled" if job.cancelled.is_set() or isinstance(error, Cancelled) else "failed"
                job.error = str(error) or ("已取消" if job.state == "cancelled" else "SFTP 操作失败")
            finally:
                job.connection = None
                self.notify(job)

    def plan(self, client, job):
        upload = job.action == "upload"
        entries = []

        def visit(source, relative):
            job.check()
            attr = os.lstat(source) if upload else client.lstat(source)
            directory = stat.S_ISDIR(attr.st_mode)
            if not directory and not stat.S_ISREG(attr.st_mode):
                raise ValueError("首版仅传输普通文件和目录，不跟随符号链接：" + source)
            entries.append((relative, directory, attr.st_size, attr.st_mode))
            if directory:
                if upload:
                    names = sorted(os.listdir(source))
                else:
                    names = sorted(entry_name(a.filename) for a in client.listdir_iter(source))
                for name in names:
                    entry_name(name)
                    visit(posixpath.join(source, name), posixpath.join(relative, name))
            else:
                job.total += attr.st_size
        visit(job.source, "")
        self.notify(job)
        # Check the entire tree before copying, so conflict decisions cover a task.
        for relative, directory, _, _ in entries:
            job.check()
            target = posixpath.join(job.target, relative) if relative else job.target
            attr = optional_stat(client.lstat if upload else os.lstat, target)
            if attr is None:
                continue
            if stat.S_ISLNK(attr.st_mode) or stat.S_ISDIR(attr.st_mode) != directory:
                raise ValueError("目标类型不同或为符号链接：" + target)
            if not directory and not stat.S_ISREG(attr.st_mode):
                raise ValueError("目标不是普通文件：" + target)
            if not directory and not job.overwrite:
                raise FileExistsError("目标已存在：" + target)
        return entries

    def copy(self, client, job):
        entries = self.plan(client, job)
        upload = job.action == "upload"
        for relative, directory, size, mode in entries:
            job.check()
            source = posixpath.join(job.source, relative) if relative else job.source
            target = posixpath.join(job.target, relative) if relative else job.target
            if directory:
                attr = optional_stat(client.lstat if upload else os.lstat, target)
                if attr is None:
                    if upload:
                        client.mkdir(target)
                    else:
                        os.mkdir(target)
                elif not stat.S_ISDIR(attr.st_mode):
                    raise ValueError("目标目录已改变：" + target)
                continue
            before = job.done
            try:
                if upload:
                    temporary = posixpath.join(posixpath.dirname(target),
                                              ".zterm-" + uuid.uuid4().hex + ".part")
                    job.partial = temporary
                    with open(source, "rb") as reader, client.open(temporary, "wx") as writer:
                        writer.set_pipelined(True)
                        self.stream(reader, writer, job)
                    if job.done - before != size:
                        raise RuntimeError("传输期间源文件大小发生变化：" + source)
                    client.chmod(temporary, stat.S_IMODE(mode) & 0o777)
                    job.check()
                    if job.overwrite:
                        # Do not delete the old file if atomic replacement isn't supported.
                        client.posix_rename(temporary, target)
                    else:
                        client.rename(temporary, target)
                else:
                    fd, temporary = tempfile.mkstemp(prefix=".zterm-", suffix=".part",
                                                     dir=posixpath.dirname(target))
                    job.partial = temporary
                    with os.fdopen(fd, "wb") as writer, client.open(source, "rb") as reader:
                        self.stream(reader, writer, job)
                        writer.flush()
                        os.fsync(writer.fileno())
                        os.fchmod(writer.fileno(), stat.S_IMODE(mode) & 0o777)
                    if job.done - before != size:
                        raise RuntimeError("传输期间源文件大小发生变化：" + source)
                    job.check()
                    if job.overwrite:
                        os.replace(temporary, target)
                    else:
                        os.link(temporary, target)  # Atomic no-clobber publication.
                        os.unlink(temporary)
                job.partial = ""
            finally:
                if job.partial:
                    try:
                        if upload:
                            client.remove(job.partial)
                        else:
                            os.unlink(job.partial)
                        job.partial = ""
                    except OSError:
                        pass  # Keep the path in the job so the UI can report it.

    def stream(self, reader, writer, job):
        while True:
            job.check()
            data = reader.read(256 * 1024)
            if not data:
                break
            writer.write(data)
            job.done += len(data)
            self.notify(job, force=False)
