"""Transport framing risks; no HTTP service, Docker, finance, or success mock."""

import ast
import copy
from pathlib import Path
from typing import Any

import pytest

from scripts.demo_loopback_relay import (
    MAX_BODY,
    capture_inspect,
    current_web_endpoint,
    relay_program,
    request_frame,
    sha,
)


def tool_services() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    api = {
        "Id": "TOOL_ONLY-api",
        "Image": "TOOL_ONLY-image",
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "HostConfig": {"NetworkMode": "container:TOOL_ONLY-db", "ReadonlyRootfs": True},
        "Mounts": [],
    }
    web = {
        "Id": "TOOL_ONLY-web",
        "Image": "TOOL_ONLY-web-image",
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "Config": {"Labels": {"owner": "TOOL_ONLY"}},
        "HostConfig": {"ReadonlyRootfs": False, "NetworkMode": "TOOL_ONLY-demo"},
        "Mounts": [],
        "NetworkSettings": {"Networks": {"TOOL_ONLY_demo_internal": {"IPAddress": "172.22.0.3"}}},
    }
    return api, copy.deepcopy(web), web


def endpoint(api: dict[str, Any], web: dict[str, Any], original: dict[str, Any]) -> str:
    return current_web_endpoint(
        api,
        web,
        original,
        api_id="TOOL_ONLY-api",
        db_id="TOOL_ONLY-db",
        api_image_id="TOOL_ONLY-image",
        project="TOOL_ONLY",
    )


def test_transport_preserves_registered_web_readonly_value_without_inventing_it() -> None:
    api, web, original = tool_services()
    assert endpoint(api, web, original) == "172.22.0.3"
    assert web["HostConfig"]["ReadonlyRootfs"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "id",
        "image",
        "namespace",
        "api_writable",
        "web_readonly",
        "hostconfig",
        "config",
        "mount",
        "network",
        "health",
    ],
)
def test_changed_actual_owner_settings_cannot_admit_ingress(mutation: str) -> None:
    api, web, original = tool_services()
    if mutation == "id":
        api["Id"] = "TOOL_ONLY-other"
    elif mutation == "image":
        web["Image"] = "TOOL_ONLY-other"
    elif mutation == "namespace":
        api["HostConfig"]["NetworkMode"] = "host"
    elif mutation == "api_writable":
        api["HostConfig"]["ReadonlyRootfs"] = False
    elif mutation == "web_readonly":
        web["HostConfig"]["ReadonlyRootfs"] = True
    elif mutation == "hostconfig":
        web["HostConfig"]["Privileged"] = True
    elif mutation == "config":
        web["Config"]["Labels"]["owner"] = "TOOL_ONLY-other"
    elif mutation == "mount":
        web["Mounts"] = [{"Type": "bind", "Source": "TOOL_ONLY-foreign"}]
    elif mutation == "network":
        web["NetworkSettings"]["Networks"]["TOOL_ONLY-foreign"] = {"IPAddress": "172.23.0.4"}
    else:
        api["State"]["Health"]["Status"] = "unhealthy"
    with pytest.raises(ValueError):
        endpoint(api, web, original)


def test_inspect_private_original_and_public_redaction_do_not_replace_financial_bodies(
    tmp_path: Path,
) -> None:
    password = "123abc" * 8
    raw = ('{"Env":["PASSWORD=' + password + '"],"literal":"other"}').encode()
    private = tmp_path / "private/original.json"
    public = tmp_path / "public.json"
    result = capture_inspect(raw, password, private, public)
    assert private.read_bytes() == raw
    assert password.encode() not in public.read_bytes()
    assert public.read_bytes() == raw.replace(password.encode(), b"[REDACTED]")
    assert result["private_original_sha256"] == sha(raw)
    assert result["public_sha256"] == sha(public.read_bytes())
    assert result["public_provenance"] == "REGISTERED_PASSWORD_LITERAL_REDACTION_V1"
    with pytest.raises(FileExistsError):
        capture_inspect(raw, password, private, public)


def test_invalid_inspect_secret_refuses_before_writing(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        capture_inspect(b"{}", "[REDACTED]", tmp_path / "private.json", tmp_path / "public.json")
    assert list(tmp_path.iterdir()) == []


def headers(extra: bytes = b"", first: bytes = b"POST /api/v1/actions/prepare HTTP/1.1") -> bytes:
    return first + b"\r\nHost: 127.0.0.1:15183\r\n" + extra + b"\r\n"


def test_preserves_exact_financial_body_and_original_path() -> None:
    body = b'{"effect":"literal\\nbody","amount":12345}'
    original = headers(
        b"Connection: keep-alive\r\nOrigin: http://127.0.0.1:15183\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
    )
    result = request_frame(original, body, 15183)
    assert result.split(b"\r\n\r\n", 1)[1] == body
    assert result.startswith(b"POST /api/v1/actions/prepare HTTP/1.1\r\n")
    assert b"keep-alive" not in result and result.count(b"Connection: close") == 1


@pytest.mark.parametrize(
    "extra,body,first",
    [
        (b"Content-Length: 1\r\nContent-Length: 1\r\n", b"x", None),
        (b"Content-Length: -1\r\n", b"", None),
        (b"Content-Length: 1\r\n", b"", None),
        (b"Transfer-Encoding: chunked\r\n", b"", None),
        (b"Upgrade: websocket\r\n", b"", None),
        (b"Origin: https://example.com\r\n", b"", None),
        (b"Host: 127.0.0.1:15183\r\n", b"", None),
        (b" folded: bad\r\n", b"", None),
        (b"", b"", b"CONNECT example.com:443 HTTP/1.1"),
        (b"", b"", b"POST http://example.com/path HTTP/1.1"),
        (b"", b"", b"TRACE / HTTP/1.1"),
    ],
)
def test_rejects_ambiguous_or_external_request(
    extra: bytes, body: bytes, first: bytes | None
) -> None:
    with pytest.raises(ValueError):
        request_frame(
            headers(extra, first or b"POST /api/v1/actions/prepare HTTP/1.1"), body, 15183
        )


def test_rejects_oversize_and_cross_port() -> None:
    with pytest.raises(ValueError):
        request_frame(
            headers(f"Content-Length: {MAX_BODY + 1}\r\n".encode()), b"x" * (MAX_BODY + 1), 15183
        )
    with pytest.raises(ValueError):
        request_frame(headers(), b"", 15184)


@pytest.mark.parametrize("value", ["example.com", "127.0.0.1;echo secret", "256.0.0.1", "::1"])
def test_program_cannot_choose_arbitrary_target(value: str) -> None:
    with pytest.raises(ValueError):
        relay_program(value)


def test_program_has_one_connect_and_no_retry() -> None:
    source = relay_program("172.22.0.3").decode()
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    connect = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "create_connection"
    ]
    assert len(connect) == 1
    assert "target=('172.22.0.3',80)" in source
    assert "sys.stdout.buffer.write(part)" in source
