# OAuth Mercado Libre (Vercel)

Proyecto **Node** aparte del buscador Python. En Vercel, **Root Directory = `oauth`** (antes del primer deploy). Así no se lee el `pyproject.toml` de la raíz.

## Al crear el proyecto

1. Add New → Project → `demichele/rental-scout`
2. **Edit** Root Directory → `oauth`
3. No toques Build Command / Output Directory (los pone `vercel.json`)
4. Environment Variables (Production):

   | Name | Value |
   |---|---|
   | `MELI_CLIENT_ID` | App ID de developers.mercadolibre.com.ar |
   | `MELI_CLIENT_SECRET` | Secret corto (~32 chars), no `APP_USR-` |
   | `MELI_REDIRECT_URI` | `https://<dominio>.vercel.app/api/callback` |

   Esas tres **no** van en el `.env` de Python. El buscador solo usa `MELI_ACCESS_TOKEN` (y conviene guardar `MELI_REFRESH_TOKEN`).

5. Deploy. En Mercado Libre, Redirect URI = **exactamente** el mismo `MELI_REDIRECT_URI` (sin slash final).

## Uso

Home del dominio:

- **Autorizar**: flujo normal. Si la app ya está autorizada y seguís logueado, Mercado Libre **no** vuelve a pedir consentimiento (emite un token con el mismo grant).
- **Cerrar sesión y autorizar**: logout de ML y después OAuth. Sirve para cambiar de cuenta; si el grant sigue vivo, igual puede saltarse el consentimiento.
- **Revocar y autorizar**: pegá el `MELI_ACCESS_TOKEN` actual. Borra el grant (`DELETE /users/{id}/applications/{app_id}`) y arranca OAuth como la primera vez.

Copiá `MELI_ACCESS_TOKEN` (`APP_USR-...`) y `MELI_REFRESH_TOKEN` al `.env` de Python (raíz de rental-scout). No copies Client ID / Secret / Redirect URI ahí. Validá con `python -m jobs.ping_meli`.
