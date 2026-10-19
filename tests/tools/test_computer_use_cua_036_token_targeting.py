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


# ---------------------------------------------------------------------------
# Token-only classification needs POSITIVE evidence that element_index is forbidden.
# Omitting a property from ``properties`` does not forbid it under JSON Schema.
# ---------------------------------------------------------------------------

_TOKEN_PROPS = {"pid": {"type": "integer"}, "window_id": {"type": "integer"}, "element_token": {"type": "string"}}


def _click_schema(**keywords: Any) -> Dict[str, Any]:
    return {"type": "object", "properties": dict(_TOKEN_PROPS), **keywords}


# Schemas that positively forbid element_index while accepting element_token.
_FORBIDS_INDEX = {
    "strict": _click_schema(additionalProperties=False),
    "strict-with-unrelated-required": _click_schema(additionalProperties=False, required=["pid", "window_id"]),
    "boolean-false-index": _click_schema(properties={**_TOKEN_PROPS, "element_index": False}),
    "local-ref-false-index": _click_schema(properties={**_TOKEN_PROPS, "element_index": {"$ref": "#/$defs/never"}},
                                           **{"$defs": {"never": False}}),
    "composed-via-local-ref": {"allOf": [{"$ref": "#/$defs/target"}],
                               "$defs": {"target": _click_schema(additionalProperties=False)}},
    "unevaluated-properties-false": {"type": "object", "allOf": [{"properties": dict(_TOKEN_PROPS)}],
                                     "unevaluatedProperties": False},
}

# Schemas that declare element_token but still PERMIT element_index (or whose support is unknown).
_PERMITS_INDEX = {
    "additionalProperties-omitted": _click_schema(),
    "additionalProperties-true": _click_schema(additionalProperties=True),
    "additionalProperties-empty-schema": _click_schema(additionalProperties={}),
    "unrelated-required-does-not-forbid": _click_schema(required=["pid", "element_token"]),
    "composed-permissive": {"allOf": [_click_schema(), {"properties": {"button": {"type": "string"}}}]},
    "unknown-oneOf-required": _click_schema(oneOf=[{"required": ["pid"]}, {"required": ["window_id"]}]),
    "remote-ref-property-unresolvable": _click_schema(
        properties={**_TOKEN_PROPS, "button": {"$ref": "https://invalid.example/button.json"}}),
    "index-declared": _click_schema(properties={**_TOKEN_PROPS, "element_index": {"type": "integer"}},
                                    additionalProperties=False),
}


@pytest.mark.parametrize("tool", ["click", "set_value"])
@pytest.mark.parametrize("name", list(_FORBIDS_INDEX))
@pytest.mark.parametrize("index", [0, 1, _ELEMENT_COUNT - 1], ids=["root-0", "first", "last"])
def test_positive_prohibition_of_the_index_makes_the_schema_token_only(name, tool, index):
    backend = _backend({tool: _FORBIDS_INDEX[name]}, tokens={i: f"s1:{i}" for i in range(_ELEMENT_COUNT)})
    assert _act(backend, tool, index).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_token"] == f"s1:{index}" and "element_index" not in args


@pytest.mark.parametrize("name", list(_FORBIDS_INDEX))
def test_positive_prohibition_of_the_index_still_refuses_a_tokenless_index(name):
    backend = _backend({"click": _FORBIDS_INDEX[name]}, tokens={1: "s1:1"})
    res = backend.click(element=0)
    assert res.ok is False and res.code == "stale_element"
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("tool", ["click", "set_value"])
@pytest.mark.parametrize("name", list(_PERMITS_INDEX))
@pytest.mark.parametrize("index", [0, 1, _ELEMENT_COUNT - 1], ids=["root-0", "first", "last"])
def test_permissive_or_unknown_schema_keeps_the_index_beside_the_token(name, tool, index):
    backend = _backend({tool: _PERMITS_INDEX[name]}, tokens={i: f"s1:{i}" for i in range(_ELEMENT_COUNT)})
    assert _act(backend, tool, index).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == index and args["element_token"] == f"s1:{index}"


@pytest.mark.parametrize("name", list(_PERMITS_INDEX))
def test_permissive_or_unknown_schema_never_refuses_a_tokenless_index(name):
    backend = _backend({"click": _PERMITS_INDEX[name]}, tokens={})
    assert backend.click(element=0).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 0 and "element_token" not in args


@pytest.mark.parametrize("schema", [
    _click_schema(properties={**_TOKEN_PROPS, "element_token": False}),
    _click_schema(properties={**_TOKEN_PROPS, "element_token": False, "element_index": {"type": "integer"}},
                  additionalProperties=False),
], ids=["permissive", "strict-with-index"])
def test_a_token_the_schema_rejects_is_not_attached_from_the_schema(schema):
    """Declaring ``element_token`` is not proof it is accepted (boolean ``false``). The separate legacy
    ``capabilities[]`` path is deliberately unchanged (see the capability-advertised test above)."""
    backend = _backend({"click": schema}, tokens={5: "s1:5"})
    assert backend.click(element=5).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5 and "element_token" not in args


@pytest.mark.parametrize("name", list(_FORBIDS_INDEX))
def test_without_a_validator_only_a_structural_prohibition_counts(monkeypatch, name):
    """If jsonschema cannot evaluate the schema, `additionalProperties: false` / `element_index: false`
    still classify as token-only; anything needing real evaluation stays legacy, never token-only."""
    def unavailable(*args, **kwargs):
        raise ImportError("jsonschema unavailable")
    monkeypatch.setattr("jsonschema.validators.validator_for", unavailable)
    structural = name in {"strict", "strict-with-unrelated-required", "boolean-false-index"}
    backend = _backend({"click": _FORBIDS_INDEX[name]}, tokens={5: "s1:5"})
    assert backend.click(element=5).ok is True
    args = backend._session.call_tool.call_args.args[1]
    if structural:
        assert args["element_token"] == "s1:5" and "element_index" not in args
    else:
        assert args["element_index"] == 5  # needs real evaluation: unknown, so legacy behaviour


@pytest.mark.parametrize("name", list(_PERMITS_INDEX))
def test_without_a_validator_permissive_schemas_stay_legacy(monkeypatch, name):
    monkeypatch.setattr("jsonschema.validators.validator_for", lambda *a, **k: (_ for _ in ()).throw(ImportError()))
    backend = _backend({"click": _PERMITS_INDEX[name]}, tokens={5: "s1:5"})
    assert backend.click(element=5).ok is True
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5


def test_classification_never_fetches_remote_schema_references(monkeypatch):
    remote_reads: List[Any] = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: remote_reads.append(a))
    schema = _click_schema(properties={**_TOKEN_PROPS, "element_index": {"$ref": "https://invalid.example/i.json"}})
    backend = _backend({"click": schema}, tokens={5: "s1:5"})
    assert backend.click(element=5).ok is True  # unresolvable: unknown, legacy behaviour
    args = backend._session.call_tool.call_args.args[1]
    assert args["element_index"] == 5
    assert remote_reads == []


def test_tree_format_resolves_a_root_local_defs_ref_and_still_rejects_remote_refs(monkeypatch):
    """Review finding 1 did not reproduce: a root-local $ref is resolved (documents correct behaviour),
    while a remote $ref keeps failing closed without any network read."""
    remote_reads: List[Any] = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: remote_reads.append(a))
    local = {"properties": {"tree_format": {"$ref": "#/$defs/treeFormat"}},
             "$defs": {"treeFormat": {"enum": ["markdown", "elements", "both"]}}}
    backend = _capture_backend(local)
    assert backend._session.supports_input_value("get_window_state", "tree_format", "elements") is True
    assert backend._session.supports_input_value("get_window_state", "tree_format", "bogus") is False
    backend._active_pid, backend._active_window_id = 101, 202
    assert backend._gws_args(structured=True)["tree_format"] == "elements"
    remote = _capture_backend({"properties": {"tree_format": {"$ref": "https://invalid.example/f.json"}}})
    assert remote._session.supports_input_value("get_window_state", "tree_format", "elements") is False
    assert remote_reads == []


# ---------------------------------------------------------------------------
# Negotiate the ACTUAL payload values (real index, real snapshot token), never placeholders.
# ---------------------------------------------------------------------------

def _send(schema: Dict[str, Any], element: Any, tokens: Dict[int, str], tool: str = "click"):
    backend = _backend({tool: schema}, tokens=tokens)
    res = _act(backend, tool, element)
    sent = backend._session.call_tool.call_args.args[1] if backend._session.call_tool.called else None
    return res, sent


_MIXED_MIN1 = {"type": "object",
               "properties": {"element_token": {"type": "string"},
                              "element_index": {"type": "integer", "minimum": 1}},
               "additionalProperties": False}


def test_a_valid_index_is_kept_on_a_mixed_schema_with_a_positive_minimum():
    """Manager reproduction 1: index 1 satisfies `minimum: 1`; the schema is NOT token-only."""
    res, sent = _send(_MIXED_MIN1, 1, {1: "tok_synthetic"})
    assert res.ok is True
    assert sent["element_index"] == 1 and sent["element_token"] == "tok_synthetic"


def test_an_index_the_mixed_schema_rejects_is_replaced_by_its_valid_token_or_refused():
    res, sent = _send(_MIXED_MIN1, 0, {0: "tok_root"})
    assert res.ok is True and sent["element_token"] == "tok_root" and "element_index" not in sent
    res, sent = _send(_MIXED_MIN1, 0, {})
    assert res.ok is False and res.code == "stale_element" and sent is None


_PATTERNED = {"type": "object",
              "properties": {"element_token": {"type": "string", "pattern": "^tok_"}},
              "additionalProperties": False}


def test_a_valid_patterned_token_is_attached_and_the_forbidden_index_dropped():
    """Manager reproduction 2: 'tok_synthetic' matches the pattern the old fixed probe 'token' failed."""
    res, sent = _send(_PATTERNED, 1, {1: "tok_synthetic"})
    assert res.ok is True
    assert sent["element_token"] == "tok_synthetic" and "element_index" not in sent


@pytest.mark.parametrize(("token_schema", "good", "bad"), [
    ({"type": "string", "pattern": "^tok_"}, "tok_synthetic", "bad_synthetic"),
    ({"type": "string", "minLength": 6}, "abcdef", "abc"),
    ({"type": "string", "enum": ["alpha", "beta"]}, "beta", "gamma"),
], ids=["pattern", "minLength", "enum"])
def test_actual_token_value_decides_acceptance_on_a_strict_token_only_schema(token_schema, good, bad):
    schema = {"type": "object", "properties": {"element_token": token_schema}, "additionalProperties": False}
    res, sent = _send(schema, 3, {3: good})
    assert res.ok is True and sent["element_token"] == good and "element_index" not in sent
    # An invalid snapshot token fails closed: no forbidden index, no downgrade, driver never called.
    res, sent = _send(schema, 3, {3: bad})
    assert res.ok is False and res.code == "stale_element" and sent is None


@pytest.mark.parametrize("tool", ["click", "set_value", "scroll", "double_click"])
def test_patterned_token_schema_works_for_every_element_action(tool):
    res, sent = _send(_PATTERNED, 4, {4: "tok_x"}, tool=tool)
    assert res.ok is True and sent["element_token"] == "tok_x" and "element_index" not in sent


def test_a_token_missing_for_the_actual_index_is_refused_even_when_other_tokens_exist():
    res, sent = _send(_PATTERNED, 2, {1: "tok_synthetic"})
    assert res.ok is False and res.code == "stale_element" and sent is None


# Unadvertised tokens are not sent: permitting extra properties is not declaring token support.
_NO_TOKEN_SUPPORT = {
    "omitted-additionalProperties": {"type": "object", "properties": {"pid": {"type": "integer"},
                                                                      "element_index": {"type": "integer"}}},
    "additionalProperties-true": {"type": "object", "properties": {"element_index": {"type": "integer"}},
                                  "additionalProperties": True},
    "index-only-no-properties-list": {"type": "object"},
}


@pytest.mark.parametrize("name", list(_NO_TOKEN_SUPPORT))
def test_a_schema_that_merely_permits_extras_does_not_get_an_unadvertised_token(name):
    res, sent = _send(_NO_TOKEN_SUPPORT[name], 5, {5: "tok_synthetic"})
    assert res.ok is True and sent["element_index"] == 5 and "element_token" not in sent


@pytest.mark.parametrize("name", list(_NO_TOKEN_SUPPORT))
def test_the_legacy_capability_still_attaches_the_token_beside_the_index(name):
    backend = _backend({"click": _NO_TOKEN_SUPPORT[name]}, tokens={5: "tok_synthetic"})
    backend._session._capabilities["click"] = {"accessibility.element_tokens"}
    assert backend.click(element=5).ok is True
    sent = backend._session.call_tool.call_args.args[1]
    assert sent["element_index"] == 5 and sent["element_token"] == "tok_synthetic"


def test_a_token_declared_only_inside_a_composed_branch_counts_as_declared():
    schema = {"allOf": [{"properties": {"element_token": {"type": "string", "pattern": "^tok_"}}}]}
    res, sent = _send(schema, 5, {5: "tok_synthetic"})
    assert res.ok is True and sent["element_index"] == 5 and sent["element_token"] == "tok_synthetic"


# patternProperties makes a property list non-exhaustive: never infer a prohibition from it.
_PATTERN_PROPERTIES = {"type": "object", "properties": {"element_token": {"type": "string"}},
                       "patternProperties": {"^element_": {}}, "additionalProperties": False}


@pytest.mark.parametrize("validator", ["available", "missing"])
def test_pattern_properties_do_not_make_the_index_forbidden(monkeypatch, validator):
    if validator == "missing":
        monkeypatch.setattr("jsonschema.validators.validator_for",
                            lambda *a, **k: (_ for _ in ()).throw(ImportError("jsonschema unavailable")))
    res, sent = _send(_PATTERN_PROPERTIES, 5, {5: "tok_synthetic"})
    assert res.ok is True and sent["element_index"] == 5
    res, sent = _send(_PATTERN_PROPERTIES, 5, {})
    assert res.ok is True and sent["element_index"] == 5 and "element_token" not in sent


@pytest.mark.parametrize("composition", [
    {"allOf": [{"properties": {"element_index": {"type": "integer"}}}]},
    {"anyOf": [{"properties": {"element_index": {"type": "integer"}}}, {"type": "object"}]},
    {"if": {"required": ["pid"]}, "then": {"properties": {"element_index": {"type": "integer"}}}},
], ids=["allOf", "anyOf", "if-then"])
def test_without_a_validator_a_composed_schema_is_never_called_restrictive(monkeypatch, composition):
    monkeypatch.setattr("jsonschema.validators.validator_for",
                        lambda *a, **k: (_ for _ in ()).throw(ImportError("jsonschema unavailable")))
    schema = {"type": "object", "properties": {"element_token": {"type": "string"}},
              "additionalProperties": False, **composition}
    res, sent = _send(schema, 5, {})
    assert res.ok is True and sent["element_index"] == 5  # acceptance unknown -> legacy, not refused


def test_without_a_validator_a_simple_strict_schema_is_still_token_only(monkeypatch):
    monkeypatch.setattr("jsonschema.validators.validator_for",
                        lambda *a, **k: (_ for _ in ()).throw(ImportError("jsonschema unavailable")))
    res, sent = _send(_PATTERNED, 1, {1: "tok_synthetic"})
    assert res.ok is True and sent["element_token"] == "tok_synthetic" and "element_index" not in sent
    res, sent = _send(_PATTERNED, 1, {})
    assert res.ok is False and res.code == "stale_element" and sent is None


# ---------------------------------------------------------------------------
# Negotiate inside the REAL outbound action payload, never an isolated one-property probe.
# A verdict may only reject the index/token when the rejection is attributable to that
# property itself; another field's error, or a constraint that needs other fields, is unknown.
# ---------------------------------------------------------------------------

_CTX_PROPS = {"pid": {"type": "integer"}, "window_id": {"type": "integer"},
              "button": {"enum": ["left", "right", "middle"]}, "session": {"type": "string"},
              "element_index": {"type": "integer"}, "element_token": {"type": "string"}}


def _ctx_schema(**keywords: Any) -> Dict[str, Any]:
    return {"type": "object", "properties": dict(_CTX_PROPS), "additionalProperties": False, **keywords}


def test_manager_reproduction_dependent_required_keeps_the_valid_index():
    """Manager reproduction (8d419ffb7): `dependentRequired: element_index -> pid`; the real args
    {element_index: 1, pid: 123} are valid, an isolated {element_index: 1} probe is not."""
    schema = {"type": "object", "properties": {"element_index": {"type": "integer"}, "pid": {"type": "integer"}},
              "dependentRequired": {"element_index": ["pid"]}, "additionalProperties": False}
    backend = _backend({"click": schema}, tokens={})
    res = backend._action("click", {"element_index": 1, "pid": 123})
    assert res.ok is True, res.message
    sent = backend._session.call_tool.call_args.args[1]
    assert sent["element_index"] == 1 and sent["pid"] == 123 and "element_token" not in sent


@pytest.mark.parametrize("tool", ["click", "set_value", "scroll", "double_click"])
def test_dependent_required_on_a_fully_declared_action_keeps_index_and_token(tool):
    schema = _ctx_schema(dependentRequired={"element_index": ["pid"], "element_token": ["pid"]})
    res, sent = _send(schema, 1, {1: "tok_synthetic"}, tool=tool)
    assert res.ok is True
    assert sent["element_index"] == 1 and sent["element_token"] == "tok_synthetic"


def test_dependent_required_without_a_token_sends_the_legacy_index():
    res, sent = _send(_ctx_schema(dependentRequired={"element_index": ["pid"]}), 0, {})
    assert res.ok is True and sent["element_index"] == 0 and "element_token" not in sent


_IF_PID = {"if": {"properties": {"pid": {"const": 111}}, "required": ["pid"]},
           "then": {"properties": {"element_index": {"minimum": 0}}},
           "else": {"properties": {"element_index": False}}}


def test_a_conditional_that_depends_on_another_field_is_evaluated_in_the_real_payload():
    schema = _ctx_schema(**_IF_PID)
    res, sent = _send(schema, 4, {4: "tok_x"})  # real pid is 111 -> `then` applies -> index valid
    assert res.ok is True and sent["element_index"] == 4 and sent["element_token"] == "tok_x"
    res, sent = _send(schema, 4, {})
    assert res.ok is True and sent["element_index"] == 4 and "element_token" not in sent


def test_a_context_specific_rejection_attributable_to_the_index_still_counts():
    """pid 999 selects the `else` branch where `element_index` is a false schema: the real payload's
    index IS rejected, so it is dropped when a token exists and the action is refused otherwise."""
    backend = _backend({"click": _ctx_schema(**_IF_PID)}, tokens={4: "tok_x"})
    backend._active_pid = 999
    assert backend.click(element=4).ok is True
    sent = backend._session.call_tool.call_args.args[1]
    assert sent["element_token"] == "tok_x" and "element_index" not in sent
    backend = _backend({"click": _ctx_schema(**_IF_PID)}, tokens={})
    backend._active_pid = 999
    res = backend.click(element=4)
    assert res.ok is False and res.code == "stale_element"
    backend._session.call_tool.assert_not_called()


def test_a_composed_constraint_that_cannot_be_attributed_is_unknown_not_a_refusal():
    schema = _ctx_schema(anyOf=[{"properties": {"element_index": {"minimum": 100}}},
                                {"properties": {"element_index": {"maximum": -1}}}])
    res, sent = _send(schema, 5, {})
    assert res.ok is True and sent["element_index"] == 5  # rejection not attributable -> legacy
    res, sent = _send(schema, 5, {5: "tok_x"})
    assert res.ok is True and sent["element_index"] == 5 and sent["element_token"] == "tok_x"


def test_dependent_required_on_the_token_does_not_hide_a_valid_token():
    schema = {"type": "object", "properties": {**_CTX_PROPS, "element_index": False},
              "dependentRequired": {"element_token": ["pid"]}, "additionalProperties": False}
    res, sent = _send(schema, 2, {2: "tok_synthetic"})
    assert res.ok is True and sent["element_token"] == "tok_synthetic" and "element_index" not in sent


@pytest.mark.parametrize("tool", ["click", "set_value"])
def test_an_unrelated_invalid_field_is_not_proof_that_a_declared_index_is_forbidden(tool):
    backend = _backend({tool: _ctx_schema()}, tokens={})
    backend._active_pid = "not-an-integer"  # another field's error only
    res = _act(backend, tool, 3)
    assert res.ok is True
    assert backend._session.call_tool.call_args.args[1]["element_index"] == 3


def test_a_false_schema_on_an_unrelated_field_is_not_attributed_to_the_index():
    """A `false` property schema yields a path-less error; it only counts against the index when
    dropping the index removes it. Here `pid` is the rejected field, the index is fine."""
    schema = _ctx_schema(properties={**_CTX_PROPS, "pid": False})
    res, sent = _send(schema, 3, {})
    assert res.ok is True and sent["element_index"] == 3
    res, sent = _send(schema, 3, {3: "tok_x"})
    assert res.ok is True and sent["element_index"] == 3 and sent["element_token"] == "tok_x"


def test_an_unrelated_invalid_field_does_not_weaken_strict_token_only_safety():
    strict = {"type": "object", "properties": {k: v for k, v in _CTX_PROPS.items() if k != "element_index"},
              "additionalProperties": False}
    backend = _backend({"click": strict}, tokens={3: "tok_x"})
    backend._active_pid = "not-an-integer"
    assert backend.click(element=3).ok is True
    sent = backend._session.call_tool.call_args.args[1]
    assert sent["element_token"] == "tok_x" and "element_index" not in sent
    backend = _backend({"click": strict}, tokens={})
    backend._active_pid = "not-an-integer"
    res = backend.click(element=3)
    assert res.ok is False and res.code == "stale_element"
    backend._session.call_tool.assert_not_called()
