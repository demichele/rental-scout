# OAuth Mercado Libre (redirect URI)

App **Node** independiente. Si en Vercel el Root Directory es `oauth`, Python no existe para el deploy: no se lee el `pyproject.toml` de la raíz.

Mercado Libre redirige a `/api/callback`; el server intercambia el `code` por `access_token` + `refresh_token`.

## Redirect URI

```text
https://TU-PROYECTO.vercel.app/api/callback
```

Sin slash al final. El mismo string en el panel de ML, en `MELI_REDIRECT_URI`, y en el authorize.

## Deploy

1. Settings → General → **Root Directory:** `oauth`
2. Framework Preset: Other (o dejá que detecte por `package.json`)
3. **Output Directory** vacío (no `public`)
4. Env Production: `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET`, `MELI_REDIRECT_URI`
5. Redeploy. Home → Autorizar. Copiá `MELI_ACCESS_TOKEN` al `.env` local.
