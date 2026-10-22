"""Verified Cua 0.36 flat-contract adaptation (T-271).

Scope is deliberately narrow.  The five tools observed on the installed driver (click, scroll,
set_value, type_text, get_window_state) publish a flat ``additionalProperties: false`` object
schema in which ``element_index`` does not exist and ``element_token`` is an explicitly declared
string pattern.  Only a schema with exactly that shape is adapted:

* a recognised element action sends the snapshot token INSTEAD of the index, but only when the
  complete outbound payload validates; otherwise it is refused locally and never dispatched;
* a recognised ``get_window_state`` asks for ``tree_format="elements"`` only when the complete
  payload (including session, and on the CLI the real screenshot file) validates.

Everything else (composed, permissive, malformed, boolean, undiscovered, unadvertised or
unrecognised tools) must stay byte-for-byte what the installed baseline sent.

The schemas come from ``fixtures/cua_036_cli_schemas.json`` (``cua-driver describe`` output).
That is advertised metadata, not a daemon ``tools/list`` capture and not native acceptance; JSON
validation proves admissibility, not targeting semantics.

Upstream: NousResearch/hermes-agent#134857 (token-only targeting), #138596 (structured capture).
Stdlib + pytest only; no live cua-driver, no real GUI, no network.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict
from unittest.mock import MagicMock

import jsonschema.validators
import pytest

from tools.computer_use.cua_backend import CuaDriverBackend

_FIXTURES = Path(__file__).parent / "fixtures"
_METADATA = json.loads((_FIXTURES / "cua_036_cli_schemas.json").read_text(encoding="utf-8"))
_SID = "test-session"
_CAP = "accessibility.element_tokens"
_OK = {"data": "ok", "images": [], "image_mime_types": [], "structuredContent": None, "isError": False}


def _real(tool: str) -> Dict[str, Any]:
    return copy.deepcopy(_METADATA["tools"][tool]["input_schema"])


def _tok(index: int) -> str:
    return f"s0000002a:{index}"


def _backend(schemas: Dict[str, Any] | None = None, tokens: Dict[int, str] | None = None,
             capable: tuple[str, ...] = ()) -> CuaDriverBackend:
    backend = CuaDriverBackend()
    backend._session_id = _SID
    backend._session._tool_schemas = dict(_all_real() if schemas is None else schemas)
    backend._session._capabilities = {name: ({_CAP} if name in capable else set())
                                      for name in backend._session._tool_schemas}
    backend._active_pid, backend._active_window_id = 111, 222
    if tokens:
        _snapshot(backend, [{"element_index": i, "element_token": t} for i, t in tokens.items()])
    backend._session.call_tool = MagicMock(return_value=dict(_OK))
    return backend


def _snapshot(backend: CuaDriverBackend, rows: list) -> None:
    """Populate the backend's token cache the way production does: a real structured capture."""
    elements = [{"role": "AXButton", "label": f"row {row['element_index']}", **row} for row in rows]
    backend._session.call_tool = MagicMock(return_value={
        "data": "", "images": ["cG5n"], "image_mime_types": [], "isError": False,
        "structuredContent": {"tree_format": "elements", "elements": elements}})
    backend.capture("ax", pid=111, window_id=222)


def _all_real() -> Dict[str, Any]:
    return {name: _real(name) for name in _METADATA["tools"]}


def _sent(backend: CuaDriverBackend) -> Dict[str, Any]:
    assert backend._session.call_tool.call_count == 1
    return backend._session.call_tool.call_args.args[1]


# tool -> (invoke(backend, element), ordinary fields Hermes builds before targeting)
_ACTIONS: Dict[str, tuple[Callable[[CuaDriverBackend, Any], Any], Dict[str, Any]]] = {
    "click": (lambda b, e: b.click(element=e), {"pid": 111, "button": "left", "window_id": 222}),
    "scroll": (lambda b, e: b.scroll(direction="down", element=e),
               {"pid": 111, "direction": "down", "amount": 3, "window_id": 222}),
    "set_value": (lambda b, e: b.set_value("v", element=e), {"pid": 111, "window_id": 222, "value": "v"}),
}


def _tokenised(tool: str, index: int) -> Dict[str, Any]:
    return {**_ACTIONS[tool][1], "element_token": _tok(index), "session": _SID}


def _legacy(tool: str, index: Any) -> Dict[str, Any]:
    return {**_ACTIONS[tool][1], "element_index": index, "session": _SID}


def _no_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise ImportError("jsonschema unavailable")
    monkeypatch.setattr("jsonschema.validators.validator_for", unavailable)


def _broken_validator(monkeypatch: pytest.MonkeyPatch, when: Callable[[Any], bool] = lambda i: True) -> None:
    """Validation starts but evaluation raises (UNKNOWN), optionally only for some payloads."""
    real = jsonschema.validators.validator_for

    def patched(schema: Any, *args: Any, **kwargs: Any) -> Any:
        base = real(schema, *args, **kwargs)
        if args or kwargs:  # jsonschema's own metaschema lookup (passes default=): leave it alone
            return base

        class Broken:  # composition, not subclassing (jsonschema discourages subclassing)
            check_schema = staticmethod(base.check_schema)

            def __init__(self, schema: Any, **kw: Any) -> None:
                self._inner = base(schema, **kw)

            def is_valid(self, instance: Any) -> bool:
                if when(instance):
                    raise RuntimeError("evaluation failed")
                return self._inner.is_valid(instance)
        return Broken
    monkeypatch.setattr("jsonschema.validators.validator_for", patched)


# ---------------------------------------------------------------------------
# Fixture sanity: this is what "recognised" means.
# ---------------------------------------------------------------------------

# SHA-256 of the manager-verified source artifact (installed-cua-036-cli-schemas.json, LF, no final newline).
_FIXTURE_SHA256 = "ab3bfd206564c4a4f89f06e65791ee970806afd51574e88aa08c467c24901de0"


def test_the_fixture_is_the_verbatim_source_artifact():
    raw = (_FIXTURES / "cua_036_cli_schemas.json").read_bytes()
    # A Windows checkout with autocrlf may store CRLF; the blob (and the source) are LF.
    assert hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == _FIXTURE_SHA256


def test_the_verified_metadata_is_the_flat_contract_for_exactly_five_tools():
    assert _METADATA["version"] == "cua-driver 0.36.0"
    assert set(_METADATA["tools"]) == {"click", "scroll", "set_value", "type_text", "get_window_state"}
    for name, spec in _METADATA["tools"].items():
        schema = spec["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False, name
        assert set(schema) <= {"type", "properties", "additionalProperties", "required"}, name
        assert {"session", "pid"} <= set(schema["properties"]), name
    for name in ("click", "scroll", "set_value", "type_text"):
        properties = _real(name)["properties"]
        assert "element_index" not in properties
        assert properties["element_token"]["type"] == "string" and isinstance(properties["element_token"]["pattern"], str)


# ---------------------------------------------------------------------------
# Recognised element actions: token replaces the index, or local refusal with zero dispatch.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("index", [0, 1, 11, 4096])
def test_recognised_action_sends_the_current_token_instead_of_the_index(tool, index):
    backend = _backend(tokens={index: _tok(index), 99: _tok(99)})
    result = _ACTIONS[tool][0](backend, index)
    assert result.ok is True
    assert _sent(backend) == _tokenised(tool, index)  # exact dict: no element_index, nothing lost


def test_recognised_click_keeps_every_ordinary_field_and_the_foreground_choice():
    backend = _backend(tokens={11: _tok(11)})
    backend.click(element=11, button="right", modifiers=["ctrl"], delivery_mode="foreground")
    assert _sent(backend) == {"pid": 111, "button": "right", "window_id": 222, "modifier": ["ctrl"],
                              "delivery_mode": "foreground", "element_token": _tok(11), "session": _SID}


_BAD_TOKENS = {
    "missing": {},
    "token-for-another-index": {12: _tok(12)},
    "empty": {11: ""},
    "not-a-token": {11: "not-a-token"},
    "uppercase-hex": {11: "S0000002A:11"},
    "seven-hex-digits": {11: "s000002a:11"},
    "trailing-text": {11: _tok(11) + " "},
}


@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("name", list(_BAD_TOKENS))
def test_recognised_action_without_a_valid_current_token_is_refused_before_dispatch(tool, name):
    backend = _backend(tokens=_BAD_TOKENS[name])
    result = _ACTIONS[tool][0](backend, 11)
    assert result.ok is False and result.code == "stale_element"
    assert "11" in result.message and "capture" in result.message
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("element", [True, 11.0, "11", 1.5, [11], {"i": 11}],
                         ids=["bool", "float", "str", "fraction", "list", "dict"])
def test_recognised_action_never_aliases_a_bool_or_noninteger_to_a_cached_index(tool, element):
    backend = _backend(tokens={1: _tok(1), 11: _tok(11)})
    result = _ACTIONS[tool][0](backend, element)
    assert result.ok is False and result.code == "stale_element" and "capture" in result.message
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("tool", list(_ACTIONS))
def test_recognised_action_is_refused_when_the_validator_is_unavailable(monkeypatch, tool):
    _no_validator(monkeypatch)
    backend = _backend(tokens={11: _tok(11)})
    result = _ACTIONS[tool][0](backend, 11)
    assert result.ok is False and result.code == "stale_element" and "capture" in result.message
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("tool", list(_ACTIONS))
def test_recognised_action_is_refused_when_evaluation_is_unknown(monkeypatch, tool):
    _broken_validator(monkeypatch)
    backend = _backend(tokens={11: _tok(11)})
    result = _ACTIONS[tool][0](backend, 11)
    assert result.ok is False and result.code == "stale_element" and "capture" in result.message
    backend._session.call_tool.assert_not_called()


def test_a_driver_stale_verdict_is_the_drivers_own_response_and_is_not_retried():
    backend = _backend(tokens={11: _tok(11)})
    backend._session.call_tool.return_value = {
        "data": "stale", "images": [], "image_mime_types": [], "isError": True,
        "structuredContent": {"code": "snapshot_superseded", "message": "token superseded by a newer read"}}
    result = backend.click(element=11)
    assert result.ok is False and result.code == "snapshot_superseded"
    assert backend._session.call_tool.call_count == 1  # no retry introduced here


def test_a_recognised_scroll_with_an_element_but_no_window_never_falls_through():
    for coords in ({}, {"x": 1, "y": 2}):
        backend = _backend(tokens={11: _tok(11)})
        backend._active_window_id = None
        result = backend.scroll(direction="down", element=11, **coords)
        assert result.ok is False
        backend._session.call_tool.assert_not_called()


def test_type_text_has_no_element_path_and_sends_the_baseline_payload():
    backend = _backend(tokens={11: _tok(11)})
    assert backend.type_text("hello").ok is True
    assert _sent(backend) == {"pid": 111, "window_id": 222, "text": "hello", "session": _SID}


def test_coordinate_actions_on_recognised_schemas_are_untouched():
    backend = _backend(tokens={11: _tok(11)})
    assert backend.click(x=10, y=20).ok is True
    assert _sent(backend) == {"pid": 111, "button": "left", "x": 10, "y": 20, "window_id": 222, "session": _SID}


# ---------------------------------------------------------------------------
# Negative eligibility: everything outside the observed flat contract is baseline-identical.
# ---------------------------------------------------------------------------

_MANAGER_ONEOF = {"oneOf": [
    {"type": "object", "properties": {"element_index": {"type": "integer"}},
     "required": ["element_index"], "additionalProperties": False},
    {"type": "object", "properties": {"element_token": {"type": "string"}},
     "required": ["element_token"], "additionalProperties": False}]}
_MANAGER_Z_PROBE = {"type": "object", "properties": {"pid": {"type": "integer"}},
                    "allOf": [{"not": {"required": ["element_index"]}}, {"minProperties": 2},
                              {"propertyNames": {"not": {"const": "x-hermes-unrelated-probe"}}}]}
_REVIEW_Z_FAMILY = {
    "patternProperties+minProperties": {
        "type": "object", "minProperties": 3,
        "properties": {"pid": {"type": "integer"}, "session": {"type": "string"}},
        "patternProperties": {"^element_": {"type": "string"}}, "additionalProperties": False},
    "propertyNames": {
        "type": "object", "minProperties": 3,
        "properties": {"pid": {"type": "integer"}, "session": {"type": "string"}, "element_index": False},
        "propertyNames": {"pattern": "^(pid|session|element_[a-z]+)$"}, "additionalProperties": True},
    "not": {
        "type": "object", "minProperties": 3,
        "properties": {"pid": {"type": "integer"}, "session": {"type": "string"}},
        "not": {"anyOf": [{"required": ["element_index"]}, {"required": ["x-hermes-unrelated-probe"]}]},
        "additionalProperties": True},
    "conditionals": {
        "type": "object", "minProperties": 3,
        "properties": {"pid": {"type": "integer"}, "session": {"type": "string"}},
        "allOf": [{"if": {"required": ["element_index"]}, "then": False},
                  {"if": {"required": ["x-hermes-unrelated-probe"]}, "then": False}],
        "additionalProperties": True},
}


def _ineligible_click_schemas() -> Dict[str, Any]:
    base = _real("click")

    def with_(**keywords: Any) -> Dict[str, Any]:
        return {**base, **keywords}

    def with_props(**props: Any) -> Dict[str, Any]:
        return {**base, "properties": {**base["properties"], **props}}

    def without(key: str) -> Dict[str, Any]:
        return {k: v for k, v in base.items() if k != key}

    return {
        "oneOf-manager-repro": _MANAGER_ONEOF,
        "z-probe-manager": _MANAGER_Z_PROBE,
        **{f"z-family-{k}": v for k, v in _REVIEW_Z_FAMILY.items()},
        "allOf-wrapper": {"allOf": [base]},
        "anyOf-wrapper": {"anyOf": [base, {"type": "object"}]},
        "ref-wrapper": {"$ref": "#/$defs/click", "$defs": {"click": base}},
        "if-then": with_(**{"if": {"required": ["pid"]}, "then": {}}),
        "dependentRequired": with_(dependentRequired={"button": ["pid"]}),
        "dependentSchemas": with_(dependentSchemas={"button": {}}),
        "patternProperties": with_(patternProperties={"^x_": {}}),
        "extra-root-title": with_(title="click"),
        "extra-root-defs": with_(**{"$defs": {}}),
        "additionalProperties-omitted": without("additionalProperties"),
        "additionalProperties-true": with_(additionalProperties=True),
        "additionalProperties-schema": with_(additionalProperties={"type": "string"}),
        "type-missing": without("type"),
        "type-string": with_(type="string"),
        "type-list": with_(type=["object", "null"]),
        "properties-list": with_(properties=[]),
        "properties-missing": without("properties"),
        "boolean-true": True,
        "boolean-false": False,
        "list-root": [base],
        "malformed-required": with_(required="pid"),
        "duplicate-required": with_(required=["pid", "pid"]),
        "element-index-declared": with_props(element_index={"type": "integer"}),
        "token-unadvertised": {**base, "properties": {k: v for k, v in base["properties"].items()
                                                      if k != "element_token"}},
        "token-false": with_props(element_token=False),
        "token-true": with_props(element_token=True),
        "token-without-pattern": with_props(element_token={"type": "string"}),
        "token-pattern-without-type": with_props(element_token={"pattern": "^s"}),
        "token-integer": with_props(element_token={"type": "integer", "pattern": "^s"}),
    }


_INELIGIBLE = _ineligible_click_schemas()


@pytest.mark.parametrize("name", list(_INELIGIBLE))
def test_an_ineligible_schema_keeps_the_exact_baseline_request(name):
    backend = _backend({"click": _INELIGIBLE[name]}, tokens={11: _tok(11)})
    assert backend.click(element=11).ok is True
    assert _sent(backend) == _legacy("click", 11)  # index kept, no new token, no refusal


@pytest.mark.parametrize("name", list(_INELIGIBLE))
def test_an_ineligible_schema_keeps_the_historical_capability_attachment(name):
    backend = _backend({"click": _INELIGIBLE[name]}, tokens={11: _tok(11)}, capable=("click",))
    assert backend.click(element=11).ok is True
    assert _sent(backend) == {**_legacy("click", 11), "element_token": _tok(11)}


@pytest.mark.parametrize("name", list(_INELIGIBLE))
def test_an_ineligible_schema_never_refuses_a_tokenless_index(name):
    backend = _backend({"click": _INELIGIBLE[name]}, tokens={})
    assert backend.click(element=0).ok is True
    assert _sent(backend) == _legacy("click", 0)


def test_an_unrecognised_tool_name_is_never_adapted():
    backend = _backend(tokens={11: _tok(11)})  # all five real schemas are present, double_click is not
    assert backend.click(element=11, click_count=2).ok is True
    assert _sent(backend) == {"pid": 111, "button": "left", "element_index": 11, "window_id": 222,
                              "session": _SID}
    assert backend._session.call_tool.call_args.args[0] == "double_click"


@pytest.mark.parametrize("tool", list(_ACTIONS))
def test_a_recognised_looking_schema_under_an_unrecognised_name_is_not_adapted(tool):
    backend = _backend({tool: _real(tool), "double_click": _real("click")}, tokens={11: _tok(11)})
    backend.click(element=11, click_count=2)
    assert _sent(backend) == {"pid": 111, "button": "left", "element_index": 11, "window_id": 222,
                              "session": _SID}


def test_no_discovered_schema_keeps_the_historical_capability_attachment():
    backend = _backend({}, tokens={11: _tok(11)}, capable=("click",))
    backend._session._capabilities = {"click": {_CAP}}
    assert backend.click(element=11).ok is True
    assert _sent(backend) == {**_legacy("click", 11), "element_token": _tok(11)}


def test_no_discovered_schema_and_no_capability_sends_only_the_index():
    backend = _backend({}, tokens={11: _tok(11)})
    assert backend.click(element=11).ok is True
    assert _sent(backend) == _legacy("click", 11)


def test_the_older_0_9_driver_fixture_keeps_index_and_capability_token_beside_each_other():
    listing = json.loads((Path(__file__).parents[1] / "fixtures" / "cua_driver_0_9_tools_list.json")
                         .read_text(encoding="utf-8"))
    click = next(tool for tool in listing["tools"] if tool["name"] == "click")
    backend = _backend({"click": click["inputSchema"]}, tokens={5: "tok5"})
    backend._session._capabilities = {"click": set(click["capabilities"])}
    assert backend.click(element=5).ok is True
    assert _sent(backend) == {**_legacy("click", 5), "element_token": "tok5"}


def test_the_manager_oneof_failure_keeps_a_valid_index_only_request():
    backend = _backend({"click": _MANAGER_ONEOF}, tokens={1: "tok_synthetic"})
    backend._active_pid = backend._active_window_id = None
    res = backend._action("click", {"element_index": 1})
    assert res.ok is True
    assert backend._session.call_tool.call_args.args[1] == {"element_index": 1, "session": _SID}


def test_a_nonrecognised_flat_schema_for_a_scroll_without_a_window_keeps_the_baseline_request():
    backend = _backend({"scroll": {}}, tokens={11: _tok(11)})
    backend._active_window_id = None
    assert backend.scroll(direction="down", element=11).ok is True
    assert _sent(backend) == {"pid": 111, "direction": "down", "amount": 3, "session": _SID}


# ---------------------------------------------------------------------------
# Capture: structured `elements` view, MCP and CLI re-fetch.
# ---------------------------------------------------------------------------

_LG = {"pid": 101, "window_id": 202, "session": _SID}  # installed-baseline get_window_state request
_C = {**_LG, "tree_format": "elements"}
_WINDOW = {"element_index": 0, "role": "AXWindow", "label": "Fixture",
           "element_token": "s1234abcd:0", "frame": {"x": 0, "y": 0, "w": 800, "h": 600}}
_BUTTON = {"element_index": 7, "role": "AXButton", "label": "Increment",
           "element_token": "s1234abcd:7", "frame": {"x": 10, "y": 20, "w": 30, "h": 40}}
_ELEMENTS_RESPONSE = {
    "data": "window_id=202 pid=101 elements=2 snapshot_id=s1234abcd\n\n(tree is in structuredContent.elements)",
    "images": ["cG5n"], "image_mime_types": [], "isError": False,
    "structuredContent": {"tree_format": "elements", "snapshot_id": "s1234abcd", "elements": [_WINDOW, _BUTTON]}}
_EMPTY = {"data": "", "images": [], "image_mime_types": [], "structuredContent": None, "isError": False}


def _capture_backend(schema: Any = None) -> CuaDriverBackend:
    schemas = {"get_window_state": _real("get_window_state") if schema is None else schema}
    backend = _backend(schemas)
    backend._active_pid = backend._active_window_id = None
    return backend


def _mcp_requests(backend: CuaDriverBackend, response: Dict[str, Any]) -> list[Dict[str, Any]]:
    seen: list[Dict[str, Any]] = []
    backend._session.call_tool = MagicMock(side_effect=lambda name, args, **kw: seen.append(dict(args)) or response)
    return seen


@pytest.mark.parametrize(("mode", "extra", "title"), [
    ("ax", {"window_title": "Driver title"}, "Driver title"),
    ("som", {}, "Fixture"),
])
def test_recognised_capture_requests_elements_and_keeps_frames_title_and_tokens(mode, extra, title):
    backend = _capture_backend()
    response = {**_ELEMENTS_RESPONSE,
                "structuredContent": {**_ELEMENTS_RESPONSE["structuredContent"], **extra}}
    seen = _mcp_requests(backend, response)
    cap = backend.capture(mode, pid=101, window_id=202)
    assert seen == [_C]
    assert cap.window_title == title and cap.png_b64 == "cG5n"
    assert [(e.index, e.bounds, e.element_token) for e in cap.elements] == [
        (0, (0, 0, 800, 600), "s1234abcd:0"), (7, (10, 20, 30, 40), "s1234abcd:7")]
    assert backend._snapshot_tokens == {0: "s1234abcd:0", 7: "s1234abcd:7"}


def test_a_capture_then_a_recognised_action_uses_the_captured_token_end_to_end():
    backend = _capture_backend()
    backend._session._tool_schemas["click"] = _real("click")
    _mcp_requests(backend, _ELEMENTS_RESPONSE)
    backend.capture("ax", pid=101, window_id=202)
    backend._session.call_tool = MagicMock(return_value=dict(_OK))
    assert backend.click(element=7).ok is True
    assert _sent(backend) == {"pid": 101, "button": "left", "window_id": 202,
                              "element_token": "s1234abcd:7", "session": _SID}


def test_vision_capture_never_gains_tree_format_on_mcp():
    backend = _capture_backend()
    seen = _mcp_requests(backend, _ELEMENTS_RESPONSE)
    cap = backend.capture("vision", pid=101, window_id=202)
    assert seen == [_LG]
    assert cap.png_b64 == "cG5n" and cap.elements == [] and backend._snapshot_tokens == {}


def _install_cli(monkeypatch, body: Dict[str, Any], cli_requests: list, shot_files: list) -> None:
    import subprocess
    monkeypatch.setattr("tools.computer_use.cua_backend.resolve_cua_driver_cmd", lambda *a, **k: "unused-driver")

    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["unused-driver", "call", "get_window_state"]
        args = json.loads(cmd[3])
        shot_files.append(Path(args["screenshot_out_file"]))
        assert shot_files[-1].is_file()
        cli_requests.append(args)
        return MagicMock(returncode=0, stdout=json.dumps(body), stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)


def _without_file(request: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in request.items() if k != "screenshot_out_file"}


@pytest.mark.parametrize("mode", ["ax", "som"])
def test_the_cli_refetch_keeps_the_validated_view_plus_the_real_screenshot_file(monkeypatch, mode):
    backend = _capture_backend()
    mcp = _mcp_requests(backend, _EMPTY)  # degenerate MCP result forces the CLI re-fetch
    cli: list = []
    files: list = []
    _install_cli(monkeypatch, {**_ELEMENTS_RESPONSE["structuredContent"], "screenshot_png_b64": "cG5n"}, cli, files)
    cap = backend.capture(mode, pid=101, window_id=202)
    assert mcp == [_C]
    assert len(cli) == 1 and "screenshot_out_file" in cli[0] and _without_file(cli[0]) == _C
    assert [e.element_token for e in cap.elements] == ["s1234abcd:0", "s1234abcd:7"]
    assert cap.window_title == "Fixture" and cap.png_b64 == "cG5n"
    assert all(not f.exists() for f in files)


def test_the_vision_cli_refetch_keeps_the_baseline_request_plus_the_file(monkeypatch):
    backend = _capture_backend()
    mcp = _mcp_requests(backend, _EMPTY)
    cli: list = []
    files: list = []
    _install_cli(monkeypatch, {"screenshot_png_b64": "cG5n"}, cli, files)
    cap = backend.capture("vision", pid=101, window_id=202)
    assert mcp == [_LG] and len(cli) == 1 and _without_file(cli[0]) == _LG
    assert cap.png_b64 == "cG5n" and cap.elements == []
    assert all(not f.exists() for f in files)


_MARKDOWN_BODY = {"element_count": 1, "tree_markdown": '- [3] AXButton "OK"', "screenshot_png_b64": "cG5n"}


def test_an_invalid_file_augmented_cli_request_drops_only_the_added_tree_format(monkeypatch):
    schema = _real("get_window_state")
    del schema["properties"]["screenshot_out_file"]  # MCP request still valid, CLI request is not
    backend = _capture_backend(schema)
    mcp = _mcp_requests(backend, _EMPTY)
    cli: list = []
    files: list = []
    _install_cli(monkeypatch, _MARKDOWN_BODY, cli, files)
    cap = backend.capture("ax", pid=101, window_id=202)
    assert mcp == [_C]
    assert len(cli) == 1 and "screenshot_out_file" in cli[0] and _without_file(cli[0]) == _LG
    assert [(e.index, e.label) for e in cap.elements] == [(3, "OK")]  # markdown fallback preserved
    assert all(not f.exists() for f in files)


def test_an_unknown_file_augmented_cli_validation_drops_only_the_added_tree_format(monkeypatch):
    backend = _capture_backend()
    mcp = _mcp_requests(backend, _EMPTY)
    cli: list = []
    files: list = []
    _install_cli(monkeypatch, _MARKDOWN_BODY, cli, files)
    _broken_validator(monkeypatch, when=lambda instance: "screenshot_out_file" in instance)
    backend.capture("ax", pid=101, window_id=202)
    assert mcp == [_C]  # the MCP request validated before the file existed
    assert len(cli) == 1 and _without_file(cli[0]) == _LG and "screenshot_out_file" in cli[0]


@pytest.mark.parametrize("breakage", ["unavailable", "unknown"])
def test_without_a_usable_validator_capture_sends_the_baseline_request(monkeypatch, breakage):
    (_no_validator if breakage == "unavailable" else _broken_validator)(monkeypatch)
    backend = _capture_backend()
    seen = _mcp_requests(backend, _EMPTY)
    cli: list = []
    files: list = []
    _install_cli(monkeypatch, _MARKDOWN_BODY, cli, files)
    backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG] and len(cli) == 1 and _without_file(cli[0]) == _LG


def test_an_invalid_structured_request_falls_back_to_the_baseline_request():
    schema = _real("get_window_state")
    schema["required"] = ["pid", "window_id", "undeclared_but_required"]  # nothing can satisfy it
    backend = _capture_backend(schema)
    seen = _mcp_requests(backend, _ELEMENTS_RESPONSE)
    backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG]


def _ineligible_capture_schemas() -> Dict[str, Any]:
    base = _real("get_window_state")

    def with_(**keywords: Any) -> Dict[str, Any]:
        return {**base, **keywords}

    def with_format(value: Any) -> Dict[str, Any]:
        return {**base, "properties": {**base["properties"], "tree_format": value}}

    return {
        "allOf-markdown-only-manager-repro": {
            "type": "object", "properties": {"tree_format": {}},
            "allOf": [{"properties": {"tree_format": {"enum": ["markdown"]}}}]},
        "allOf-wrapper": {"allOf": [base]},
        "oneOf-wrapper": {"oneOf": [base]},
        "ref-wrapper": {"$ref": "#/$defs/w", "$defs": {"w": base}},
        "if-then": with_(**{"if": {"required": ["pid"]}, "then": {}}),
        "dependentRequired": with_(dependentRequired={"tree_format": ["pid"]}),
        "extra-root-title": with_(title="x"),
        "additionalProperties-omitted": {k: v for k, v in base.items() if k != "additionalProperties"},
        "additionalProperties-true": with_(additionalProperties=True),
        "type-missing": {k: v for k, v in base.items() if k != "type"},
        "boolean-true": True,
        "boolean-false": False,
        "list-root": [base],
        "tree-format-undeclared": {**base, "properties": {k: v for k, v in base["properties"].items()
                                                          if k != "tree_format"}},
        "tree-format-empty-schema": with_format({}),
        "tree-format-true": with_format(True),
        "tree-format-false": with_format(False),
        "tree-format-no-elements": with_format({"type": "string", "enum": ["markdown", "both"]}),
        "tree-format-no-enum": with_format({"type": "string"}),
        "tree-format-no-type": with_format({"enum": ["markdown", "elements", "both"]}),
        "tree-format-integer": with_format({"type": "integer"}),
        "tree-format-ref": with_format({"$ref": "#/$defs/f"}),
    }


_INELIGIBLE_CAPTURE = _ineligible_capture_schemas()


@pytest.mark.parametrize("name", list(_INELIGIBLE_CAPTURE))
def test_an_ineligible_capture_schema_keeps_the_baseline_request(name):
    backend = _capture_backend(_INELIGIBLE_CAPTURE[name])
    seen = _mcp_requests(backend, _ELEMENTS_RESPONSE)
    backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG]


def test_an_undiscovered_capture_schema_keeps_the_baseline_request_and_markdown_parsing():
    backend = _backend({})
    backend._active_pid = backend._active_window_id = None
    seen = _mcp_requests(backend, {"data": '1 elements\n- [0] AXWindow "Legacy title"\n- [1] AXButton "OK"',
                                   "images": ["cG5n"], "image_mime_types": [], "structuredContent": None,
                                   "isError": False})
    cap = backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG] and cap.window_title == "Legacy title" and [e.index for e in cap.elements] == [0, 1]


def test_a_failed_capture_disarms_previous_tokens_so_a_recognised_action_is_refused():
    backend = _backend(tokens={11: _tok(11)})
    backend._clear_active_target()
    backend._active_pid, backend._active_window_id = 111, 222
    result = backend.click(element=11)
    assert result.ok is False and result.code == "stale_element"
    backend._session.call_tool.assert_not_called()


def test_reconnect_clears_cached_schemas_with_the_capabilities():
    session = CuaDriverBackend()._session
    session._started, session._capabilities, session._capability_version = False, {"click": {"x"}}, "v"
    session._tool_schemas = {"click": _real("click")}
    session._start_lifecycle_locked = lambda: None
    session._restart_session_locked()
    assert session._capabilities == {} and session._tool_schemas == {}


# ---------------------------------------------------------------------------
# A schema that is not valid JSON Schema is not recognised: installed-baseline behaviour.
# (An unavailable/failing VALIDATOR on a valid recognised schema still refuses; see above.)
# ---------------------------------------------------------------------------

_MALFORMED = ["nested-property-is-an-integer", "type-is-an-integer", "enum-is-not-a-list",
              "token-pattern-is-an-invalid-regex"]


def _malformed(tool: str, name: str) -> Dict[str, Any]:
    schema = _real(tool)
    if name == "nested-property-is-an-integer":
        schema["properties"]["pid"] = 123
    elif name == "type-is-an-integer":
        schema["properties"]["pid"] = {"type": 7}
    elif name == "enum-is-not-a-list":
        schema["properties"]["session"] = {"enum": "x"}
    else:
        schema["properties"]["element_token"]["pattern"] = "["
    return schema


@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("name", _MALFORMED)
@pytest.mark.parametrize("capable", [False, True], ids=["no-capability", "capability"])
def test_a_json_schema_invalid_action_schema_keeps_the_exact_baseline_request(tool, name, capable):
    backend = _backend({tool: _malformed(tool, name)}, tokens={11: _tok(11)}, capable=(tool,) if capable else ())
    assert _ACTIONS[tool][0](backend, 11).ok is True
    expected = {**_legacy(tool, 11), **({"element_token": _tok(11)} if capable else {})}
    assert _sent(backend) == expected


@pytest.mark.parametrize("name", _MALFORMED[:3])
def test_a_json_schema_invalid_capture_schema_keeps_the_baseline_request(name):
    backend = _capture_backend(_malformed("get_window_state", name))
    seen = _mcp_requests(backend, _ELEMENTS_RESPONSE)
    backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG]


# ---------------------------------------------------------------------------
# Eligibility needs the LITERAL observed token pattern, not any pattern that validates.
# ---------------------------------------------------------------------------

_OBSERVED_PATTERN = "^s[0-9a-f]{8}:[0-9]+$"
_OTHER_PATTERNS = {
    "match-anything": (".*", "garbage"),
    "uppercase-expanded": ("^s[0-9a-fA-F]{8}:[0-9]+$", _tok(11)),
    "digit-class-spelled-differently": (r"^s[0-9a-f]{8}:\d+$", _tok(11)),
    "no-end-anchor": ("^s[0-9a-f]{8}:[0-9]+", _tok(11)),
    "no-start-anchor": ("s[0-9a-f]{8}:[0-9]+$", _tok(11)),
    "longer-snapshot-id": ("^s[0-9a-f]{16}:[0-9]+$", "s0000002a0000002a:11"),
    "invalid-regex": ("[", _tok(11)),
}


def test_the_observed_pattern_literal_is_what_the_fixture_publishes():
    for tool in _ACTIONS:
        assert _real(tool)["properties"]["element_token"]["pattern"] == _OBSERVED_PATTERN


@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("name", list(_OTHER_PATTERNS))
@pytest.mark.parametrize("capable", [False, True], ids=["no-capability", "capability"])
def test_a_token_pattern_other_than_the_observed_one_keeps_the_exact_baseline_request(tool, name, capable):
    pattern, token = _OTHER_PATTERNS[name]
    schema = _real(tool)
    schema["properties"]["element_token"]["pattern"] = pattern
    backend = _backend({tool: schema}, tokens={11: token}, capable=(tool,) if capable else ())
    assert _ACTIONS[tool][0](backend, 11).ok is True
    assert _sent(backend) == {**_legacy(tool, 11), **({"element_token": token} if capable else {})}


# ---------------------------------------------------------------------------
# Snapshot safety on the recognised path: only a row whose captured index is an exact int counts.
# The (pre-existing) parser also accepts a bool index; True aliases the dict key 1 and False aliases 0.
# ---------------------------------------------------------------------------

def _after_capture(rows: list, schemas: Dict[str, Any] | None = None) -> CuaDriverBackend:
    backend = _backend(schemas)
    _snapshot(backend, rows)
    backend._session.call_tool = MagicMock(return_value=dict(_OK))
    return backend


_ALIASED_SNAPSHOTS = {
    "bool-true-row-only": ([{"element_index": True, "element_token": _tok(1)}], 1),
    "bool-false-row-only": ([{"element_index": False, "element_token": _tok(0)}], 0),
    "int-row-then-bool-alias": ([{"element_index": 1, "element_token": _tok(1)},
                                 {"element_index": True, "element_token": "s0000002a:101"}], 1),
    "bool-alias-then-int-row": ([{"element_index": True, "element_token": "s0000002a:101"},
                                 {"element_index": 1, "element_token": _tok(1)}], 1),
}


@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("name", list(_ALIASED_SNAPSHOTS))
def test_a_bool_row_never_supplies_a_token_for_an_integer_action(tool, name):
    rows, index = _ALIASED_SNAPSHOTS[name]
    backend = _after_capture(rows)
    result = _ACTIONS[tool][0](backend, index)
    assert result.ok is False and result.code == "stale_element" and "capture" in result.message
    backend._session.call_tool.assert_not_called()


@pytest.mark.parametrize("tool", list(_ACTIONS))
@pytest.mark.parametrize("index", [0, 1, 7])
def test_an_exact_int_row_still_supplies_its_own_token_beside_unrelated_rows(tool, index):
    backend = _after_capture([{"element_index": index, "element_token": _tok(index)},
                              {"element_index": 40, "element_token": _tok(40)}])
    assert _ACTIONS[tool][0](backend, index).ok is True
    assert _sent(backend) == _tokenised(tool, index)


def test_a_bool_row_does_not_disturb_a_different_exact_int_row():
    backend = _after_capture([{"element_index": True, "element_token": "s0000002a:101"},
                              {"element_index": 5, "element_token": _tok(5)}])
    assert backend.click(element=5).ok is True
    assert _sent(backend) == _tokenised("click", 5)


def test_an_unrecognised_schema_keeps_the_historical_bool_cache_behaviour():
    """Baseline behaviour outside the verified contract, documented but NOT claimed repaired."""
    backend = _backend({}, capable=("click",))
    backend._session._capabilities = {"click": {_CAP}}
    _snapshot(backend, [{"element_index": True, "element_token": _tok(1)}])
    backend._session.call_tool = MagicMock(return_value=dict(_OK))
    assert backend.click(element=1).ok is True
    assert _sent(backend) == {**_legacy("click", 1), "element_token": _tok(1)}


def test_a_recognised_action_only_trusts_tokens_from_a_recognised_structured_capture():
    schemas = _all_real()
    del schemas["get_window_state"]  # the capture request stays the baseline one
    backend = _after_capture([{"element_index": 11, "element_token": _tok(11)}], schemas)
    assert backend._snapshot_tokens == {11: _tok(11)}  # the baseline cache is untouched
    result = backend.click(element=11)
    assert result.ok is False and result.code == "stale_element"
    backend._session.call_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Structured title fallback exists only for the validated structured request.
# ---------------------------------------------------------------------------

_STRUCTURED_TITLE_RESPONSE = {
    "data": "", "images": ["cG5n"], "image_mime_types": [], "isError": False,
    "structuredContent": {"window_title": "New title", "elements": [_WINDOW]}}


@pytest.mark.parametrize("name", ["allOf-wrapper", "additionalProperties-true", "tree-format-empty-schema",
                                  "boolean-true", "tree-format-no-elements", "extra-root-title"])
def test_an_unrecognised_capture_schema_keeps_the_baseline_empty_title(name):
    backend = _capture_backend(_INELIGIBLE_CAPTURE[name])
    seen = _mcp_requests(backend, _STRUCTURED_TITLE_RESPONSE)
    cap = backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG] and cap.window_title == ""
    assert backend._snapshot_tokens == {0: "s1234abcd:0"}  # baseline cache handling is untouched


def test_an_undiscovered_capture_schema_keeps_the_baseline_empty_title():
    backend = _backend({})
    seen = _mcp_requests(backend, _STRUCTURED_TITLE_RESPONSE)
    assert backend.capture("ax", pid=101, window_id=202).window_title == "" and seen == [_LG]


@pytest.mark.parametrize("breakage", ["unavailable", "unknown"])
def test_an_unvalidated_structured_request_keeps_the_baseline_empty_title(monkeypatch, breakage):
    (_no_validator if breakage == "unavailable" else _broken_validator)(monkeypatch)
    backend = _capture_backend()
    seen = _mcp_requests(backend, _STRUCTURED_TITLE_RESPONSE)
    assert backend.capture("ax", pid=101, window_id=202).window_title == "" and seen == [_LG]


def test_an_invalid_structured_request_keeps_the_baseline_empty_title():
    schema = _real("get_window_state")
    schema["required"] = ["pid", "window_id", "undeclared_but_required"]
    backend = _capture_backend(schema)
    seen = _mcp_requests(backend, _STRUCTURED_TITLE_RESPONSE)
    assert backend.capture("ax", pid=101, window_id=202).window_title == "" and seen == [_LG]


def test_the_validated_structured_request_keeps_the_title_fallback():
    backend = _capture_backend()
    seen = _mcp_requests(backend, _STRUCTURED_TITLE_RESPONSE)
    assert backend.capture("ax", pid=101, window_id=202).window_title == "New title" and seen == [_C]
    no_title = {**_STRUCTURED_TITLE_RESPONSE, "structuredContent": {"elements": [_WINDOW]}}
    seen = _mcp_requests(backend, no_title)
    assert backend.capture("som", pid=101, window_id=202).window_title == "Fixture" and seen == [_C]


# ---------------------------------------------------------------------------
# Remote schema references are never retrieved.
# ---------------------------------------------------------------------------

def _record_network(monkeypatch: pytest.MonkeyPatch) -> list:
    reads: list = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: reads.append(a))
    return reads


def test_a_remote_reference_on_an_ineligible_schema_is_never_fetched(monkeypatch):
    reads = _record_network(monkeypatch)
    backend = _backend({"click": {"$ref": "https://invalid.example/click.json"}}, tokens={11: _tok(11)})
    assert backend.click(element=11).ok is True
    assert _sent(backend) == _legacy("click", 11) and reads == []


@pytest.mark.parametrize("tool", list(_ACTIONS))
def test_a_remote_reference_inside_a_recognised_schema_is_unknown_so_the_action_is_refused(monkeypatch, tool):
    reads = _record_network(monkeypatch)
    schema = _real(tool)
    schema["properties"]["session"] = {"$ref": "https://invalid.example/session.json"}
    backend = _backend({**_all_real(), tool: schema}, tokens={11: _tok(11)})
    result = _ACTIONS[tool][0](backend, 11)
    assert result.ok is False and result.code == "stale_element" and "capture" in result.message
    backend._session.call_tool.assert_not_called()
    assert reads == []


def test_a_remote_reference_inside_the_capture_schema_is_unknown_so_the_baseline_request_is_kept(monkeypatch):
    reads = _record_network(monkeypatch)
    schema = _real("get_window_state")
    schema["properties"]["session"] = {"$ref": "https://invalid.example/session.json"}
    backend = _capture_backend(schema)
    seen = _mcp_requests(backend, _ELEMENTS_RESPONSE)
    backend.capture("ax", pid=101, window_id=202)
    assert seen == [_LG] and reads == []
