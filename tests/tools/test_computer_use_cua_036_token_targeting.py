"""Cua 0.36 strict token-only action schemas + structured window capture (T-271 backport).

Behaviour contracts for the installed monolithic ``cua_backend.py``:

* A strict native action schema that names ``element_token`` but not ``element_index`` must be sent
  the token alone; an element action with no token from the latest capture is refused locally and
  never reaches the driver (so it cannot degrade to untargeted or coordinate input).
* ``get_window_state`` asks for the structured ``elements`` tree through BOTH the MCP call and the CLI
  re-fetch, only when the live schema accepts that exact value, so bounds / tokens / title survive.
* Drivers with older schemas keep their legacy request shape.

Upstream: NousResearch/hermes-agent#134857 (token-only targeting), #138596 (structured capture).
Stdlib + pytest only; no live cua-driver, no real GUI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from tools.computer_use import cua_backend
from tools.computer_use.cua_backend import CuaDriverBackend

_FORMAT = {"type": "string", "enum": ["markdown", "elements", "both"]}
_ELEMENT_COUNT = 10  # indices 0 (AXWindow root) .. 9


def _element(index: int, snapshot: str = "s1") -> Dict[str, Any]:
    return {"element_index": index, "role": "AXWindow" if index == 0 else "AXButton",
            "label": "Fixture" if index == 0 else f"Button {index}",
            "element_token": f"{snapshot}:{index}",
            "frame": {"x": index, "y": index * 2, "w": 30, "h": 40}}


def _props(*names: str) -> Dict[str, Any]:
    return {"type": "object", "properties": {n: {} for n in names}, "additionalProperties": False}


_ACTION_TOOLS = ("click", "double_click", "scroll", "set_value")
_TOKEN_ONLY = {t: _props("pid", "window_id", "element_token", "button", "direction", "amount", "value")
               for t in _ACTION_TOOLS}
_ACCEPTS_BOTH = {t: _props("pid", "window_id", "element_index", "element_token", "button", "direction",
                           "amount", "value") for t in _ACTION_TOOLS}
_INDEX_ONLY = {t: _props("pid", "window_id", "element_index", "button", "direction", "amount", "value")
               for t in _ACTION_TOOLS}


def _backend(schemas: Dict[str, Any], tokens: Dict[int, str] | None = None) -> CuaDriverBackend:
    backend = CuaDriverBackend()
    backend._session._tool_schemas = schemas
    backend._session._capabilities = {name: set() for name in schemas}
    backend._active_pid, backend._active_window_id = 111, 222
    backend._snapshot_tokens = dict(tokens or {})
    backend._session.call_tool = MagicMock(return_value={
        "data": "ok", "images": [], "image_mime_types": [], "structuredContent": None, "isError": False})
    return backend


def _act(backend: CuaDriverBackend, tool: str, element: Any):
    if tool == "click":
        return backend.click(element=element)
    if tool == "double_click":
        return backend.click(element=element, click_count=2)
    if tool == "scroll":
        return backend.scroll(direction="down", element=element)
    return backend.set_value("v", element=element)


def _capture_with_elements(backend: CuaDriverBackend, count: int, snapshot: str) -> None:
    """Drive the real capture() so tokens come from a (faked) structured driver response."""
    elements = [_element(i, snapshot) for i in range(count)]
    backend._session.call_tool = MagicMock(return_value={
        "data": "", "images": ["cG5n"], "image_mime_types": [],
        "structuredContent": {"tree_format": "elements", "elements": elements}, "isError": False})
    backend.capture("ax", pid=111, window_id=222)
    backend._session.call_tool = MagicMock(return_value={
        "data": "ok", "images": [], "image_mime_types": [], "structuredContent": None, "isError": False})


# ---------------------------------------------------------------------------
# 1 + 2: token-only schemas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", _ACTION_TOOLS)
@pytest.mark.parametrize("index", [0, 1, _ELEMENT_COUNT - 1], ids=["root-0", "first", "last"])
def test_token_only_schema_sends_the_token_and_never_the_index(tool, index):
    backend = _backend(_TOKEN_ONLY)
    _capture_with_elements(backend, _ELEMENT_COUNT, "s1")
    res = _act(backend, tool, index)
    assert res.ok is True
    name, args = backend._session.call_tool.call_args.args
    assert name == tool
    assert args["element_token"] == f"s1:{index}"
    assert "element_index" not in args


@pytest.mark.parametrize("tool", _ACTION_TOOLS)
@pytest.mark.parametrize("index", [0, 1, _ELEMENT_COUNT - 1], ids=["root-0", "first", "last"])
def test_token_only_schema_refuses_an_index_with_no_token(tool, index):
    backend = _backend(_TOKEN_ONLY, tokens={})
    res = _act(backend, tool, index)
    assert res.ok is False and res.code == "stale_element"
    assert str(index) in res.message and "capture" in res.message
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("tool", _ACTION_TOOLS)
def test_token_only_schema_refuses_an_index_from_a_superseded_capture(tool):
    backend = _backend(_TOKEN_ONLY)
    _capture_with_elements(backend, _ELEMENT_COUNT, "s1")
    _capture_with_elements(backend, 3, "s2")  # indices 3..9 no longer exist
    assert _act(backend, tool, 2).ok is True
    assert backend._session.call_tool.call_args.args[1]["element_token"] == "s2:2"
    backend._session.call_tool.reset_mock()
    res = _act(backend, tool, _ELEMENT_COUNT - 1)
    assert res.ok is False and res.code == "stale_element"
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("bad", ["5", 5.0, True, [5]], ids=["str", "float", "bool", "list"])
def test_token_only_schema_refuses_a_non_integer_index(bad):
    backend = _backend(_TOKEN_ONLY, tokens={0: "s1:0", 1: "s1:1", 5: "s1:5"})
    res = backend.click(element=bad)
    assert res.ok is False and res.code == "stale_element"
    backend._session.call_tool.assert_not_called()


def test_a_fresh_capture_that_fails_disarms_the_previous_tokens():
    backend = _backend(_TOKEN_ONLY)
    _capture_with_elements(backend, _ELEMENT_COUNT, "s1")
    backend._clear_active_target()
    backend._active_pid, backend._active_window_id = 111, 222
    res = backend.click(element=4)
    assert res.ok is False and res.code == "stale_element"
    backend._session.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Legacy / mixed schemas keep their behaviour
# ---------------------------------------------------------------------------

def test_schema_accepting_both_keeps_index_beside_token():
    backend = _backend(_ACCEPTS_BOTH, tokens={5: "s1:5"})
    backend.click(element=5)
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5 and args["element_token"] == "s1:5"


def test_schema_accepting_both_does_not_refuse_a_tokenless_index():
    backend = _backend(_ACCEPTS_BOTH, tokens={})
    assert backend.click(element=5).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5 and "element_token" not in args


def test_index_only_schema_never_receives_a_token_without_the_capability():
    backend = _backend(_INDEX_ONLY, tokens={5: "s1:5"})
    backend.click(element=5)
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5 and "element_token" not in args


def test_capability_advertised_token_is_still_attached_to_an_index_only_schema():
    backend = _backend(_INDEX_ONLY, tokens={5: "s1:5"})
    backend._session._capabilities["click"] = {"accessibility.element_tokens"}
    backend.click(element=5)
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5 and args["element_token"] == "s1:5"


def test_undiscovered_schema_is_not_treated_as_token_only():
    backend = _backend({}, tokens={5: "s1:5"})
    backend.click(element=5)
    args = backend._session.call_tool.call_args.args[1]
    assert args == {"pid": 111, "button": "left", "element_index": 5, "window_id": 222,
                    "session": backend._session_id}


def test_coordinate_actions_are_unaffected_by_a_token_only_schema():
    backend = _backend(_TOKEN_ONLY, tokens={})
    assert backend.click(x=10, y=20).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert (args["x"], args["y"]) == (10, 20) and "element_index" not in args and "element_token" not in args


@pytest.mark.parametrize("schemas", [_TOKEN_ONLY, _ACCEPTS_BOTH, {}], ids=["token-only", "both", "undiscovered"])
@pytest.mark.parametrize("coords", [{}, {"x": 1, "y": 2}], ids=["no-coords", "coords"])
def test_element_scroll_without_a_window_fails_closed_instead_of_scrolling_untargeted(schemas, coords):
    backend = _backend(schemas, tokens={5: "s1:5"})
    backend._active_window_id = None
    res = backend.scroll(direction="down", element=5, **coords)
    assert res.ok is False
    backend._session.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# 3: structured capture, MCP + CLI
# ---------------------------------------------------------------------------

_LEGACY_ARGS = {"pid": 101, "window_id": 202, "session": "test-session"}


def _capture_backend(schema: Any) -> CuaDriverBackend:
    backend = CuaDriverBackend()
    backend._session_id = "test-session"
    backend._session._tool_schemas = {"get_window_state": schema}
    backend._session._capabilities = {"get_window_state": set()}
    backend._session._embedded_daemon = None
    return backend


def _elements_response(**extra: Any) -> Dict[str, Any]:
    return {"data": "window_id=202 pid=101 elements=2 snapshot_id=s9\n\n(tree is in structuredContent.elements)",
            "images": ["cG5n"], "image_mime_types": [],
            "structuredContent": {"tree_format": "elements", "snapshot_id": "s9",
                                  "elements": [_element(0, "s9"), _element(7, "s9")], **extra},
            "isError": False}


@pytest.mark.parametrize(("schema", "requested"), [
    ({"properties": {"tree_format": _FORMAT}}, True),
    ({"properties": {"tree_format": {"type": "string"}}}, True),
    (None, False),
    ({}, False),
    ({"properties": []}, False),
    ({"properties": {}}, False),
    ({"properties": {"tree_format": {"enum": ["markdown", "both"]}}}, False),
    ({"properties": {"tree_format": {"type": "integer"}}}, False),
    ({"properties": {"tree_format": {"$ref": "https://invalid.example/schema"}}}, False),
])
def test_structured_view_is_requested_only_when_the_schema_accepts_it(monkeypatch, schema, requested):
    backend = _capture_backend(schema)
    backend._active_pid, backend._active_window_id = 101, 202
    remote_reads: List[Any] = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: remote_reads.append(a))
    expected = dict(_LEGACY_ARGS, **({"tree_format": "elements"} if requested else {}))
    assert backend._gws_args(structured=True) == expected
    assert backend._gws_args() == _LEGACY_ARGS  # vision/other callers keep the compact default
    assert remote_reads == []


@pytest.mark.parametrize(("mode", "extra", "title"), [
    ("ax", {"window_title": "WindowServer title"}, "WindowServer title"),
    ("som", {}, "Fixture"),
])
def test_elements_only_capture_keeps_title_bounds_and_tokens(mode, extra, title):
    backend = _capture_backend({"properties": {"tree_format": _FORMAT}})
    response = _elements_response(**extra)
    backend._session.call_tool = MagicMock(side_effect=lambda name, args, **kw: response)
    cap = backend.capture(mode, pid=101, window_id=202)
    assert cap.window_title == title
    assert [(e.index, e.bounds, e.element_token) for e in cap.elements] == [
        (0, (0, 0, 30, 40), "s9:0"), (7, (7, 14, 30, 40), "s9:7")]
    assert backend._snapshot_tokens == {0: "s9:0", 7: "s9:7"}
    assert cap.png_b64 == "cG5n"
    name, args = backend._session.call_tool.call_args.args
    assert name == "get_window_state" and args["tree_format"] == "elements"


def test_markdown_title_still_wins_when_the_driver_sends_a_markdown_tree():
    backend = _capture_backend({})
    backend._session.call_tool = MagicMock(return_value={
        "data": '2 elements\n- [0] AXWindow "Legacy title"\n- [1] AXButton "OK"', "images": ["cG5n"],
        "image_mime_types": [], "structuredContent": None, "isError": False})
    cap = backend.capture("ax", pid=101, window_id=202)
    assert cap.window_title == "Legacy title"
    assert [e.index for e in cap.elements] == [0, 1]
    assert backend._session.call_tool.call_args.args[1] == _LEGACY_ARGS


@pytest.mark.parametrize("mode", ["ax", "som", "vision"])
def test_mcp_and_cli_refetch_request_the_same_view(monkeypatch, mode):
    backend = _capture_backend({"properties": {"tree_format": _FORMAT}})
    mcp_requests: List[Dict[str, Any]] = []
    cli_requests: List[Dict[str, Any]] = []
    shot_files: List[Path] = []
    # MCP answers with a degenerate result, forcing the CLI re-fetch.
    backend._session.call_tool = MagicMock(side_effect=lambda name, args, **kw: mcp_requests.append(dict(args)) or {
        "data": "", "images": [], "image_mime_types": [], "structuredContent": None, "isError": False})
    monkeypatch.setattr("tools.computer_use.cua_backend.resolve_cua_driver_cmd", lambda *a, **k: "unused-driver")

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["unused-driver", "call", "get_window_state"]
        args = json.loads(cmd[3])
        shot = Path(args.pop("screenshot_out_file"))
        assert shot.is_file()
        shot_files.append(shot)
        cli_requests.append(args)
        body = {**_elements_response()["structuredContent"], "screenshot_png_b64": "cG5n"}
        return MagicMock(returncode=0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cap = backend.capture(mode, pid=101, window_id=202)
    expected = dict(_LEGACY_ARGS)
    if mode != "vision":
        expected["tree_format"] = "elements"
        assert [e.element_token for e in cap.elements] == ["s9:0", "s9:7"]
        assert cap.window_title == "Fixture"
        assert backend._snapshot_tokens == {0: "s9:0", 7: "s9:7"}
    else:
        assert cap.elements == [] and backend._snapshot_tokens == {}
    assert mcp_requests == [expected] and cli_requests == [expected]
    assert cap.png_b64 == "cG5n"
    assert all(not shot.exists() for shot in shot_files)


def test_vision_ignores_elements_and_keeps_the_compact_request():
    backend = _capture_backend({"properties": {"tree_format": _FORMAT}})
    backend._session.call_tool = MagicMock(side_effect=lambda name, args, **kw: _elements_response())
    cap = backend.capture("vision", pid=101, window_id=202)
    assert backend._session.call_tool.call_args.args[1] == _LEGACY_ARGS
    assert cap.png_b64 == "cG5n" and cap.elements == [] and backend._snapshot_tokens == {}


def test_mock_session_without_schema_support_keeps_the_legacy_request():
    """Sessions that cannot positively confirm the value (e.g. mocks) must not get tree_format."""
    backend = CuaDriverBackend()
    backend._session = MagicMock()
    backend._active_pid, backend._active_window_id = 101, 202
    assert "tree_format" not in backend._gws_args(structured=True)


def test_reconnect_clears_cached_schemas_with_the_capabilities():
    session = cua_backend._CuaDriverSession.__new__(cua_backend._CuaDriverSession)
    session._started, session._capabilities, session._capability_version = False, {"click": {"x"}}, "v"
    session._tool_schemas = {"click": _props("element_token")}
    session._start_lifecycle_locked = lambda: None
    session._restart_session_locked()
    assert session._capabilities == {} and session._tool_schemas == {}
