const { revokeGrant, escapeHtml, page } = require("../lib/oauth");

function send(res, { status, html }) {
  res.statusCode = status;
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  res.end(html);
}

function readBody(req) {
  if (req.body && typeof req.body === "object" && !Buffer.isBuffer(req.body)) {
    return Promise.resolve(req.body.token || req.body.access_token || "");
  }
  if (typeof req.body === "string" && req.body) {
    return Promise.resolve(new URLSearchParams(req.body).get("token") || "");
  }
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      resolve(new URLSearchParams(raw).get("token") || "");
    });
    req.on("error", reject);
  });
}

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    send(
      res,
      page({
        title: "Revocar grant",
        status: 405,
        body: `<h1>Usá el formulario de la home</h1>
<p>Para revocar el grant y volver a autorizar, empezá en <a href="/">la home</a>.</p>`,
      }),
    );
    return;
  }

  try {
    const token = await readBody(req);
    await revokeGrant(token);
    res.setHeader("Cache-Control", "no-store");
    res.redirect(302, "/api/start?reauth=1");
  } catch (err) {
    send(
      res,
      page({
        title: "No se pudo revocar",
        status: err.statusCode || 500,
        body: `<h1>No se pudo revocar el grant</h1>
<p>${escapeHtml(err.message)}</p>
<p class="muted">Si el token ya no sirve, borralo a mano en
<a href="https://developers.mercadolibre.com.ar">developers.mercadolibre.com.ar</a>
→ tu app → Administrar permisos, y después
<a href="/api/start?reauth=1">autorizá de nuevo</a>.</p>
<p><a href="/">Volver</a></p>`,
      }),
    );
  }
};
