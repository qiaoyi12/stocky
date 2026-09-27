// Pure, typed helpers that derive dashboard KPIs from the real inventory data
// returned by the backend API. Every figure here is an *honest* proxy computed
// directly from fields already present on each InventoryItem — there are no
// fabricated constants, no random values, and no I/O. Keeping these functions
// pure (input -> number/string) makes them trivially testable and lets the React
// layer stay a thin presentation shell over deterministic calculations.

import type { InventoryItem, DashboardResponse } from "../api/client";

// Classification labels that indicate an item needs attention (i.e. it is not
// "healthy"). Kept as a readonly tuple so it can be reused by both the health
// predicate and the percentage calculation.
const ATTENTION_LABELS: readonly string[] = [
  "stockout_risk",
  "needs_reorder",
  "overstock",
  "slow_moving",
  "trend_anomaly",
];

/** Total on-hand inventory value: sum of current_stock * unit_cost across items. */
export function totalStockValue(items: InventoryItem[]): number {
  return items.reduce((sum, item) => sum + item.current_stock * item.unit_cost, 0);
}

/**
 * An item is "healthy" when its classifications carry none of the attention
 * labels (stockout_risk, needs_reorder, overstock, slow_moving, trend_anomaly).
 * Exported so the same predicate can be reused wherever the health notion is
 * needed instead of re-deriving it.
 */
export function isHealthy(item: InventoryItem): boolean {
  return !item.classifications.some((label) => ATTENTION_LABELS.includes(label));
}

/**
 * Percentage (0..100, rounded) of items considered healthy per isHealthy.
 * Returns 0 for an empty list to avoid a divide-by-zero.
 */
export function stockHealthPct(items: InventoryItem[]): number {
  if (items.length === 0) {
    return 0;
  }
  const healthy = items.filter(isHealthy).length;
  return Math.round((healthy / items.length) * 100);
}

/** Number of items whose classifications include the given label. */
export function countByClassification(items: InventoryItem[], label: string): number {
  return items.filter((item) => item.classifications.includes(label)).length;
}

/** Items flagged with a near-term stockout risk. */
export function stockoutCount(items: InventoryItem[]): number {
  return countByClassification(items, "stockout_risk");
}

/** Items flagged as carrying more stock than needed. */
export function overstockCount(items: InventoryItem[]): number {
  return countByClassification(items, "overstock");
}

/** Items with no recent sales — a proxy for dead / non-moving stock. */
export function deadStockCount(items: InventoryItem[]): number {
  return items.filter((item) => item.no_recent_sales === true).length;
}

/**
 * Estimated excess units held across overstocked items.
 *
 * Honest definition: for each item classified "overstock", the excess is the
 * stock held above its reorder point, i.e. max(0, current_stock - reorder_point),
 * summed over all overstock items. This is a simple, transparent proxy for
 * "excess inventory units" using fields that already exist on the item — it is
 * not a modelled or fabricated figure.
 */
export function excessUnits(items: InventoryItem[]): number {
  return items
    .filter((item) => item.classifications.includes("overstock"))
    .reduce((sum, item) => sum + Math.max(0, item.current_stock - item.reorder_point), 0);
}

// Priority-ordered status labels. primaryStatus returns the first of these that
// an item carries, so the UI can show a single representative badge.
const STATUS_PRIORITY: readonly string[] = [
  "stockout_risk",
  "needs_reorder",
  "overstock",
  "slow_moving",
  "trend_anomaly",
  "fast_moving",
];

/**
 * Pick a single representative status label for an item, in priority order:
 * stockout_risk > needs_reorder > overstock > slow_moving > trend_anomaly >
 * fast_moving. Falls back to "healthy" when the item carries none of these.
 */
export function primaryStatus(item: InventoryItem): string {
  const match = STATUS_PRIORITY.find((label) => item.classifications.includes(label));
  return match ?? "healthy";
}

// Keyword -> emoji map, checked case-insensitively against the product name and
// then its category. Ordered so more specific words are matched deterministically.
const EMOJI_KEYWORDS: ReadonlyArray<readonly [string, string]> = [
  ["bunny", "🐰"],
  ["rabbit", "🐰"],
  ["teddy", "🐻"],
  ["bear", "🐻"],
  ["cat", "🐱"],
  ["key", "🔑"],
  ["mug", "☕"],
  ["cup", "☕"],
  ["lamp", "💡"],
  ["pillow", "🛏️"],
  ["bag", "👜"],
  ["dog", "🐶"],
  ["frog", "🐸"],
  ["panda", "🐼"],
  ["hamster", "🐹"],
];

// Neutral fallback icons chosen deterministically by hashing the SKU.
const FALLBACK_EMOJIS: readonly string[] = ["📦", "🧸", "🎁", "🪀", "🧩"];

/**
 * Deterministic cute emoji for a product. First tries a keyword match against
 * the lowercased name, then the category; on no match, hashes the SKU to pick a
 * stable neutral fallback. The same item always maps to the same icon.
 */
export function productEmoji(item: InventoryItem): string {
  const name = item.name.toLowerCase();
  const category = item.category.toLowerCase();
  for (const [keyword, emoji] of EMOJI_KEYWORDS) {
    if (name.includes(keyword) || category.includes(keyword)) {
      return emoji;
    }
  }
  // Simple deterministic string hash (djb2-style) over the SKU.
  let hash = 0;
  for (let i = 0; i < item.sku.length; i += 1) {
    hash = (hash * 31 + item.sku.charCodeAt(i)) >>> 0;
  }
  return FALLBACK_EMOJIS[hash % FALLBACK_EMOJIS.length];
}

/**
 * Honest ~7-day sales estimate: the item's daily sales_velocity projected over
 * 7 days and rounded. Missing velocity is treated as zero. The UI should label
 * this as an estimate (e.g. "~7d sales").
 */
export function sevenDaySales(item: InventoryItem): number {
  return Math.round((item.sales_velocity ?? 0) * 7);
}

// Re-export the response type so callers pulling KPI helpers can also reference
// the shape they typically feed in, without a second import line.
export type { DashboardResponse };
