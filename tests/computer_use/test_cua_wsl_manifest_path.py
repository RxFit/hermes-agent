from __future__ import annotations

import json
import os
import posixpath
from types import SimpleNamespace
from unittest.mock import patch

from tools.computer_use import cua_backend


def _wsl_posix_host():
    """WSL is a POSIX host, so DrvFS paths join with ``/`` even when the suite runs on Windows."""
    return patch.object(os.path, "join", posixpath.join)


def test_wsl_windows_manifest_path_translates_to_drvfs():
    with patch("hermes_constants.is_wsl", return_value=True), _wsl_posix_host():
        assert cua_backend._wsl_windows_path_to_posix(
            r"C:\Users\Fernando\AppData\Local\cua-driver\cua-driver.exe"
        ) == "/mnt/c/Users/Fernando/AppData/Local/cua-driver/cua-driver.exe"






def test_resolve_mcp_invocation_normalizes_windows_manifest_command_in_wsl():
    manifest = {
        "mcp_invocation": {
            "command": r"C:\Users\Fernando\AppData\Local\cua-driver\cua-driver.exe",
            "args": ["mcp"],
        }
    }
    proc = SimpleNamespace(returncode=0, stdout=json.dumps(manifest))
    with (
        patch.object(cua_backend.subprocess, "run", return_value=proc),
        patch("hermes_constants.is_wsl", return_value=True),
        _wsl_posix_host(),
    ):
        command, args = cua_backend._resolve_mcp_invocation("cua-driver")

    assert command == "/mnt/c/Users/Fernando/AppData/Local/cua-driver/cua-driver.exe"
    assert args == ["mcp"]
