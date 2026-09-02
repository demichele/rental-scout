# OAuth Mercado Libre (redirect URI)

Mini app para Vercel: Mercado Libre redirige a una URL **fija**, el server intercambia el `code` (vale 10 min, un solo uso) por `access_token` + `refresh_token`.

La doc que miraste es de [OAuth de Mercado Pago](https://www.mercadopago.com.ar/developers/es/docs/security/oauth). El flujo es el mismo (Authorization Code), pero **rental-scout busca avisos en Mercado Libre**. Creá la app en [developers.mercadolibre.com.ar](https://developers.mercadolibre.com.ar), no en Mercado Pago. Endpoints:

- Autorizar: `https://auth.mercadolibre.com.ar/authorization`
- Canje: `POST https://api.mercadolibre.com/oauth/token`

## Redirect URI

Tiene que coincidir **carácter por carácter** en tres lugares:

1. Campo Redirect URI de la app en Mercado Libre
2. Env `MELI_REDIRECT_URI` en Vercel
3. Query `redirect_uri` del authorize y del POST `/oauth/token` (esto lo arma la app)

Formato (sin slash final, sin `?`, sin preview URL de Vercel):

```text
https://TU-PROYECTO.vercel.app/api/callback
```

Si cambia una barra o `www`, ML responde `invalid_grant` / “redirect_uri does not match”.

## Deploy en Vercel

1. Importá el repo `rental-scout` en Vercel.
2. **Root Directory:** `oauth`
3. Env vars (Production):

   - `MELI_CLIENT_ID` — App ID
   - `MELI_CLIENT_SECRET` — Secret (el string corto de ~32 chars)
   - `MELI_REDIRECT_URI` — `https://TU-PROYECTO.vercel.app/api/callback`

4. Deploy. Abrí la home del dominio de producción.
5. Pegá esa misma URI en la app de Mercado Libre y guardá.
6. Entrá a la home → **Autorizar con Mercado Libre** (cuenta dueña, no operador).
7. Copiá `MELI_ACCESS_TOKEN` (`APP_USR-...`) al `.env` local.

El token dura ~6 horas. Guardá también `MELI_REFRESH_TOKEN` para renovarlo después.

## Local (opcional)

```bash
cd oauth
npx vercel dev
```

ML casi nunca acepta `http://localhost...` como redirect. Usá el dominio HTTPS de Vercel.
