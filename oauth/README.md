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

   Si todavía no hay dominio: deployá, copiá `….vercel.app`, actualizá la env y **Redeploy**.

5. Deploy. En Mercado Libre, Redirect URI = **exactamente** el mismo `MELI_REDIRECT_URI` (sin slash final).

## Uso

Home del dominio → Autorizar → copiá `MELI_ACCESS_TOKEN` (`APP_USR-...`) al `.env` de rental-scout. Guardá también `MELI_REFRESH_TOKEN` (el access dura ~6 h).
