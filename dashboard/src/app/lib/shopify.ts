/**
 * Shopify deep-link helpers.
 *
 * The merchant's biggest complaint with pure read-only dashboards is:
 * "you told me to fix X but I still have to hunt it in Shopify admin
 * myself." These helpers turn every product suggestion into a one-click
 * jump to the right page in the merchant's admin.
 *
 * Two classes of link:
 *   1. `buildShopifyAdminProductUrl` — takes a product id/url/handle
 *      and returns the best admin-side URL we can construct:
 *        - numeric Shopify ID → direct admin edit page
 *        - URL / handle        → admin product search filtered to it
 *   2. `buildStorefrontUrl` — takes a path (from Live Opportunities,
 *      where rows are storefront URLs) and returns the full https URL
 *      so the merchant can see the page her visitors see.
 *
 * Both helpers are null-safe — they return `null` when any required
 * piece is missing so the caller can render a non-link fallback.
 */

function cleanShopDomain(shop: string): string {
  return shop.replace(/^https?:\/\//i, "").replace(/\/+$/g, "");
}

/**
 * Build an admin-side URL for a product the merchant should edit.
 *
 * Shopify admin product URL shapes:
 *   - By numeric ID (preferred, deep links to the edit page):
 *       https://{shop}/admin/products/{numericId}
 *   - By handle/URL (fallback via admin search):
 *       https://{shop}/admin/products?query={handle}
 *
 * Returns null when shop or productIdOrUrl is missing/empty.
 */
export function buildShopifyAdminProductUrl(
  shop: string | null | undefined,
  productIdOrUrl: string | number | null | undefined,
): string | null {
  if (!shop) return null;
  if (productIdOrUrl === null || productIdOrUrl === undefined) return null;

  const cleanShop = cleanShopDomain(String(shop));
  if (!cleanShop) return null;

  const value = String(productIdOrUrl).trim();
  if (!value) return null;

  if (/^\d+$/.test(value)) {
    return `https://${cleanShop}/admin/products/${value}`;
  }

  const handleMatch = value.match(/\/products\/([^/?#]+)/);
  const handle = handleMatch ? handleMatch[1] : value;
  return `https://${cleanShop}/admin/products?query=${encodeURIComponent(handle)}`;
}

/**
 * Build a full storefront URL from a path. Useful for Live Opportunity
 * rows where each row is a page on the merchant's store — the merchant
 * clicks to see the page her visitors see.
 *
 * Returns null when shop or path is missing.
 */
export function buildStorefrontUrl(
  shop: string | null | undefined,
  pathOrUrl: string | null | undefined,
): string | null {
  if (!shop || !pathOrUrl) return null;
  const cleanShop = cleanShopDomain(String(shop));
  if (!cleanShop) return null;

  const value = String(pathOrUrl).trim();
  if (!value) return null;

  if (/^https?:\/\//i.test(value)) {
    return value;
  }

  const path = value.startsWith("/") ? value : `/${value}`;
  return `https://${cleanShop}${path}`;
}
