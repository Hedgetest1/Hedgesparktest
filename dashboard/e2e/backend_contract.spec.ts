import { expect, test } from "@playwright/test";

/**
 * Backend contract smoke — verifies the backend endpoints the landing
 * page and dashboard rely on return the shape we expect. These tests
 * catch the "type change ships but frontend still assumes old shape"
 * class of bug — the one that unit tests never see.
 */

const API_BASE = process.env.E2E_API_BASE || "http://127.0.0.1:8000";

test("public ROI counter returns honest state shape", async ({ request }) => {
  const r = await request.get(`${API_BASE}/public/roi-counter`);
  expect(r.ok()).toBeTruthy();
  const doc = await r.json();

  // Shape contract
  expect(doc).toHaveProperty("state");
  expect(["live", "warming"]).toContain(doc.state);
  expect(doc).toHaveProperty("prevented_eur_30d");
  expect(typeof doc.prevented_eur_30d).toBe("number");
  expect(doc).toHaveProperty("shops_contributing");
  expect(doc).toHaveProperty("by_vertical");
  expect(Array.isArray(doc.by_vertical)).toBe(true);
  expect(doc).toHaveProperty("publish_thresholds");

  // Honesty guarantee: if we're in warming state, the number matches
  // the real figure. No fabrication.
  if (doc.state === "warming") {
    expect(doc.prevented_eur_30d).toBeLessThan(doc.publish_thresholds.min_eur);
  }
});

test("system health endpoint responds with shape", async ({ request }) => {
  const r = await request.get(`${API_BASE}/system/health`);
  expect(r.ok()).toBeTruthy();
  const doc = await r.json();
  expect(doc).toHaveProperty("subsystems");
  expect(doc.subsystems).toHaveProperty("database");
});
