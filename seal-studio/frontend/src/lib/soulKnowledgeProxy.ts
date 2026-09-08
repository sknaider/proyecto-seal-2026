import { createHash } from "node:crypto";

// Server-side adapter for the surviving SUIE API contract. No route is installed here.
type Configuration = {
  origin: string;
  ingestionUrl: string;
  ingestToken: string;
  reviewToken: string;
  fetcher?: typeof fetch;
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid_payload");
  return value as Record<string, unknown>;
}

function only(body: Record<string, unknown>, keys: string[]) {
  if (Object.keys(body).some(key => !keys.includes(key))) throw new Error("unexpected_payload_field");
}

export function createSoulKnowledgeProxy(config: Configuration) {
  if (typeof window !== "undefined") throw new Error("server_runtime_required");
  const target = new URL(config.ingestionUrl);
  if (target.protocol !== "http:" || target.hostname !== "127.0.0.1" ||
      target.username || target.password || target.search || target.hash || target.pathname !== "/") {
    throw new Error("loopback_ingestion_origin_required");
  }
  if (new URL(config.origin).origin !== config.origin || !config.ingestToken || !config.reviewToken) {
    throw new Error("server_configuration_required");
  }
  const fetcher = config.fetcher ?? fetch;

  async function authorize(request: Request): Promise<string> {
    if (request.method !== "POST" || request.headers.get("origin") !== config.origin) {
      throw new Error("origin_or_method_not_allowed");
    }
    const headers = new Headers();
    const bearer = request.headers.get("authorization");
    const cookie = request.headers.get("cookie")?.split(";").map(part => part.trim())
      .find(part => part.startsWith("seal_token="));
    let session: string;
    if (bearer?.startsWith("Bearer ") && bearer.slice(7).trim().length > 0) {
      headers.set("authorization", bearer);
      session = bearer;
    } else if (cookie && cookie.length > "seal_token=".length) {
      headers.set("cookie", cookie);
      session = cookie;
    } else {
      throw new Error("authentication_required");
    }
    const response = await fetcher("http://127.0.0.1:8765/api/auth/me", {
      headers, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("authentication_required");
    const identity = await response.json();
    if (identity.ok !== true || identity.user?.id !== 1) throw new Error("william_required");
    return createHash("sha256").update(session).digest("hex");
  }

  async function post(path: string, body: object, review: boolean) {
    const headers = new Headers({ "Content-Type": "application/json" });
    if (review) headers.set("X-SUIE-Review-Token", config.reviewToken);
    else headers.set("Authorization", `Bearer ${config.ingestToken}`);
    const upstream = await fetcher(target.origin + path, {
      method: "POST", headers, body: JSON.stringify(body), cache: "no-store",
      redirect: "error", signal: AbortSignal.timeout(30000),
    });
    // Capability-bearing upstream errors never pass through to the browser.
    if (!upstream.ok) throw new Error(`suie_request_failed_${upstream.status}`);
    return upstream.json();
  }

  return {
    async ingestText(request: Request, input: unknown) {
      await authorize(request);
      const body = object(input);
      only(body, ["text", "title", "language", "profile_id", "propose_candidates"]);
      if (typeof body.text !== "string" || !body.text.trim()) throw new Error("text_required");
      return post("/v1/ingest/text", {
        ...body, owner_agent: "ADA", scope: "william", sensitivity: "confidential",
      }, false);
    },
    async reviewCandidate(request: Request, candidateId: string, input: unknown) {
      const session = await authorize(request);
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(candidateId)) {
        throw new Error("candidate_uuid_required");
      }
      const body = object(input);
      only(body, ["decision", "reason"]);
      if (!["approved", "rejected", "revoked"].includes(String(body.decision)) ||
          typeof body.reason !== "string" || body.reason.length < 3 || body.reason.length > 1000) {
        throw new Error("invalid_review");
      }
      return post(`/v1/review/candidates/${candidateId}/decision`, {
        ...body, actor: "William", actor_session_id: session,
      }, true);
    },
  };
}
