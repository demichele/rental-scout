const { test, mock } = require("node:test");
const assert = require("node:assert/strict");

process.env.MELI_CLIENT_ID = "123456";
process.env.MELI_CLIENT_SECRET = "secret";
process.env.MELI_REDIRECT_URI = "https://example.vercel.app/api/callback";

const {
  AUTH_URL,
  LOGOUT_URL,
  USERS_ME_URL,
  authorizationUrl,
  logoutThenAuthorizeUrl,
  applicationsUrl,
  revokeGrant,
} = require("./oauth");

test("authorizationUrl includes response_type, client_id, redirect_uri, state", () => {
  const url = new URL(authorizationUrl("abc"));
  assert.equal(url.origin + url.pathname, AUTH_URL);
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("client_id"), "123456");
  assert.equal(url.searchParams.get("redirect_uri"), "https://example.vercel.app/api/callback");
  assert.equal(url.searchParams.get("state"), "abc");
});

test("logoutThenAuthorizeUrl sends the auth URL as go=", () => {
  const url = new URL(logoutThenAuthorizeUrl("abc"));
  assert.equal(url.origin + url.pathname, LOGOUT_URL);
  const go = url.searchParams.get("go");
  assert.ok(go.startsWith(AUTH_URL));
  assert.ok(go.includes("state=abc"));
});

test("applicationsUrl uses user id and client id", () => {
  assert.equal(
    applicationsUrl(158748587),
    "https://api.mercadolibre.com/users/158748587/applications/123456",
  );
});

test("revokeGrant DELETEs the user application after /users/me", async () => {
  const calls = [];
  mock.method(globalThis, "fetch", async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET" });
    if (String(url) === USERS_ME_URL) {
      return {
        ok: true,
        json: async () => ({ id: 99, nickname: "TEST" }),
      };
    }
    return {
      ok: true,
      json: async () => ({ msg: "Autorización eliminada" }),
    };
  });
  try {
    const result = await revokeGrant("APP_USR-token");
    assert.deepEqual(result, { userId: "99", appId: "123456" });
    assert.equal(calls[0].url, USERS_ME_URL);
    assert.equal(calls[0].method, "GET");
    assert.equal(calls[1].url, applicationsUrl(99));
    assert.equal(calls[1].method, "DELETE");
  } finally {
    mock.restoreAll();
  }
});

test("revokeGrant fails without a token and does not call the API", async () => {
  mock.method(globalThis, "fetch", async () => {
    throw new Error("no debería llamar a fetch");
  });
  try {
    await assert.rejects(() => revokeGrant("  "), /MELI_ACCESS_TOKEN/);
  } finally {
    mock.restoreAll();
  }
});
