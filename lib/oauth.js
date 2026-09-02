const AUTH_URL = "https://auth.mercadolibre.com.ar/authorization";
const TOKEN_URL = "https://api.mercadolibre.com/oauth/token";
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

module.exports = {
  STATE_COOKIE,
  authorizationUrl,
  cookieHeader,
  clearCookieHeader,
  isSecureRequest,
  parseCookies,
  escapeHtml,
  page,
  exchangeCode,
  redirectUri,
};
