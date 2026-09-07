import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

function canonicalize(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canonicalize).join(",")}]`;
  }
  if (typeof value === "object") {
    const members = Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalize(value[key])}`);
    return `{${members.join(",")}}`;
  }
  throw new Error(`unsupported type: ${typeof value}`);
}

const fixture = JSON.parse(readFileSync(process.argv[2], "utf8"));
const domain = Buffer.from(fixture.domain_separator_hex, "hex");
for (const vector of fixture.vectors) {
  const canonical = canonicalize(vector.value);
  if (canonical !== vector.canonical) {
    throw new Error(`${vector.name}: canonical mismatch`);
  }
  const digest = `sha256:${createHash("sha256")
    .update(domain)
    .update(Buffer.from(canonical, "utf8"))
    .digest("hex")}`;
  if (digest !== vector.domain_sha256) {
    throw new Error(`${vector.name}: digest mismatch`);
  }
}
console.log(`${fixture.vectors.length} SSAI JCS vectors verified by Node.js`);
