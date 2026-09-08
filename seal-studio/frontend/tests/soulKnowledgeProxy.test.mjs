import test from "node:test";
import assert from "node:assert/strict";
import { createSoulKnowledgeProxy } from "../src/lib/soulKnowledgeProxy.ts";

const origin = "https://studio.example.test";
const request = (extra = {}) => new Request(origin + "/knowledge", {
  method: "POST", headers: { Origin: origin, Authorization: "Bearer synthetic-session", ...extra },
});
function setup(userId = 1) {
  const calls = [];
  const proxy = createSoulKnowledgeProxy({
    origin, ingestionUrl: "http://127.0.0.1:8790", ingestToken: "synthetic-ingest",
    reviewToken: "synthetic-review", fetcher: async (url, init) => {
      calls.push({ url, ...init });
      return Response.json(url.endsWith("api/auth/me") ? { ok: true, user: { id: userId } } : { ok: true });
    },
  });
  return { calls, proxy };
}
test("server_runtime_required blocks construction when window is defined", () => {
  global.window = {};
  try {
    assert.throws(() => createSoulKnowledgeProxy({
      origin: "https://studio.example.test", ingestionUrl: "http://127.0.0.1:8790",
      ingestToken: "t", reviewToken: "r",
    }), /server_runtime_required/);
  } finally {
    delete global.window;
  }
});
test("loopback_ingestion_origin_required rejects non-loopback ingestionUrl", () => {
  assert.throws(() => createSoulKnowledgeProxy({
    origin: "https://studio.example.test", ingestionUrl: "https://evil.example.com/ingest",
    ingestToken: "t", reviewToken: "r",
  }), /loopback_ingestion_origin_required/);
});
// Surgical per-guard tests so that removing any single guard kills one test (NEXUS S1-S6)
test("loopback guard: bad hostname alone is rejected (evil.example.com with http scheme)", () => {
  assert.throws(() => createSoulKnowledgeProxy({
    origin: "https://studio.example.test", ingestionUrl: "http://evil.example.com/",
    ingestToken: "t", reviewToken: "r",
  }), /loopback_ingestion_origin_required/);
});
test("loopback guard: https scheme alone is rejected even with 127.0.0.1", () => {
  assert.throws(() => createSoulKnowledgeProxy({
    origin: "https://studio.example.test", ingestionUrl: "https://127.0.0.1/",
    ingestToken: "t", reviewToken: "r",
  }), /loopback_ingestion_origin_required/);
});
test("loopback guard: credentials in URL are rejected", () => {
  assert.throws(() => createSoulKnowledgeProxy({
    origin: "https://studio.example.test", ingestionUrl: "http://user:pw@127.0.0.1/",
    ingestToken: "t", reviewToken: "r",
  }), /loopback_ingestion_origin_required/);
});
test("loopback guard: non-root pathname is rejected", () => {
  assert.throws(() => createSoulKnowledgeProxy({
    origin: "https://studio.example.test", ingestionUrl: "http://127.0.0.1/ingest",
    ingestToken: "t", reviewToken: "r",
  }), /loopback_ingestion_origin_required/);
});
test("origin_or_method_not_allowed: GET request is rejected even with valid auth", async () => {
  const { proxy } = setup();
  const getReq = new Request("https://studio.example.test/knowledge", {
    method: "GET", headers: { Origin: "https://studio.example.test", Authorization: "Bearer synthetic-session" },
  });
  await assert.rejects(proxy.ingestText(getReq, { text: "x" }), /origin_or_method_not_allowed/);
});
test("authentication_required: Bearer with only whitespace token is not accepted", async () => {
  const { proxy } = setup();
  // Regular space is trimmed by Request headers; use non-breaking space ( ) which survives
  // and was accepted by the old length>7 guard — the fix uses .trim() to reject it.
  const nbspBearer = new Request("https://studio.example.test/knowledge", {
    method: "POST", headers: { Origin: "https://studio.example.test", Authorization: "Bearer  " },
  });
  await assert.rejects(proxy.ingestText(nbspBearer, { text: "x" }), /authentication_required/);
});
test("authentication_required when request has no credentials", async () => {
  const { proxy } = setup();
  const bare = new Request("https://studio.example.test/knowledge", {
    method: "POST", headers: { Origin: "https://studio.example.test" },
  });
  await assert.rejects(proxy.ingestText(bare, { text: "x" }), /authentication_required/);
});
test("verified human identity supplies fixed ingestion authority", async () => {
  const { proxy, calls } = setup();
  await proxy.ingestText(request(), { text: "Texto sintético." });
  assert.deepEqual(JSON.parse(calls[1].body), {
    text: "Texto sintético.", owner_agent: "ADA", scope: "william", sensitivity: "confidential",
  });
  assert.equal(calls[1].headers.get("authorization"), "Bearer synthetic-ingest");
});
test("foreign origins and other users cannot invoke SUIE", async () => {
  const a = setup();
  await assert.rejects(a.proxy.ingestText(request({ Origin: "https://other.example.test" }), { text: "x" }));
  assert.equal(a.calls.length, 0);
  const b = setup(2);
  await assert.rejects(b.proxy.ingestText(request(), { text: "x" }));
  assert.equal(b.calls.length, 1);
});
test("client cannot replace owner or review actor", async () => {
  const { proxy, calls } = setup();
  await assert.rejects(proxy.ingestText(request(), { text: "x", owner_agent: "NEXUS" }));
  await assert.rejects(proxy.reviewCandidate(request(), "22222222-2222-2222-2222-222222222222", {
    decision: "approved", reason: "Revisado", actor: "William",
  }));
  assert.equal(calls.length, 2);
});
test("review uses distinct capability and hashes authenticated session", async () => {
  const { proxy, calls } = setup();
  await proxy.reviewCandidate(request(), "22222222-2222-2222-2222-222222222222", {
    decision: "approved", reason: "Revisado",
  });
  const body = JSON.parse(calls[1].body);
  assert.equal(body.actor, "William");
  assert.match(body.actor_session_id, /^[a-f0-9]{64}$/);
  assert.equal(calls[1].headers.get("X-SUIE-Review-Token"), "synthetic-review");
  assert.equal(calls[1].headers.has("Authorization"), false);
});
