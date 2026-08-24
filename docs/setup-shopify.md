# Shopify Setup Guide

Full OAuth connect flow requires a Shopify Partner account, a development store, and an app.

## 1. Partner account + development store

1. Sign up at <https://www.shopify.com/partners> (free).
2. In the Partner Dashboard → **Stores** → **Add store** → **Create development store**.
3. Give it a name; under *Store type* you can pick "Custom app / testing".
4. Seed products in the dev store admin (see §4 below) so the catalog sync has data.

## 2. Create the app for OAuth

1. Partner Dashboard → **Apps** → **Create app**.
2. Choose "Public app" / custom distribution when prompted.
3. Note the **Client ID** (`SHOPIFY_API_KEY`) and **Client secret** (`SHOPIFY_API_SECRET`).
4. Under **Configuration → URLs**:

   - App URL: `http://localhost:5173`
   - Allowed redirection URI:

     ```
     http://localhost:8000/api/v1/onboarding/shopify/callback
     ```

5. Requested scopes are read from `.env`:

   ```
   read_products,read_inventory,write_draft_orders,read_shop
   ```

   Development stores allow `http://localhost` redirect URIs, so no tunnel is needed.

## 3. Connect from the dashboard

1. Fill `.env`:

   ```env
   SHOPIFY_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   SHOPIFY_API_SECRET=shpss_xxxxxxxxxxxxxxxxxxxxxxxx
   SHOPIFY_API_VERSION=2026-07        # verify current stable version before demoing
   ```

2. Restart the API, open the dashboard → **Connect Store**, enter `your-store.myshopify.com`,
   click **Make AI-Native** and approve on Shopify's consent screen.
3. You land back on the dashboard; the profile page shows the synced catalog.

## 4. Suggested seed product (matches the PRD demo)

Create a product titled exactly **Nike Downshifter 14** (vendor `Nike`, category/type
`Running Shoes`) with variants:

| Option values      | Price    | Inventory |
| ------------------ | -------- | --------- |
| Size 9 / Black     | ₹4,799   | 8         |
| Size 8 / Black     | ₹4,799   | 12        |
| Size 10 / Black    | ₹4,799   | 5         |

Optionally add a second product (e.g. Nike Revolution 7 @ ₹3,695) so search results
look natural.

## 5. What the platform stores

- The Admin API access token is encrypted (Fernet, keyed by `SECRET_KEY`) and stored
  server-side in `merchant_integrations.auth_reference_encrypted`. It never reaches the
  browser or any agent tool.
- On connect the platform fetches shop metadata, syncs the catalog into canonical tables,
  and generates the Merchant Agent Profile.

## Troubleshooting

- **SHOPIFY_HMAC_INVALID** – callback params were tampered or the app secret mismatches.
- **SHOPIFY_STATE_INVALID** – state expired (>10 min); restart the connect flow.
- **sync deferred** warning – OAuth succeeded but catalog sync failed; press *Re-sync
  catalog* on the Profile page after fixing credentials/scopes.
