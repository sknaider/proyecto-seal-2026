// Sala exclusiva «ADA Claude» en la barra de Studio — helper puro, sin JSX, sin red.
// Correr: node --experimental-strip-types --test tests/claudeRoom.test.mjs  (desde seal-studio/frontend)
import test from "node:test";
import assert from "node:assert/strict";
import { parseBodyRoom, findBodyRoom, topicsWithoutBodyRooms } from "../src/app/v2/claudeRoom.ts";

const topics = [
  { channel: "topic:gtl", slug: "gtl" },
  { channel: "user:1:ada-claude", slug: "ada-claude" },
  { channel: "user:3:gtl-sistemas", slug: "gtl-sistemas" },
  { channel: "user:1:ada", slug: "ada" },
];

test("unit: la sala de cuerpo se parsea con agente, cuerpo y etiqueta", () => {
  assert.deepEqual(parseBodyRoom("user:1:ada-claude"), { channel: "user:1:ada-claude", agent: "ADA", body: "claude", label: "ADA Claude" });
  assert.equal(parseBodyRoom("user:2:jarvis-codex")?.label, "JARVIS Codex");
});

test("positivo: findBodyRoom encuentra la sala exclusiva de ADA Claude", () => {
  assert.equal(findBodyRoom(topics)?.channel, "user:1:ada-claude");
  assert.equal(findBodyRoom(topics, "ada", "CLAUDE")?.label, "ADA Claude");
});

test("negativo: una sala de proyecto o una sala sin cuerpo NO es sala de cuerpo", () => {
  assert.equal(parseBodyRoom("user:3:gtl-sistemas"), null);   // GTL no es un agente
  assert.equal(parseBodyRoom("user:1:ada"), null);            // sin cuerpo
  assert.equal(parseBodyRoom("dm:ada:william"), null);
  assert.equal(parseBodyRoom("web_chat"), null);
  assert.equal(findBodyRoom([{ channel: "user:3:gtl-sistemas", slug: "gtl-sistemas" }]), null);
  assert.equal(findBodyRoom(topics, "JARVIS", "claude"), null);
});

test("control: la lista de temas pierde SOLO la sala de cuerpo, y lo demás queda intacto y en orden", () => {
  const rest = topicsWithoutBodyRooms(topics);
  assert.deepEqual(rest.map(t => t.channel), ["topic:gtl", "user:3:gtl-sistemas", "user:1:ada"]);
  assert.deepEqual(topicsWithoutBodyRooms([]), []);
  assert.deepEqual(topicsWithoutBodyRooms(undefined), []);
});
