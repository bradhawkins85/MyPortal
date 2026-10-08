import json
from pathlib import Path
import shutil
import subprocess

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.maintenance import MaintenanceMiddleware


def _client(monkeypatch, state):
    monkeypatch.setattr("app.security.maintenance.get_public_upgrade_state", lambda: state)
    app = FastAPI()
    page = Path(__file__).parents[1] / "app" / "static" / "upgrade.html"
    app.add_middleware(MaintenanceMiddleware, page_path=str(page))
    @app.get("/page")
    def page_route(): return {"ok": True}
    @app.post("/api/jobs")
    def job_route(): return {"created": True}
    @app.get("/upgrade-status")
    def status_route(): return state
    return TestClient(app)


def test_normal_traffic_continues(monkeypatch):
    with _client(monkeypatch, {"maintenance": False}) as client:
        assert client.get("/page").status_code == 200
        assert client.post("/api/jobs").status_code == 200


def test_maintenance_returns_page_and_structured_api_503(monkeypatch):
    state = {"maintenance": True, "phase": "draining", "message": "Draining", "upgrade_id": "up-3"}
    with _client(monkeypatch, state) as client:
        html = client.get("/page", headers={"Accept": "text/html"})
        assert html.status_code == 503
        assert html.headers["retry-after"] == "15"
        assert "Upgrade In Progress" in html.text
        api = client.post("/api/jobs", json={})
        assert api.status_code == 503
        assert api.headers["retry-after"] == "15"
        assert api.json()["error"] == "upgrade_in_progress"
        assert client.get("/upgrade-status").status_code == 200


def test_upgrade_page_preserves_return_url_and_has_bounded_backoff():
    page = (Path(__file__).parents[1] / "app" / "static" / "upgrade.html").read_text()
    assert ";(() => {" in page
    assert "location.pathname+location.search+location.hash" in page.replace(" ", "")
    assert "location.replace(original)" in page
    assert "Math.min(30000" in page
    assert "attempt += 1;" in page
    assert '<img src="/static/favicon.svg" alt="" width="64" height="64">' in page
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in page


def test_nginx_preserves_page_timeouts_instead_of_showing_upgrade():
    root = Path(__file__).parents[1]
    for name in ("myportal.conf", "myportal-bluegreen.conf"):
        config = (root / "deploy" / "nginx" / name).read_text()
        assert "error_page 502 503 =503 /upgrade.html;" in config
        assert "error_page 502 503 504 =503 /upgrade.html;" not in config


def test_upgrade_page_returns_when_maintenance_ends_for_any_phase():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to execute upgrade page polling")
    page = (Path(__file__).parents[1] / "app" / "static" / "upgrade.html").read_text()
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const script = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  for (const phase of ['idle', 'succeeded', 'failed', 'rolled_back', 'interrupted', 'verifying']) {
    for (const maintenance of [false, true, undefined]) {
      const storage = new Map();
      let returnedTo;
      let retries = 0;
      const original = '/admin/knowledge-base?filter=draft#articles';
      vm.runInNewContext(script, {
        sessionStorage: {
          getItem: key => storage.get(key),
          setItem: (key, value) => storage.set(key, value),
          removeItem: key => storage.delete(key),
        },
        location: {
          pathname: '/admin/knowledge-base', search: '?filter=draft', hash: '#articles',
          replace: url => { returnedTo = url; },
        },
        document: { getElementById: () => ({ textContent: '', addEventListener: () => {} }) },
        fetch: async () => ({ ok: true, json: async () => ({ phase, maintenance }) }),
        clearTimeout: () => {},
        setTimeout: () => { retries += 1; },
      });
      await new Promise(resolve => setImmediate(resolve));
      if (maintenance === false) {
        assert.equal(returnedTo, original, phase);
        assert.equal(storage.size, 0);
        assert.equal(retries, 0);
      } else {
        assert.equal(returnedTo, undefined, phase);
        assert.equal(retries, 1);
      }
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", harness], input=json.dumps(script), capture_output=True,
        text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
