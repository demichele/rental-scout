const crypto = require("crypto");
const {
  authorizationUrl,
  logoutThenAuthorizeUrl,
  cookieHeader,
  isSecureRequest,
  escapeHtml,
  page,
} = require("../lib/oauth");

module.exports = (req, res) => {
  try {
    const url = new URL(req.url, `https://${req.headers.host}`);
    const reauth = url.searchParams.get("reauth") === "1";
    const state = crypto.randomBytes(16).toString("hex");
    res.setHeader("Set-Cookie", cookieHeader(state, { secure: isSecureRequest(req) }));
    res.setHeader("Cache-Control", "no-store");
    res.redirect(302, reauth ? logoutThenAuthorizeUrl(state) : authorizationUrl(state));
  } catch (err) {
    const { status, html } = page({
      title: "OAuth MELI",
      status: err.statusCode || 500,
      body: `<h1>No se puede iniciar OAuth</h1><p>${escapeHtml(err.message)}</p>
<p class="muted">En Vercel cargá <code>MELI_CLIENT_ID</code>, <code>MELI_CLIENT_SECRET</code> y <code>MELI_REDIRECT_URI</code>.</p>`,
    });
    res.statusCode = status;
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end(html);
  }
};
