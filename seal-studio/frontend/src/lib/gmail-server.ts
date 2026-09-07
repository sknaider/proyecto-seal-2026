// Server-only module. Never import from a client component.
import { createCipheriv, createDecipheriv, randomBytes, timingSafeEqual } from "node:crypto";
import { mkdir, readFile, writeFile, rename, unlink } from "node:fs/promises";
import path from "node:path";
import { OAuth2Client, type Credentials } from "google-auth-library";

export const GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.metadata";
export type GmailConfig = { clientId: string; clientSecret: string; origin: string; key: Buffer; directory: string };
type Stored = { uid: number; email: string; credentials: Credentials };
type Pending = { uid: number; state: string; verifier: string; expires: number };
// The deployed Studio has one Node writer. Serialize same-account mutations,
// especially disconnect versus a refresh that would otherwise restore tokens.
const locks = new Map<number, Promise<void>>();
export async function withGmailLock<T>(uid: number, operation: () => Promise<T>): Promise<T> {
  const previous = locks.get(uid) || Promise.resolve();
  let release!: () => void;
  const next = new Promise<void>(resolve => { release = resolve; });
  locks.set(uid, next);
  await previous;
  try { return await operation(); }
  finally { release(); if (locks.get(uid) === next) locks.delete(uid); }
}
export function gmailConfig(env = process.env): GmailConfig | null {
  const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, STUDIO_PUBLIC_URL, STUDIO_INTEGRATION_KEY, STUDIO_INTEGRATION_DIR } = env;
  if (!GOOGLE_CLIENT_ID || !GOOGLE_CLIENT_SECRET || !STUDIO_PUBLIC_URL || !STUDIO_INTEGRATION_KEY || !STUDIO_INTEGRATION_DIR) return null;
  if (!/^[0-9a-fA-F]{64}$/.test(STUDIO_INTEGRATION_KEY) || !path.isAbsolute(STUDIO_INTEGRATION_DIR)) return null;
  try {
    const origin = new URL(STUDIO_PUBLIC_URL);
    if (origin.protocol !== "https:" || origin.username || origin.password || origin.pathname !== "/" || origin.search || origin.hash) return null;
    return { clientId: GOOGLE_CLIENT_ID, clientSecret: GOOGLE_CLIENT_SECRET, origin: origin.origin, key: Buffer.from(STUDIO_INTEGRATION_KEY, "hex"), directory: STUDIO_INTEGRATION_DIR };
  } catch { return null; }
}
function owner(uid: number) { if (!Number.isSafeInteger(uid) || uid < 1) throw new Error("invalid_session"); return String(uid); }
export function seal(value: unknown, config: GmailConfig, purpose: string): string {
  const iv = randomBytes(12), cipher = createCipheriv("aes-256-gcm", config.key, iv);
  cipher.setAAD(Buffer.from(purpose));
  const encrypted = Buffer.concat([cipher.update(JSON.stringify(value), "utf8"), cipher.final()]);
  return Buffer.concat([iv, cipher.getAuthTag(), encrypted]).toString("base64url");
}
export function unseal<T>(value: string, config: GmailConfig, purpose: string): T {
  const bytes = Buffer.from(value, "base64url");
  if (bytes.length < 29) throw new Error("invalid_encrypted_record");
  const decipher = createDecipheriv("aes-256-gcm", config.key, bytes.subarray(0,12));
  decipher.setAAD(Buffer.from(purpose)); decipher.setAuthTag(bytes.subarray(12,28));
  return JSON.parse(Buffer.concat([decipher.update(bytes.subarray(28)), decipher.final()]).toString("utf8"));
}
async function read<T>(config: GmailConfig, kind: string, uid: number): Promise<T | null> {
  const name = `${kind}-${owner(uid)}`;
  try { return unseal<T>(await readFile(path.join(config.directory, name + ".enc"), "utf8"), config, name); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return null; throw new Error("integration_storage_unavailable"); }
}
async function write(config: GmailConfig, kind: string, uid: number, value: unknown) {
  const name = `${kind}-${owner(uid)}`;
  await mkdir(config.directory, { recursive: true, mode: 0o700 });
  const target = path.join(config.directory, name + ".enc"), temp = target + "." + randomBytes(12).toString("hex");
  await writeFile(temp, seal(value, config, name), { mode: 0o600, flag: "wx" });
  await rename(temp, target);
}
function oauth(config: GmailConfig) { return new OAuth2Client({ clientId: config.clientId, clientSecret: config.clientSecret, redirectUri: config.origin + "/api/integrations/gmail/callback", transporterOptions: { timeout: 10000, retry: false } }); }
export async function connection(config: GmailConfig, uid: number) {
  const stored = await read<Stored>(config, "gmail", uid);
  if (stored && stored.uid !== uid) throw new Error("invalid_owner");
  return { connected: !!stored, email: stored?.email };
}
export async function beginConnection(config: GmailConfig, uid: number) {
  const client = oauth(config), state = randomBytes(32).toString("base64url");
  const pkce = await client.generateCodeVerifierAsync();
  await write(config, "pending", uid, { uid, state, verifier: pkce.codeVerifier, expires: Date.now() + 600000 } satisfies Pending);
  const url = client.generateAuthUrl({ access_type: "offline", prompt: "consent select_account", scope: [GMAIL_SCOPE], state, code_challenge: pkce.codeChallenge, code_challenge_method: "S256" as import("google-auth-library").CodeChallengeMethod });
  return { url, state };
}
export function verifyState(pending: Pending | null, uid: number, state: string, cookie: string) {
  if (!pending || pending.uid !== uid || pending.expires < Date.now()) throw new Error("invalid_oauth_state");
  const expected = Buffer.from(pending.state), supplied = Buffer.from(state), browser = Buffer.from(cookie);
  if (expected.length !== supplied.length || expected.length !== browser.length || !timingSafeEqual(expected, supplied) || !timingSafeEqual(expected, browser)) throw new Error("invalid_oauth_state");
}
export async function finishConnection(config: GmailConfig, uid: number, code: string, state: string, cookie: string) {
  const pending = await read<Pending>(config, "pending", uid);
  verifyState(pending, uid, state, cookie);
  // Atomically claim this authorization attempt; a duplicate callback cannot consume it twice.
  const source = path.join(config.directory, `pending-${owner(uid)}.enc`), claimed = source + ".used-" + randomBytes(12).toString("hex");
  await rename(source, claimed);
  try {
    const claimedState = unseal<Pending>(await readFile(claimed, "utf8"), config, `pending-${uid}`);
    verifyState(claimedState, uid, state, cookie);
    const client = oauth(config);
    const { tokens } = await client.getToken({ code, codeVerifier: claimedState.verifier });
    if (!tokens.refresh_token || !tokens.scope?.split(" ").includes(GMAIL_SCOPE)) throw new Error("gmail_permission_not_granted");
    client.setCredentials(tokens);
    const profile = await client.request<{ emailAddress: string }>({ url: "https://gmail.googleapis.com/gmail/v1/users/me/profile", timeout: 10000 });
    await write(config, "gmail", uid, { uid, email: profile.data.emailAddress, credentials: client.credentials } satisfies Stored);
  } finally { await unlink(claimed); }
}
export async function inbox(config: GmailConfig, uid: number) {
  const stored = await read<Stored>(config, "gmail", uid);
  if (!stored || stored.uid !== uid) throw new Error("gmail_not_connected");
  const client = oauth(config); client.setCredentials(stored.credentials);
  const result = await client.request<{ messages?: { id: string }[] }>({ url: "https://gmail.googleapis.com/gmail/v1/users/me/messages", params: { labelIds: "INBOX", maxResults: 10 }, timeout: 10000 });
  const messages = [];
  // Bounded fetch: ten headers, never bodies or attachments, never a background poll.
  const ids = (result.data.messages || []).slice(0,10);
  for (let offset = 0; offset < ids.length; offset += 5) {
    const batch = await Promise.all(ids.slice(offset,offset+5).map(async msg => {
    const detail = await client.request<{ id: string; payload?: { headers?: { name: string; value: string }[] } }>({ url: "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + encodeURIComponent(msg.id), params: { format: "metadata", metadataHeaders: ["From", "Subject", "Date"] }, timeout: 10000 });
    const headers = detail.data.payload?.headers || [];
    const header = (name: string) => headers.find(h => h.name.toLowerCase() === name)?.value || "";
    return { id: detail.data.id, from: header("from"), subject: header("subject"), date: header("date") };
    }));
    messages.push(...batch);
  }
  await write(config, "gmail", uid, { ...stored, credentials: client.credentials });
  return { email: stored.email, messages };
}
export async function disconnect(config: GmailConfig, uid: number) {
  const stored = await read<Stored>(config, "gmail", uid);
  if (!stored) return;
  if (stored.uid !== uid) throw new Error("invalid_owner");
  const token = stored.credentials.refresh_token || stored.credentials.access_token;
  if (token) await oauth(config).revokeToken(token);
  await unlink(path.join(config.directory, `gmail-${owner(uid)}.enc`));
}
