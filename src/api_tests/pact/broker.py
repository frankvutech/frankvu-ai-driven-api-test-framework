"""Pact Broker integration: publish contracts and ask `can-i-deploy`.

Uses the Broker's documented HTTP API directly (`POST /contracts/publish`, `GET /can-i-deploy`) so
the framework needs no Ruby or Docker tooling to talk to a Broker. Provider verification *results*
are published by the Pact verifier itself (`verification.py`), not from here.

    python -m api_tests.pact.broker publish --version "$GITHUB_SHA" --branch "$GITHUB_REF_NAME"
    python -m api_tests.pact.broker can-i-deploy --pacticipant <name> --version "$GITHUB_SHA" \
        --environment production
    python -m api_tests.pact.broker record-deployment --pacticipant <name> --version "$GITHUB_SHA" \
        --environment production

Exit codes: 0 success / deployable, 1 not deployable, 2 configuration or Broker error. A Broker that
cannot be reached is an error (2), never a pass.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from api_tests.config import Settings, load_settings
from api_tests.redaction import Redactor


class BrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanIDeployResult:
    deployable: bool | None
    reason: str
    raw: dict[str, Any]


class BrokerClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        auth: httpx.Auth | None = None
        headers = {"Accept": "application/hal+json, application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif username and password:
            auth = httpx.BasicAuth(username, password)
        self._redactor = Redactor([secret for secret in (token, password) if secret])
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, auth=auth, timeout=timeout_seconds, transport=transport
        )

    @classmethod
    def from_settings(cls, settings: Settings, **kwargs: Any) -> BrokerClient:
        if not settings.pact_broker_url:
            raise BrokerError("MTP_PACT_BROKER_URL is not set")
        return cls(
            settings.pact_broker_url,
            token=settings.pact_broker_token.get_secret_value() if settings.pact_broker_token else None,
            username=settings.pact_broker_username,
            password=settings.pact_broker_password.get_secret_value() if settings.pact_broker_password else None,
            **kwargs,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BrokerClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def publish(
        self,
        pact_files: list[Path],
        *,
        version: str,
        branch: str | None = None,
        tags: list[str] | None = None,
        build_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """Publish each pacticipant's contracts with its version and branch. Returns Broker notices."""
        if not version:
            raise BrokerError("a consumer version is required to publish (use the commit SHA)")
        if not pact_files:
            raise BrokerError("no pact files to publish")
        by_consumer: dict[str, list[dict[str, Any]]] = {}
        for path in pact_files:
            content = path.read_bytes()
            document = json.loads(content)
            consumer = document["consumer"]["name"]
            by_consumer.setdefault(consumer, []).append(
                {
                    "consumerName": consumer,
                    "providerName": document["provider"]["name"],
                    "specification": "pact",
                    "contentType": "application/json",
                    "content": base64.b64encode(content).decode("ascii"),
                }
            )
        notices: list[dict[str, Any]] = []
        for consumer, contracts in by_consumer.items():
            body: dict[str, Any] = {
                "pacticipantName": consumer,
                "pacticipantVersionNumber": version,
                "contracts": contracts,
            }
            if branch:
                body["branch"] = branch
            if tags:
                body["tags"] = tags
            if build_url:
                body["buildUrl"] = build_url
            response = self._request("POST", "/contracts/publish", json=body)
            notices.extend(response.json().get("notices", []))
        return notices

    def can_i_deploy(
        self,
        pacticipant: str,
        version: str,
        *,
        environment: str,
        retries: int = 0,
        retry_interval_seconds: float = 10.0,
    ) -> CanIDeployResult:
        """Ask whether `pacticipant@version` is compatible with everything in `environment`.

        `deployable` is `None` while the Broker is still waiting for a verification result; that is
        retried `retries` times and then reported as not deployable.
        """
        params = {"pacticipant": pacticipant, "version": version, "environment": environment}
        for attempt in range(retries + 1):
            response = self._request("GET", "/can-i-deploy", params=params)
            body = response.json()
            summary = body.get("summary", {})
            deployable = summary.get("deployable")
            if deployable is not None or attempt == retries:
                return CanIDeployResult(deployable, str(summary.get("reason", "")), body)
            time.sleep(retry_interval_seconds)
        raise AssertionError("unreachable")  # pragma: no cover

    def record_deployment(self, pacticipant: str, version: str, *, environment: str) -> None:
        """Tell the Broker `pacticipant@version` is now deployed to `environment`.

        Follows the version resource's `pb:record-deployment` link for that environment, which is how
        the Broker's HAL API exposes it. Run this *after* a real deployment, never instead of one.
        """
        resource = self._request("GET", f"/pacticipants/{quote(pacticipant)}/versions/{quote(version)}").json()
        links = resource.get("_links", {}).get("pb:record-deployment", [])
        for link in links if isinstance(links, list) else [links]:
            if link.get("name") == environment:
                self._request("POST", link["href"], json={})
                return
        known = [link.get("name") for link in links] if isinstance(links, list) else []
        raise BrokerError(f"environment {environment!r} is not known to the Broker (known: {known})")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise BrokerError(self._redactor.text(f"{method} {path} failed: {error!r}")) from error
        if response.status_code >= 400:
            raise BrokerError(
                self._redactor.text(f"{method} {path} answered {response.status_code}: {response.text[:500]}")
            )
        return response


# --------------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api_tests.pact.broker", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="publish pact files to the Broker")
    publish.add_argument("files", nargs="*", type=Path, help="pact files (default: every file in MTP_PACT_DIR)")
    publish.add_argument("--version", required=True, help="consumer version, e.g. the commit SHA")
    publish.add_argument("--branch")
    publish.add_argument("--tag", action="append", default=[])
    publish.add_argument("--build-url")

    record = commands.add_parser("record-deployment", help="record that a version was deployed")
    record.add_argument("--pacticipant", required=True)
    record.add_argument("--version", required=True)
    record.add_argument("--environment", required=True)

    deploy = commands.add_parser("can-i-deploy", help="ask whether a version is safe to deploy")
    deploy.add_argument("--pacticipant", required=True)
    deploy.add_argument("--version", required=True)
    deploy.add_argument("--environment", required=True)
    deploy.add_argument("--retries", type=int, default=0)
    deploy.add_argument("--retry-interval", type=float, default=10.0)

    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        with BrokerClient.from_settings(settings) as broker:
            if args.command == "publish":
                files = args.files or sorted(settings.pact_dir.glob("*.json"))
                notices = broker.publish(
                    files, version=args.version, branch=args.branch, tags=args.tag, build_url=args.build_url
                )
                for notice in notices:
                    print(f"[{notice.get('type', 'info')}] {notice.get('text', '')}")
                print(f"Published {len(files)} pact file(s) as version {args.version}.")
                return 0
            if args.command == "record-deployment":
                broker.record_deployment(args.pacticipant, args.version, environment=args.environment)
                print(f"Recorded {args.pacticipant}@{args.version} as deployed to {args.environment}.")
                return 0
            result = broker.can_i_deploy(
                args.pacticipant,
                args.version,
                environment=args.environment,
                retries=args.retries,
                retry_interval_seconds=args.retry_interval,
            )
            print(f"can-i-deploy {args.pacticipant}@{args.version} -> {args.environment}: {result.reason}")
            return 0 if result.deployable else 1
    except (BrokerError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
