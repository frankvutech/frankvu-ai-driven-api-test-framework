"""`BrokerClient` against a controlled `httpx.MockTransport`."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import allure
import httpx
import pytest

from api_tests.pact.broker import BrokerClient, BrokerError

pytestmark = [
    pytest.mark.unit,
    allure.parent_suite("Framework"),
    allure.suite("Unit"),
    allure.feature("Pact Broker client"),
]


def _pact(tmp_path: Path, consumer: str = "Mobile", provider: str = "Backend") -> Path:
    path = tmp_path / f"{consumer}-{provider}.json"
    path.write_text(json.dumps({"consumer": {"name": consumer}, "provider": {"name": provider}, "interactions": []}))
    return path


def _recording(seen: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    return handler


def _broker(handler: Any, **kwargs: Any) -> BrokerClient:
    return BrokerClient("http://broker.local", transport=httpx.MockTransport(handler), **kwargs)


def test_publish_sends_version_branch_and_base64_content(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"notices": [{"type": "success", "text": "published"}]})

    pact = _pact(tmp_path)
    notices = _broker(handler, token="tok-secret").publish(
        [pact], version="abc123", branch="main", tags=["main"], build_url="https://ci.local/1"
    )

    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/contracts/publish"
    assert seen[0].headers["Authorization"] == "Bearer tok-secret"
    assert body["pacticipantName"] == "Mobile"
    assert (body["pacticipantVersionNumber"], body["branch"], body["tags"]) == ("abc123", "main", ["main"])
    assert base64.b64decode(body["contracts"][0]["content"]) == pact.read_bytes()
    assert notices == [{"type": "success", "text": "published"}]


def test_publish_groups_contracts_by_consumer(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    broker = _broker(_recording(seen))

    broker.publish([_pact(tmp_path, "Android"), _pact(tmp_path, "iOS")], version="v1")

    assert sorted(json.loads(request.content)["pacticipantName"] for request in seen) == ["Android", "iOS"]


def test_publish_needs_a_version_and_files(tmp_path: Path) -> None:
    broker = _broker(lambda _: httpx.Response(200))

    with pytest.raises(BrokerError, match="version is required"):
        broker.publish([_pact(tmp_path)], version="")
    with pytest.raises(BrokerError, match="no pact files"):
        broker.publish([], version="v1")


def test_basic_auth_is_used_when_no_token(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    _broker(_recording(seen), username="u", password="p").publish([_pact(tmp_path)], version="v1")

    assert seen[0].headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(("deployable", "expected"), [(True, True), (False, False)])
def test_can_i_deploy(deployable: bool, expected: bool) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"summary": {"deployable": deployable, "reason": "because"}})

    result = _broker(handler).can_i_deploy("Mobile", "abc", environment="production")

    assert (result.deployable, result.reason) == (expected, "because")
    assert dict(seen[0].url.params) == {"pacticipant": "Mobile", "version": "abc", "environment": "production"}


def test_can_i_deploy_retries_while_the_answer_is_unknown() -> None:
    answers = iter([None, None, True])
    broker = _broker(lambda _: httpx.Response(200, json={"summary": {"deployable": next(answers), "reason": "r"}}))

    assert broker.can_i_deploy("M", "v", environment="prod", retries=2, retry_interval_seconds=0).deployable is True


def test_broker_errors_never_leak_the_token() -> None:
    broker = _broker(lambda _: httpx.Response(401, text="bad token tok-secret-value"), token="tok-secret-value")

    with pytest.raises(BrokerError) as raised:
        broker.can_i_deploy("M", "v", environment="prod")

    assert "tok-secret-value" not in str(raised.value)
    assert "401" in str(raised.value)


def test_an_unreachable_broker_is_an_error_not_a_pass() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(BrokerError, match="failed"):
        _broker(handler).can_i_deploy("M", "v", environment="prod")


def test_record_deployment_follows_the_environment_link() -> None:
    seen: list[httpx.Request] = []
    href = "http://broker.local/pacticipants/Backend/versions/v1/deployed-versions/environment/abc"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            links = [{"name": "test", "href": "http://broker.local/x"}, {"name": "production", "href": href}]
            return httpx.Response(200, json={"_links": {"pb:record-deployment": links}})
        return httpx.Response(201, json={})

    _broker(handler).record_deployment("Backend", "v1", environment="production")

    assert [(r.method, str(r.url)) for r in seen] == [
        ("GET", "http://broker.local/pacticipants/Backend/versions/v1"),
        ("POST", href),
    ]


def test_record_deployment_to_an_unknown_environment_is_an_error() -> None:
    broker = _broker(lambda _: httpx.Response(200, json={"_links": {"pb:record-deployment": []}}))

    with pytest.raises(BrokerError, match="not known to the Broker"):
        broker.record_deployment("Backend", "v1", environment="moon")
