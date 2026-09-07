// Keep the former Studio's localStorage sessions usable alongside HttpOnly cookies.
export function savedToken(): string | null {
  try { return window.localStorage.getItem("seal_token"); } catch { return null; }
}

export function saveToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem("seal_token", token);
    else window.localStorage.removeItem("seal_token");
  } catch { /* Cookies still work when browser storage is unavailable. */ }
}

export async function api(path: string, init: RequestInit = {}, timeoutMs = 12000) {
  const headers = new Headers(init.headers);
  const token = savedToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const controller = new AbortController();
  const cancel = () => controller.abort();
  const timeout = window.setTimeout(cancel, timeoutMs);
  init.signal?.addEventListener("abort", cancel, { once: true });
  if (init.signal?.aborted) cancel();
  try {
    return await fetch(path, {
      ...init, headers, credentials: "include", cache: "no-store", signal: controller.signal,
    });
  } catch (error) {
    if (!init.signal?.aborted) {
      throw new Error("No se pudo conectar con SEAL. Comprobá Tailscale y volvé a intentar.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    init.signal?.removeEventListener("abort", cancel);
  }
}

export async function jsonResponse(response: Response) {
  let data;
  try { data = await response.json(); } catch {
    throw new Error(`El servidor no respondió correctamente (HTTP ${response.status}).`);
  }
  if (!response.ok || data.ok === false) {
    throw new Error(response.status === 401 ? "La sesión venció. Volvé a iniciar sesión." :
      typeof data.error === "string" ? data.error : `Error del servidor (HTTP ${response.status}).`);
  }
  return data;
}
