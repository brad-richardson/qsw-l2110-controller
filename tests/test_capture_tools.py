import json
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.capture_lacp import capture_command, main
from tools.capture_support import private_directory, write_private


def test_capture_files_are_private_and_cannot_overwrite(tmp_path):
    target = tmp_path / "capture"
    private_directory(target)
    write_private(target / "file", b"original")
    assert target.stat().st_mode & 0o777 == 0o700
    assert (target / "file").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        private_directory(target)
    with pytest.raises(FileExistsError):
        write_private(target / "file", b"replacement")
    assert (target / "file").read_bytes() == b"original"


@pytest.mark.parametrize("interface", ["eth0;id", "$(id)", "../out", "-i", "a" * 16])
def test_capture_rejects_shell_and_path_injection(interface):
    with pytest.raises(ValueError):
        capture_command(interface, 5, "inout")


def test_capture_uses_bounded_filter_and_explicit_promiscuous_mode():
    args = shlex.split(capture_command("eth0", 5, "inout"))
    assert args[:5] == ["sudo", "-n", "timeout", "5", "tcpdump"]
    assert args[-1] == "ether proto 0x8809 and ether[14] = 1"
    assert "-p" in args
    assert "-p" not in shlex.split(capture_command("eth0", 5, "in", promiscuous=True))
    for duration in [0, 301]:
        with pytest.raises(ValueError):
            capture_command("eth0", duration, "inout")


@pytest.mark.parametrize(
    "data,status,success",
    [
        (b"\xd4\xc3\xb2\xa1" + bytes(20), 124, True),
        (b"permission denied", 1, False),
        (b"", 124, False),
        (b"\xd4\xc3\xb2\xa1", 0, False),
    ],
)
def test_capture_requires_pcap_header_and_preserves_failures(
    tmp_path, monkeypatch, data, status, success
):
    def run(args, **kwargs):
        assert "StrictHostKeyChecking=yes" in args
        assert "BatchMode=yes" in args
        return subprocess.CompletedProcess(args, status, data, b"diagnostic")

    monkeypatch.setattr(subprocess, "run", run)
    output = tmp_path / "capture"
    code = main(
        [
            "--ssh-target",
            "user@host",
            "--identity-file",
            "/key",
            "--interface",
            "eth0",
            "--output",
            str(output),
            "--duration",
            "5",
        ]
    )
    assert code == (0 if success else 2)
    assert json.loads((output / "manifest.json").read_text())["results"][0]["success"] is success
    assert (output / "eth0.pcap").read_bytes() == data
    assert (output / "eth0.stderr.txt").read_bytes() == b"diagnostic"


@pytest.mark.skipif(shutil.which("node") is None, reason="optional Node runtime unavailable")
def test_browser_guard_blocks_writes_and_unreviewed_gets():
    helper = Path(__file__).resolve().parents[1] / "tools" / "capture_qss_ui.cjs"
    script = r"""
      const {classifyRequest} = require(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      console.log(JSON.stringify(cases.map(([method,url]) =>
        classifyRequest(method,url,'https://switch').allowed)));
    """
    cases = [
        ["GET", "https://switch/port_mirror.json"],
        ["GET", "https://switch/js/port_info_mirror.js"],
        ["POST", "https://switch/port_mirror.json"],
        ["GET", "https://switch/save_all_configs.json"],
        ["GET", "https://switch/authorize?loginpwd=secret"],
        ["GET", "https://switch/logout"],
        ["GET", "https://other/port_mirror.json"],
        ["GET", "https://switch/js/../save_all_configs.json"],
        ["GET", "https://switch/js/%2e%2e%2fsave_all_configs.json"],
        ["GET", "https://user:secret@switch/port_mirror.json"],
    ]
    result = subprocess.run(
        ["node", "-e", script, str(helper), json.dumps(cases)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [True, True, *([False] * 8)]
