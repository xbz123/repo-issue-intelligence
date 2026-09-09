"""PR6 checks at the supported CLI, HTTP and authorization boundaries."""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from repo_issue_intelligence.api import app
from repo_issue_intelligence.cli import app as cli

TOKEN = "synthetic-test-token-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize(
    ("method", "path", "root_path", "expected"),
    [
        ("POST", "/v1/repository/index", "", True),
        ("POST", "/v1/repository/index/", "/service", True),
        ("POST", "/service/v1/repository/index/", "/service", True),
        ("POST", "/v1/agent/runs/abc/review/", "/service", True),
        ("POST", "/v1/agent/runs/abc/review/extra", "", False),
        ("POST", "/v1//agent/runs", "", False),
        ("POST", "/v1/issues/score", "", False),
        ("POST", "/v1/issues/rank", "", False),
        ("GET", "/v1/agent/runs/abc/review", "", False),
    ],
)
def test_work_slot_covers_only_state_writing_routes(method, path, root_path, expected):
    from repo_issue_intelligence.api_security import _needs_work_slot

    assert _needs_work_slot({"method": method, "path": path, "root_path": root_path}) is expected


@pytest.mark.parametrize("host", ["0.0.0.0", "example.com", "::", "127.0.0.1.example.com"])
def test_serve_refuses_non_loopback_before_starting_server(monkeypatch, host):
    import uvicorn

    def network_boundary(*args, **kwargs):
        raise AssertionError("Unsafe listener must not be started")

    monkeypatch.setattr(uvicorn, "run", network_boundary)
    result = CliRunner().invoke(cli, ["serve", "--host", host])
    assert result.exit_code == 2
    assert "loopback" in result.output


def test_http_token_is_required_and_revocation_is_current(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RII_API_TOKEN", raising=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/health").status_code == 200
        assert client.post("/v1/issues/rank", json={}).status_code == 401
        monkeypatch.setenv("RII_API_TOKEN", "synthetic-test-token-0123456789abcdef")
        assert (
            client.post(
                "/v1/issues/rank", json={}, headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        headers = {"Authorization": "Bearer synthetic-test-token-0123456789abcdef"}
        assert client.post("/v1/issues/rank", json={}, headers=headers).status_code == 422
        monkeypatch.setenv("RII_API_TOKEN", "rotated-test-token-0123456789abcdefg")
        assert client.post("/v1/issues/rank", json={}, headers=headers).status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Host": "127.0.0.1.evil.example"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Forwarded": "for=127.0.0.1;host=localhost"},
        {"X-Forwarded-User": "local-operator"},
    ],
)
def test_browser_and_proxy_headers_fail_closed(monkeypatch, tmp_path, headers):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/v1/issues/rank", json={}, headers={**AUTH, **headers})
        assert result.status_code in {400, 403}


def test_reviewer_identity_cannot_be_supplied_by_body(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "private" / "runs.sqlite3"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/v1/agent/runs/missing/review",
            json={
                "decision": "approved",
                "reviewer": "administrator",
            },
            headers=AUTH,
        )
        assert response.status_code == 422


def test_repository_allowlist_is_default_deny_and_scope_not_git_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.delenv("RII_API_ANALYSIS_ROOTS", raising=False)
    root = tmp_path / "repo"
    scope = root / "src"
    scope.mkdir(parents=True)
    (scope / "service.py").write_text("def refresh(): return 1\n")
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        assert client.post("/v1/repository/index", json={"path": str(scope)}).status_code == 403
        monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(scope)]))
        assert client.post("/v1/repository/index", json={"path": str(scope)}).status_code == 200
        for denied in (str(root), str(scope / ".."), str(tmp_path / "repo-extra")):
            assert client.post("/v1/repository/index", json={"path": denied}).status_code == 403
        (scope / "escape.py").symlink_to(tmp_path / "secret.py")
        assert client.post("/v1/repository/index", json={"path": str(scope)}).status_code == 403


def test_external_authorization_binds_principal_scope_provider_and_operation(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from repo_issue_intelligence.api_security import LocalPrincipal, authorize_repository_operation

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(tmp_path)]))
    monkeypatch.setenv("RII_API_OPERATIONS", json.dumps(["run", "retry", "recover-unknown"]))
    principal = LocalPrincipal("local-operator")
    with pytest.raises(HTTPException) as denied:
        authorize_repository_operation(principal, tmp_path, "retry", provider="opencode")
    assert denied.value.status_code == 403
    grant = {
        "principal": "local-operator",
        "analysis_root": str(tmp_path),
        "provider": "opencode",
        "operation": "retry",
    }
    monkeypatch.setenv("RII_API_EXTERNAL_GRANTS", json.dumps([grant]))
    assert (
        authorize_repository_operation(principal, tmp_path, "retry", provider="opencode")
        == tmp_path
    )
    for actor, scope, operation, provider in (
        (LocalPrincipal("forged"), tmp_path, "retry", "opencode"),
        (principal, tmp_path.parent, "retry", "opencode"),
        (principal, tmp_path, "recover-unknown", "opencode"),
        (principal, tmp_path, "retry", "another-provider"),
        (principal, tmp_path, "retry", None),
    ):
        with pytest.raises(HTTPException):
            authorize_repository_operation(actor, scope, operation, provider=provider)
    monkeypatch.setenv("RII_API_EXTERNAL_GRANTS", "[]")
    with pytest.raises(HTTPException):
        authorize_repository_operation(principal, tmp_path, "retry", provider="opencode")


@pytest.mark.parametrize("case", ["bytes", "issues", "text", "pagination", "source"])
def test_resource_limits_precede_large_work(monkeypatch, tmp_path, case):
    from test_api import issue_payload

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(tmp_path)]))
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        if case == "bytes":
            response = client.post("/v1/issues/rank", content=b" " * 1_048_577)
        elif case == "issues":
            response = client.post(
                "/v1/issues/rank", json={"issues": [issue_payload(i + 1) for i in range(101)]}
            )
        elif case == "text":
            response = client.post(
                "/v1/issues/score", json={**issue_payload(), "body": "a" * 100_001}
            )
        elif case == "pagination":
            response = client.post("/v1/issues/rank?limit=101", json={"issues": [issue_payload()]})
        else:
            (tmp_path / "large.py").write_text("x" * 2_000_001)
            response = client.post("/v1/repository/index", json={"path": str(tmp_path)})
        assert response.status_code == 413


def test_concurrent_run_is_refused_and_capacity_is_released(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Event

    from test_api import issue_payload

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "service.py"
    source.write_text("def refresh(): return 1\n")
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "private" / "runs.sqlite3"))
    entered, release = Event(), Event()
    read_text = Path.read_text

    def slow_source(path, *args, **kwargs):
        if path == source and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", slow_source)
    payload = {"issues": [issue_payload()], "repository_path": str(root)}
    with (
        TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client,
        ThreadPoolExecutor(1) as workers,
    ):
        first = workers.submit(client.post, "/v1/agent/runs", json=payload)
        try:
            assert entered.wait(5)
            assert client.get("/health").status_code == 200
            second = client.post("/v1/agent/runs", json=payload)
            assert client.post("/v1/issues/score", json=issue_payload()).status_code == 200
            assert (
                client.post("/v1/issues/rank", json={"issues": [issue_payload()]}).status_code
                == 200
            )
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 201
        assert second.status_code == 503
        assert client.post("/v1/agent/runs", json=payload).status_code == 201


def test_concurrent_repository_index_is_refused_and_capacity_is_released(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from threading import Event

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "service.py"
    source.write_text("def refresh(): return 1\n")
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    entered, release = Event(), Event()
    read_text = Path.read_text

    def slow_source(path, *args, **kwargs):
        if path == source and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", slow_source)
    payload = {"path": str(root)}
    with (
        TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client,
        ThreadPoolExecutor(1) as workers,
    ):
        first = workers.submit(client.post, "/v1/repository/index", json=payload)
        try:
            assert entered.wait(5)
            second = client.post("/v1/repository/index", json=payload)
            assert second.status_code == 503
            from test_api import issue_payload

            assert client.post("/v1/issues/score", json=issue_payload()).status_code == 200
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200
        assert client.post("/v1/repository/index", json=payload).status_code == 200


def test_http_validation_does_not_echo_secret_bearing_input(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "private" / "runs.sqlite3"))
    canary = "SYNTHETIC-CREDENTIAL-CANARY"
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post(
            "/v1/agent/runs/missing/review",
            json={
                "decision": "approved",
                "reviewer": canary,
                "base_url": f"https://user:{canary}@provider.invalid/v1",
            },
        )
        assert response.status_code == 422
        assert canary not in response.text


def test_http_database_is_private_and_unsafe_existing_file_is_refused(monkeypatch, tmp_path):
    import os
    import stat

    if os.name != "posix":
        pytest.skip("owner/mode policy requires POSIX")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    database = tmp_path / "private" / "runs.sqlite3"
    monkeypatch.setenv("AGENT_DB_PATH", str(database))
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        assert client.get("/v1/agent/runs/missing").status_code == 404
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
        database.chmod(0o644)
        assert client.get("/v1/agent/runs/missing").status_code == 503
        assert stat.S_IMODE(database.stat().st_mode) == 0o644


def test_http_execution_errors_are_not_persisted_as_raw_secrets(monkeypatch, tmp_path):
    from pathlib import Path

    from test_api import issue_payload

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "service.py"
    source.write_text("def refresh(): return 1\n")
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    database = tmp_path / "private" / "runs.sqlite3"
    monkeypatch.setenv("AGENT_DB_PATH", str(database))
    read_text = Path.read_text
    canary = "SYNTHETIC-ERROR-CREDENTIAL"

    def broken_source(path, *args, **kwargs):
        if path == source:
            raise RuntimeError(f"https://user:{canary}@provider.invalid")
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", broken_source)
    with TestClient(
        app, base_url="http://127.0.0.1", headers=AUTH, raise_server_exceptions=False
    ) as client:
        payload = {"issues": [issue_payload()], "repository_path": str(root)}
        response = client.post("/v1/agent/runs", json=payload)
        assert response.status_code == 500
        assert canary not in response.text
        leaked = canary.encode() in database.read_bytes()
        assert not leaked
        monkeypatch.setattr(Path, "read_text", read_text)
        assert client.post("/v1/agent/runs", json=payload).status_code == 201


def test_real_loopback_serve_authenticates_and_records_local_identity(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path

    import httpx
    from test_api import issue_payload

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        RII_API_TOKEN=TOKEN,
        WEB_CONCURRENCY="2",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "repo_issue_intelligence.cli", "serve", "--port", str(port)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2
        ) as client:
            deadline = time.monotonic() + 15
            while True:
                try:
                    health = client.get("/health")
                    break
                except httpx.ConnectError:
                    if time.monotonic() >= deadline or process.poll() is not None:
                        raise AssertionError("Loopback server failed to start") from None
                    time.sleep(0.05)
            assert health.status_code == 200
            assert client.get("/health", params={"token": TOKEN}).status_code == 200
            assert client.post("/v1/issues/rank", json={}).status_code == 401
            assert (
                client.post(
                    "/v1/issues/rank", json={"issues": [issue_payload()]}, headers=AUTH
                ).status_code
                == 200
            )
            assert client.get("/health", headers={"Forwarded": "for=127.0.0.1"}).status_code == 403
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
    assert "Local execution identity:" in output
    assert "Started parent process" not in output
    assert TOKEN not in output


def test_weak_token_and_credential_bearing_issue_url_are_refused(monkeypatch, tmp_path):
    from test_api import issue_payload

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", "short")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/v1/issues/score", json=issue_payload(), headers={"Authorization": "Bearer short"}
        )
        assert response.status_code == 401
        monkeypatch.setenv("RII_API_TOKEN", TOKEN)
        for url in (
            "https://user:SYNTHETIC-CANARY@host.invalid/issue/1",
            "https://host.invalid/issue/1?token=SYNTHETIC-CANARY",
        ):
            response = client.post(
                "/v1/issues/score", json={**issue_payload(), "html_url": url}, headers=AUTH
            )
            assert response.status_code == 422
            assert "SYNTHETIC-CANARY" not in response.text


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/v1/issues/score", {}),
        ("POST", "/v1/issues/rank", {}),
        ("POST", "/v1/repository/index", {}),
        ("POST", "/v1/agent/runs", {}),
        ("GET", "/v1/agent/runs/missing", None),
        ("POST", "/v1/agent/runs/missing/review", {}),
        ("GET", "/openapi.json", None),
        ("GET", "/v2/agent/runs/missing", None),
    ],
)
def test_every_sensitive_route_rejects_cookie_or_forged_identity(
    monkeypatch, tmp_path, method, path, payload
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    with TestClient(app, base_url="http://127.0.0.1", cookies={"token": TOKEN}) as client:
        response = client.request(method, path, json=payload, headers={"X-User": "local-operator"})
        assert response.status_code == 401
        assert not (tmp_path / "data").exists()


def test_run_read_review_and_scope_revocation_share_authorization(monkeypatch, tmp_path):
    from test_api import issue_payload

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text("def refresh(): return 1\n")
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "private" / "runs.sqlite3"))
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        created = client.post(
            "/v1/agent/runs", json={"issues": [issue_payload()], "repository_path": str(root)}
        )
        assert created.status_code == 201
        url = f"/v1/agent/runs/{created.json()['run_id']}"
        assert client.get(url).status_code == 200
        monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", "[]")
        assert client.get(url).status_code == 403
        assert client.post(url + "/review", json={"decision": "approved"}).status_code == 403
        monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
        assert client.get(url).json()["status"] == "awaiting_review"
        assert client.post(url + "/review", json={"decision": "approved"}).status_code == 200


def test_duplicate_headers_and_chunked_body_cannot_bypass_limits(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                "/v1/issues/rank", json={}, headers=[("Authorization", AUTH["Authorization"])] * 2
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/health", headers=[("Host", "127.0.0.1"), ("Host", "evil.example")]
            ).status_code
            == 403
        )
        response = client.post(
            "/v1/issues/rank", content=iter([b" " * 600_000, b" " * 600_000]), headers=AUTH
        )
        assert response.status_code == 413


def test_retained_scope_authorization_does_not_need_current_checkout(monkeypatch, tmp_path):
    from repo_issue_intelligence.api_security import LocalPrincipal, authorize_repository_operation

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "original"
    root.mkdir()
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("RII_API_OPERATIONS", json.dumps(["read", "retry"]))
    monkeypatch.setenv(
        "RII_API_EXTERNAL_GRANTS",
        json.dumps(
            [
                {
                    "principal": "local-operator",
                    "analysis_root": str(root),
                    "provider": "opencode",
                    "operation": "retry",
                }
            ]
        ),
    )
    root.rename(tmp_path / "moved")
    assert authorize_repository_operation(LocalPrincipal("local-operator"), root, "read") == root
    assert (
        authorize_repository_operation(
            LocalPrincipal("local-operator"), root, "retry", provider="opencode"
        )
        == root
    )


@pytest.mark.parametrize(
    "issue_url",
    [
        None,
        "https://example.com/issues/1",
        "https://user:LEGACY-URL-CANARY@provider.invalid/issues/1",
        "https://provider.invalid/issues/1?token=LEGACY-URL-CANARY",
        "https://provider.invalid/issues/1%253Ftoken%253DLEGACY-URL-CANARY",
    ],
)
def test_historical_error_is_redacted_in_http_without_rewriting_history(
    monkeypatch, tmp_path, issue_url
):
    from test_api import issue_payload

    from repo_issue_intelligence.agent_store import AgentStore

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text("def refresh(): return 1\n")
    database = tmp_path / "private" / "runs.sqlite3"
    monkeypatch.setenv("AGENT_DB_PATH", str(database))
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        created = client.post(
            "/v1/agent/runs", json={"issues": [issue_payload()], "repository_path": str(root)}
        )
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        store = AgentStore(database)
        historical = store.get_run(run_id)
        canary = "LEGACY-ERROR-CANARY"
        url_canary = "LEGACY-URL-CANARY"
        historical.error = f"RuntimeError: https://user:{canary}@provider.invalid"
        historical.traces[-1].error = historical.error
        historical.investigations[0].issue.html_url = issue_url
        store.save_run(historical)
        url = f"/v1/agent/runs/{run_id}"
        expected_url = None if issue_url and url_canary in issue_url else issue_url

        def check_response(response):
            assert response.status_code == 200
            assert canary not in response.text
            assert url_canary not in response.text
            assert response.json()["investigations"][0]["issue"]["html_url"] == expected_url

        check_response(client.get(url))
        assert store.get_run(run_id).error == historical.error
        # Successful historical/CLI results can leak URLs even without any raw error.
        historical.error = None
        historical.traces[-1].error = None
        store.save_run(historical)
        check_response(client.get(url))
        check_response(client.post(url + "/review", json={"decision": "approved"}))
        assert store.get_run(run_id).error is None
        assert store.get_run(run_id).investigations[0].issue.html_url == (
            historical.investigations[0].issue.html_url
        )


def test_unreadable_source_walk_is_not_silently_accepted(monkeypatch, tmp_path):
    import os

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(tmp_path)]))
    subtree = tmp_path / "unreadable"
    subtree.mkdir()
    scandir = os.scandir

    def deny_subtree(path):
        if str(path) == str(subtree):
            raise PermissionError("synthetic unreadable subtree")
        return scandir(path)

    monkeypatch.setattr(os, "scandir", deny_subtree)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        assert client.post("/v1/repository/index", json={"path": str(tmp_path)}).status_code == 403


@pytest.mark.parametrize("operation", ["index", "run"])
@pytest.mark.parametrize("assets", ["single", "aggregate"])
def test_large_non_source_assets_do_not_consume_source_budget(
    monkeypatch, tmp_path, operation, assets
):
    from test_api import issue_payload

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text("def refresh(): return 1\n")
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    monkeypatch.setenv("AGENT_DB_PATH", str(tmp_path / "private" / "runs.sqlite3"))
    count, size = (1, 33_000_000) if assets == "single" else (23, 1_500_000)
    for number in range(count):
        with (root / f"asset-{number}.png").open("wb") as asset:
            asset.truncate(size)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        url = "/v1/repository/index" if operation == "index" else "/v1/agent/runs"
        payload = (
            {"path": str(root)}
            if operation == "index"
            else {"repository_path": str(root), "issues": [issue_payload()]}
        )
        response = client.post(url, json=payload)
        assert response.status_code == (200 if operation == "index" else 201)
        if operation == "index":
            assert [item["path"] for item in response.json()["files"]] == ["service.py"]
        else:
            assert response.json()["status"] == "awaiting_review"
        (root / "linked-asset.png").symlink_to(tmp_path / "outside.png")
        assert client.post(url, json=payload).status_code == 403


@pytest.mark.parametrize("kind", ["upper_source", "schema", "aggregate_source"])
def test_indexed_source_bytes_remain_bounded(monkeypatch, tmp_path, kind):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(tmp_path)]))
    names = {
        "upper_source": ["large.PY"],
        "schema": ["large.SCHEMA.JSON"],
        "aggregate_source": [f"source-{number}.js" for number in range(17)],
    }[kind]
    for name in names:
        with (tmp_path / name).open("wb") as source:
            source.truncate(1_900_000 if kind == "aggregate_source" else 2_000_001)
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        response = client.post("/v1/repository/index", json={"path": str(tmp_path)})
        assert response.status_code == 413


@pytest.mark.parametrize("alias", ["/tmp", "/var"])
@pytest.mark.parametrize("existing", [False, True])
def test_system_database_aliases_support_create_read_and_review(
    monkeypatch, tmp_path, alias, existing
):
    import os
    import stat
    import tempfile
    from pathlib import Path

    from test_api import issue_payload

    if os.name != "posix":
        pytest.skip("private HTTP storage requires POSIX")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text("def refresh(): return 1\n")
    monkeypatch.setenv("RII_API_ANALYSIS_ROOTS", json.dumps([str(root)]))
    parent = "/tmp" if alias == "/tmp" else "/var/tmp"
    with tempfile.TemporaryDirectory(prefix="rii-pr71-alias-", dir=parent) as directory:
        database = Path(directory) / "private" / "runs.sqlite3"
        with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
            if existing:
                monkeypatch.setenv("AGENT_DB_PATH", str(database.resolve()))
                assert client.get("/v1/agent/runs/missing").status_code == 404
            monkeypatch.setenv("AGENT_DB_PATH", str(database))
            created = client.post(
                "/v1/agent/runs",
                json={
                    "repository_path": str(root),
                    "issues": [issue_payload()],
                },
            )
            assert created.status_code == 201
            url = f"/v1/agent/runs/{created.json()['run_id']}"
            assert client.get(url).status_code == 200
            assert client.post(url + "/review", json={"decision": "approved"}).status_code == 200
            assert stat.S_IMODE(database.stat().st_mode) == 0o600
            assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700
            database.chmod(0o644)
            assert client.get(url).status_code == 503
            assert stat.S_IMODE(database.stat().st_mode) == 0o644


@pytest.mark.parametrize("link", ["directory", "database", "sidecar"])
def test_user_database_links_are_still_refused(monkeypatch, tmp_path, link):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RII_API_TOKEN", TOKEN)
    database = tmp_path / "private" / "runs.sqlite3"
    monkeypatch.setenv("AGENT_DB_PATH", str(database))
    with TestClient(app, base_url="http://127.0.0.1", headers=AUTH) as client:
        assert client.get("/v1/agent/runs/missing").status_code == 404
        if link == "directory":
            alias = tmp_path / "directory-link"
            alias.symlink_to(database.parent, target_is_directory=True)
            monkeypatch.setenv("AGENT_DB_PATH", str(alias / database.name))
        elif link == "database":
            alias = database.with_name("database-link.sqlite3")
            alias.symlink_to(database)
            monkeypatch.setenv("AGENT_DB_PATH", str(alias))
        else:
            database.with_name(database.name + "-wal").symlink_to(tmp_path / "missing-wal")
        assert client.get("/v1/agent/runs/missing").status_code == 503
