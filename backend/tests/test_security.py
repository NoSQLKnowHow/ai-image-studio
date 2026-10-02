"""HTTP hardening: client header on mutations, no CORS, security headers, Host allow-list."""

from __future__ import annotations

from conftest import create_run


def test_mutations_need_the_client_header(client):
    for method, url in (("post", "/api/runs"), ("delete", "/api/runs/" + "a" * 32)):
        response = client.request(method.upper(), url, json={"prompt": "x"}, headers={"X-Studio-Client": ""})
        assert response.status_code == 403 and response.json()["code"] == "missing_client_header"
    assert client.get("/api/runs", headers={"X-Studio-Client": ""}).status_code == 200  # reads are fine


def test_cors_preflight_is_refused(client):
    response = client.options("/api/runs", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-studio-client, content-type", "X-Studio-Client": "",
    })
    assert response.status_code == 403
    assert not any(h.lower().startswith("access-control-") for h in response.headers)


def test_security_headers_on_every_response(client):
    run = create_run(client)
    for url in ("/", "/api/health", f"/api/runs/{run['id']}", "/api/runs/" + "f" * 32):
        headers = client.get(url).headers
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "access-control-allow-origin" not in headers


def test_host_allow_list(client_factory):
    client = client_factory(allowed_hosts=("spark.lan",))
    rejected = client.get("/api/health")  # TestClient sends Host: testserver
    assert rejected.status_code == 400 and rejected.json()["code"] == "host_not_allowed"
    assert client.get("/api/health", headers={"Host": "spark.lan:8080"}).status_code == 200
    # the machine itself (container healthcheck, curl on the Spark) always gets in...
    for host in ("localhost", "127.0.0.1:8080", "[::1]:8080"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 200, host
    # ...but only the exact loopback names
    for host in ("localhost.evil.example", "127.0.0.1.nip.io", "evil.example"):
        assert client.get("/api/health", headers={"Host": host}).status_code == 400, host
