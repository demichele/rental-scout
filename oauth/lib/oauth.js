const AUTH_URL = "https://auth.mercadolibre.com.ar/authorization";
const TOKEN_URL = "https://api.mercadolibre.com/oauth/token";
const USERS_ME_URL = "https://api.mercadolibre.com/users/me";
const LOGOUT_URL = "https://www.mercadolibre.com/jms/mla/lgz/logout";
const STATE_COOKIE = "meli_oauth_state";

function requiredEnv(name) {
  const value = (process.env[name] || "").trim();
  if (!value) {
    const err = new Error(`Falta la env ${name} en Vercel`);
    err.statusCode = 500;
    throw err;
  }
  return value;
}

function clientId() {
  return requiredEnv("MELI_CLIENT_ID");
}

function clientSecret() {
  return requiredEnv("MELI_CLIENT_SECRET");
}

function redirectUri() {
  return requiredEnv("MELI_REDIRECT_URI");
}

function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    const key = trimmed.slice(0, eq);
    const value = trimmed.slice(eq + 1);
    try {
      out[key] = decodeURIComponent(value);
    } catch {
      out[key] = value;
    }
  }
  return out;
}

function cookieHeader(state, { secure }) {
  const parts = [
    `${STATE_COOKIE}=${encodeURIComponent(state)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    "Max-Age=600",
  ];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

function clearCookieHeader({ secure }) {
  const parts = [
    `${STATE_COOKIE}=`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    "Max-Age=0",
  ];
  if (secure) parts.push("Secure");
  return parts.join("; ");
}

function isSecureRequest(req) {
  const proto = String(req.headers["x-forwarded-proto"] || "").split(",")[0].trim();
  return proto === "https" || process.env.VERCEL_ENV === "production";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function page({ title, body, status = 200 }) {
  const html = `<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>${escapeHtml(title)}</title>
  <style>
    :root { color-scheme: light; }
    body { font: 16px/1.45 system-ui, sans-serif; max-width: 42rem; margin: 2.5rem auto; padding: 0 1.25rem; color: #111; }
    h1 { font-size: 1.35rem; }
    code, textarea { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; }
    textarea { width: 100%; min-height: 4.5rem; padding: 0.6rem; box-sizing: border-box; }
    .box { background: #f4f4f5; border-radius: 8px; padding: 0.9rem 1rem; margin: 0.75rem 0; }
    .warn { background: #fff7ed; }
    a.button { display: inline-block; background: #111; color: #fff; text-decoration: none; padding: 0.65rem 1rem; border-radius: 6px; }
    label { display: block; font-weight: 600; margin-top: 1rem; }
    p.muted { color: #555; font-size: 0.95rem; }
  </style>
</head>
<body>${body}</body>
</html>`;
  return { status, html };
}

async function exchangeCode(code) {
  const body = new URLSearchParams({
    grant_type: "authorization_code",
    client_id: clientId(),
    client_secret: clientSecret(),
    code,
    redirect_uri: redirectUri(),
  });
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const err = new Error(
      data.error_description || data.message || data.error || `HTTP ${response.status}`,
    );
    err.statusCode = response.status;
    err.payload = data;
    throw err;
  }
  return data;
}

function authorizationUrl(state) {
  const url = new URL(AUTH_URL);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", clientId());
  url.searchParams.set("redirect_uri", redirectUri());
  url.searchParams.set("state", state);
  return url.toString();
}

function logoutThenAuthorizeUrl(state) {
  const url = new URL(LOGOUT_URL);
  url.searchParams.set("go", authorizationUrl(state));
  return url.toString();
}

function applicationsUrl(userId) {
  return `https://api.mercadolibre.com/users/${userId}/applications/${clientId()}`;
}

async function revokeGrant(accessToken) {
  const token = String(accessToken || "").trim();
  if (!token) {
    const err = new Error("Pegá el MELI_ACCESS_TOKEN actual para revocar el grant.");
    err.statusCode = 400;
    throw err;
  }
  const me = await fetch(USERS_ME_URL, {
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
  });
  const meBody = await me.json().catch(() => ({}));
  if (!me.ok) {
    const err = new Error(
      meBody.message ||
        meBody.error_description ||
        `Mercado Libre rechazó el token (HTTP ${me.status}). Si ya expiró, revocalo a mano en developers.mercadolibre.com.ar → tu app → Administrar permisos.`,
    );
    err.statusCode = me.status >= 400 ? me.status : 400;
    throw err;
  }
  const userId = meBody.id;
  if (userId == null || userId === "") {
    const err = new Error("/users/me no devolvió user id");
    err.statusCode = 502;
    throw err;
  }
  const del = await fetch(applicationsUrl(userId), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
  });
  const delBody = await del.json().catch(() => ({}));
  if (!del.ok) {
    const err = new Error(
      delBody.message ||
        delBody.msg ||
        delBody.error_description ||
        `No se pudo revocar el grant (HTTP ${del.status}).`,
    );
    err.statusCode = del.status >= 400 ? del.status : 400;
    throw err;
  }
  return { userId: String(userId), appId: clientId() };
}

module.exports = {
  STATE_COOKIE,
  AUTH_URL,
  LOGOUT_URL,
  USERS_ME_URL,
  authorizationUrl,
  logoutThenAuthorizeUrl,
  applicationsUrl,
  cookieHeader,
  clearCookieHeader,
  isSecureRequest,
  parseCookies,
  escapeHtml,
  page,
  exchangeCode,
  revokeGrant,
  redirectUri,
  clientId,
};
