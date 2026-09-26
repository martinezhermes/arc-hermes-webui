"""Exercise the shipped request/permission behavior in Node without a browser build."""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_messaging_concurrent_reads_and_navigation_cleanup():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", str(root / "tests/fixtures/messaging_dom_probe.js")],
        cwd=root, text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_messaging_requests_and_native_permission_boundaries():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "-e", r"""
const assert = require('node:assert/strict');
global.addEventListener = () => {};
global.t = key => key;
global.S = {activeProfile: 'work'};
const seen = [];
global.api = async (url, options) => { seen.push({url, options}); return {outcome: 'submitted'}; };
const {allowed, request, errorDetails, submit} = require('./static/messaging.js');
async function run() {
  assert.equal(allowed(null, 'messages.send', 'room'), false);
  assert.equal(allowed({enabled:false, administrator:true}, 'messages.send', 'room'), false);
  const principal = {enabled:true, operations:['messages.recent'], rooms:['room']};
  assert.equal(allowed(principal, 'messages.recent', 'room'), true);
  assert.equal(allowed(principal, 'messages.recent', 'different'), false);
  assert.equal(allowed(principal, 'messages.send', 'room'), false);
  const ctx = {profile:'work', controller:new AbortController()};
  await request(ctx, '/api/arc/whatsapp/operations/messages.send', {roomId:'room', body:'hello'});
  assert.equal(seen.length, 1);
  assert.match(seen[0].url, /profile=work$/);
  assert.equal(seen[0].options.retries, 0);
  assert.equal(seen[0].options.method, 'POST');
  global.api = async () => ({outcome:'unknown'});
  await assert.rejects(submit(ctx, '/api/arc/whatsapp/operations/messages.send', {}), error => error.uncertain === true);
  S.activeProfile = 'personal';
  await assert.rejects(request(ctx, '/api/messaging/connections'));
  assert.equal(seen.length, 1);
  const details = errorDetails({body:JSON.stringify({detail:{message:'Denied', uncertain:true}})});
  assert.deepEqual(details, {message:'Denied', uncertain:true});
}
run().catch(error => { console.error(error); process.exitCode = 1; });
"""], cwd=root, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
