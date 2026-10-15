from __future__ import annotations

import asyncio
import pathlib
import tempfile
import unittest
from unittest import mock

from moq_trace.errors import CaptureError
from moq_trace.run import hosts, local
from run_support import peer

from moq_workspace_bench import experiment, ssh
from moq_workspace_bench.config import HostConfig
from moq_workspace_bench.errors import ExperimentError


class _FakeSshProcess:
    """An asyncssh process whose merged output is `output` and whose status is `status`."""

    def __init__(self, output: bytes, status: int | None) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(output)
        self.stdout.feed_eof()
        self._status = status
        self.closed = False

    async def wait(self):
        return mock.Mock(returncode=self._status)

    def close(self):
        self.closed = True


class _FakeConnection:
    """Stand in for an asyncssh connection whose commands all finish with `status`."""

    def __init__(self, status: int | None = 0, output: bytes = b"", stdout: str = "", stderr: str = "") -> None:
        self.create_process = mock.AsyncMock(side_effect=lambda *_a, **_k: _FakeSshProcess(output, status))
        self.run = mock.AsyncMock(return_value=mock.Mock(returncode=status, stdout=stdout, stderr=stderr))


def _connected(connection: _FakeConnection):
    """Hand `connection` to every host, instead of opening a real one."""

    return mock.patch.object(ssh.SshPool, "get", mock.AsyncMock(return_value=connection))


class RemoteHostTests(unittest.IsolatedAsyncioTestCase):
    """Drive a remote host through a stand-in asyncssh connection."""

    async def asyncSetUp(self) -> None:
        self.directory = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = ssh.RemoteHost(HostConfig(ssh="me@peer.example"), ssh.SshPool())

    async def test_a_remote_process_reports_its_pid_and_quotes_its_command(self) -> None:
        connection = _FakeConnection(output=b"4242\nready\n")
        with _connected(connection) as connected:
            log = self.directory / "subscriber.log"
            process = await self.host.start(
                "subscriber", ["/opt/a dir/moq-bench", "--x"], "/tmp/a run", log, env={"K": "a b"}
            )
            self.assertEqual(await process.wait(), 0)

        connected.assert_awaited_with("me@peer.example")
        self.assertEqual(process.pid, 4242)
        self.assertEqual(log.read_bytes(), b"ready\n")
        script, options = connection.create_process.call_args.args[0], connection.create_process.call_args.kwargs
        self.assertEqual(
            script,
            "mkdir -p '/tmp/a run' && cd '/tmp/a run' && echo $$ && exec env K='a b' '/opt/a dir/moq-bench' --x",
        )
        self.assertIs(options["stdin"], ssh.asyncssh.DEVNULL)

    async def test_a_remote_process_receives_its_stdin_and_reports_its_status(self) -> None:
        connection = _FakeConnection(status=7, output=b"99\n")
        with _connected(connection):
            process = await self.host.start("tcpdump", ["tcpdump"], "/tmp/run", self.directory / "t.log", stdin=b"pw\n")
            with self.assertRaisesRegex(CaptureError, "tcpdump exited with status 7"):
                await process.stop(True)

        self.assertEqual(connection.create_process.call_args.kwargs["input"], b"pw\n")

    async def test_a_remote_process_that_loses_its_connection_reports_what_ssh_would(self) -> None:
        with _connected(_FakeConnection(status=None, output=b"99\n")):
            process = await self.host.start("relay", ["relay"], "/tmp/run", self.directory / "r.log")
            self.assertEqual(await process.wait(), 255)

    async def test_lost_ssh_output_closes_the_log_without_masking_the_exit_status(self) -> None:
        remote = _FakeSshProcess(b"99\n", None)
        remote.stdout.read = mock.AsyncMock(side_effect=ssh.asyncssh.ConnectionLost("connection lost"))
        connection = _FakeConnection()
        connection.create_process = mock.AsyncMock(return_value=remote)
        with _connected(connection):
            log = self.directory / "lost.log"
            process = await self.host.start("relay", ["relay"], "/tmp/run", log)
            self.assertEqual(await process.wait(), 255)
            self.assertTrue(process._output_closed)
            self.assertEqual(log.read_bytes(), b"")

    async def test_a_remote_process_that_never_starts_reports_why(self) -> None:
        with _connected(_FakeConnection(status=1, output=b"mkdir: cannot create directory\n")):
            with self.assertRaisesRegex(CaptureError, "relay did not start \\(status 1\\): mkdir: cannot create"):
                await self.host.start("relay", ["relay"], "/root/run", self.directory / "r.log")

    async def test_lttng_runs_through_the_hosts_configured_command(self) -> None:
        host = ssh.RemoteHost(
            HostConfig(ssh="me@relay.example", lttng="nix develop ~/moq-trace --command lttng"), ssh.SshPool()
        )
        connection = _FakeConnection(status=1, stdout="<command/>", stderr="no session daemon")
        with _connected(connection):
            result = await host.lttng(("--mi", "xml", "list", "--userspace"))

        self.assertEqual(
            connection.run.call_args.args[0], "nix develop ~/moq-trace --command lttng --mi xml list --userspace"
        )
        self.assertEqual(result, hosts.Completed(1, "<command/>", "no session daemon"))

    async def test_connecting_reads_the_remote_home_for_relative_paths(self) -> None:
        with _connected(_FakeConnection(stdout="/home/me\nme\n")):
            await self.host.connect()
        self.assertEqual(self.host.user, "me")
        self.assertEqual(self.host.path("runs"), "/home/me/runs")
        self.assertEqual(self.host.path("~/src", "/srv"), "/home/me/src")
        self.assertEqual(self.host.binary("moq-bench", None), "moq-bench")
        self.assertEqual(self.host.binary("bin/relay", "src/relay"), "/home/me/src/relay/bin/relay")

    async def test_an_unreachable_host_is_a_capture_error(self) -> None:
        with mock.patch.object(ssh.asyncssh, "connect", side_effect=OSError("connection refused")) as connect:
            with self.assertRaisesRegex(CaptureError, "ssh me@peer.example failed: connection refused"):
                await self.host.connect()

        self.assertEqual(connect.call_args.args, ("peer.example",))
        self.assertEqual(connect.call_args.kwargs["username"], "me")


class WorkloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.host = local.LocalHost()

    async def test_the_workload_rejects_even_successful_early_exits(self) -> None:
        for status in (0, 7):
            with self.subTest(status=status):
                steady = await self.host.start("relay", peer(), str(self.directory), self.directory / "r.log")
                early = await self.host.start(
                    "publisher", peer(exit_status=status), str(self.directory), self.directory / "p.log"
                )
                with self.assertRaisesRegex(
                    ExperimentError, f"publisher exited with status {status} during the workload"
                ):
                    await experiment._wait_for_workload((steady, early), 5)
                await steady.close()
