const {
  STATE_COOKIE,
  parseCookies,
  clearCookieHeader,
  isSecureRequest,
  escapeHtml,
  page,
  exchangeCode,
} = require("../lib/oauth");

function send(res, { status, html }, extraHeaders = {}) {
  res.statusCode = status;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  for (const [key, value] of Object.entries(extraHeaders)) {
    res.setHeader(key, value);
  }
  res.end(html);
}

module.exports = async (req, res) => {
  const url = new URL(req.url, `https://${req.headers.host}`);
  const code = (url.searchParams.get("code") || "").trim();
  const state = (url.searchParams.get("state") || "").trim();
  const oauthError = url.searchParams.get("error");
  const cookies = parseCookies(req.headers.cookie);
  const expected = cookies[STATE_COOKIE] || "";
  const clear = { "Set-Cookie": clearCookieHeader({ secure: isSecureRequest(req) }) };

  if (oauthError) {
    send(
      res,
      page({
        title: "OAuth cancelado",
        status: 400,
        body: `<h1>Mercado Libre no autorizó</h1><p>${escapeHtml(oauthError)}</p>
<p>${escapeHtml(url.searchParams.get("error_description") || "")}</p>
<p><a href="/">Volver</a></p>`,
      }),
      clear,
    );
    return;
  }

  if (!code) {
    send(
      res,
      page({
        title: "Falta code",
        status: 400,
        body: `<h1>No llegó el authorization code</h1>
<p>La redirect URI de la app tiene que ser exactamente <code>/api/callback</code> (sin slash final, sin query).</p>
<p><a href="/">Volver</a></p>`,
      }),
      clear,
    );
    return;
  }

  if (!expected || expected !== state) {
    send(
      res,
      page({
        title: "State inválido",
        status: 400,
        body: `<h1>El parámetro state no coincide</h1>
<p>Empezá de nuevo desde <a href="/">la home</a> (no abras el callback a mano).</p>`,
      }),
      clear,
    );
    return;
  }

  try {
    const token = await exchangeCode(code);
    const access = token.access_token || "";
    const refresh = token.refresh_token || "";
    const expires = token.expires_in != null ? String(token.expires_in) : "";
    send(
      res,
      page({
        title: "Token MELI",
        body: `<h1>Listo. Copiá esto a <code>.env</code></h1>
<p class="muted">El access token dura ~6 horas. El refresh token sirve para renovarlo sin volver a loguearte.</p>
<div class="box warn">No subas estos valores a git ni los pegues en un chat público.</div>
<label for="access">MELI_ACCESS_TOKEN</label>
<textarea id="access" readonly>${escapeHtml(access)}</textarea>
<label for="refresh">MELI_REFRESH_TOKEN</label>
<textarea id="refresh" readonly>${escapeHtml(refresh)}</textarea>
<p>expires_in=${escapeHtml(expires)}s · user_id=${escapeHtml(token.user_id || "")} · scope=${escapeHtml(token.scope || "")}</p>
<p class="muted">Si no viste la pantalla de “asociar aplicación”, Mercado Libre reutilizó el grant anterior. Para autorizar de cero: <a href="/">revocá y volvé a autorizar</a>.</p>
<p><a class="button" href="/">Volver</a> · <a href="/api/start?reauth=1">Cerrar sesión y autorizar</a></p>`,
      }),
      clear,
    );
  } catch (err) {
    send(
      res,
      page({
        title: "Error al canjear el code",
        status: err.statusCode || 500,
        body: `<h1>No se pudo obtener el access token</h1>
<p>${escapeHtml(err.message)}</p>
<p class="muted">Causas típicas: redirect_uri distinta a la de la app, Client Secret mal copiado, o el code ya se usó / expiró (10 min).</p>
<p><a href="/">Reintentar</a></p>`,
      }),
      clear,
    );
  }
};
