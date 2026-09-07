import { NextRequest, NextResponse } from "next/server";
import { beginConnection, connection, disconnect, finishConnection, gmailConfig, inbox, withGmailLock } from "@/lib/gmail-server";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const cookieName = "seal_gmail_oauth_state";
const cookieOptions = { httpOnly: true, secure: true, sameSite: "lax" as const, path: "/api/integrations/gmail", maxAge: 600 };
function json(body: object, status = 200) { return NextResponse.json(body, { status, headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } }); }
async function session(req: NextRequest) {
  const headers = new Headers();
  const token = req.cookies.get("seal_token")?.value, authorization = req.headers.get("authorization");
  if (token && !/[;\r\n]/.test(token)) headers.set("cookie", "seal_token=" + token);
  if (authorization) headers.set("authorization", authorization);
  if (!headers.has("cookie") && !headers.has("authorization")) return null;
  try {
    const response = await fetch("http://127.0.0.1:8765/api/auth/me", { headers, cache: "no-store", signal: AbortSignal.timeout(5000) });
    if (!response.ok) return null;
    const data = await response.json(); const user = data.ok === true ? data.user : null;
    return Number.isSafeInteger(user?.id) && user.id > 0 ? user : null;
  } catch { return null; }
}
export async function GET(req: NextRequest, { params }: { params: Promise<{ action: string }> }) {
  const user = await session(req);
  if (!user) return json({ ok: false, error: "authentication_required" }, 401);
  const { action } = await params, config = gmailConfig();
  if (!config) return action === "status" ? json({ ok: true, configured: false, connected: false }) : json({ ok: false, error: "Gmail necesita configuración OAuth web y HTTPS." }, 503);
  return withGmailLock(user.id, async () => {
  try {
    if (action === "status") return json({ ok: true, configured: true, ...await connection(config, user.id) });
    if (action === "inbox") return json({ ok: true, ...await inbox(config, user.id) });
    if (action === "callback") {
      const code = req.nextUrl.searchParams.get("code"), state = req.nextUrl.searchParams.get("state"), cookie = req.cookies.get(cookieName)?.value;
      if (!code || !state || !cookie || code.length > 4096 || state.length > 128) return json({ ok: false, error: "Autorización cancelada o vencida. Volvé a Studio para conectar." }, 400);
      await finishConnection(config, user.id, code, state, cookie);
      const response = NextResponse.redirect(config.origin + "/v2?gmail=connected", 303);
      response.cookies.set(cookieName, "", { ...cookieOptions, maxAge: 0 });
      response.headers.set("Cache-Control", "no-store"); response.headers.set("Referrer-Policy", "no-referrer");
      return response;
    }
    return json({ ok: false, error: "not_found" }, 404);
  } catch { return json({ ok: false, error: "No se pudo completar la conexión Gmail. Volvé a conectar desde Studio; no se enviaron correos." }, 502); }
  });
}
export async function POST(req: NextRequest, { params }: { params: Promise<{ action: string }> }) {
  const user = await session(req);
  if (!user) return json({ ok: false, error: "authentication_required" }, 401);
  const config = gmailConfig();
  if (!config) return json({ ok: false, error: "Gmail necesita configuración OAuth web y HTTPS." }, 503);
  // Never derive callback destinations from a forwarded Host header.
  if (req.headers.get("origin") !== config.origin) return json({ ok: false, error: "origin_not_allowed" }, 403);
  const { action } = await params;
  return withGmailLock(user.id, async () => {
  try {
    if (action === "connect") {
      const result = await beginConnection(config, user.id), response = json({ ok: true, url: result.url });
      response.cookies.set(cookieName, result.state, cookieOptions); return response;
    }
    if (action === "disconnect") return json({ ok: true, ...await disconnect(config, user.id) });
    return json({ ok: false, error: "not_found" }, 404);
  } catch { return json({ ok: false, error: "No se pudo completar la operación Gmail. Revisá la conexión e intentá nuevamente." }, 502); }
  });
}
