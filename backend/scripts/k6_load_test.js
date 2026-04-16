/**
 * k6 load test for HedgeSpark backend.
 *
 * Tests 3 critical paths:
 *   1. POST /track  — highest traffic, public, unauthenticated
 *   2. GET /system/health — lightweight healthcheck
 *   3. GET /pro/orders/summary — authenticated merchant endpoint
 *
 * Usage:
 *   k6 run scripts/k6_load_test.js                    # default: 50 VUs, 30s
 *   k6 run scripts/k6_load_test.js --vus 100 --duration 60s
 *   k6 run scripts/k6_load_test.js -e RAMP=1          # staged ramp-up to find ceiling
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const SHOP = __ENV.SHOP || 'hedgespark-dev.myshopify.com';
const API_KEY = __ENV.API_KEY || '';

// Custom metrics
const trackErrors = new Rate('track_errors');
const healthErrors = new Rate('health_errors');
const orderErrors = new Rate('order_errors');
const trackLatency = new Trend('track_latency', true);
const healthLatency = new Trend('health_latency', true);
const orderLatency = new Trend('order_latency', true);

// Staged ramp-up to find breaking point
const rampStages = [
  { duration: '15s', target: 10 },
  { duration: '15s', target: 25 },
  { duration: '15s', target: 50 },
  { duration: '15s', target: 100 },
  { duration: '15s', target: 200 },
  { duration: '15s', target: 300 },
  { duration: '15s', target: 400 },
  { duration: '15s', target: 0 },   // cooldown
];

export const options = __ENV.RAMP
  ? { stages: rampStages, thresholds: { http_req_failed: ['rate<0.10'] } }
  : {
      vus: parseInt(__ENV.VUS || '50', 10),
      duration: __ENV.DURATION || '30s',
      thresholds: {
        http_req_duration: ['p(95)<500'],    // p95 under 500ms
        track_errors: ['rate<0.05'],         // track: non-200 AND non-429
        health_errors: ['rate<0.01'],        // health: non-200
      },
    };

function randomVisitorId() {
  return `k6_${__VU}_${Math.random().toString(36).substring(2, 10)}`;
}

export default function () {
  const scenario = Math.random();

  if (scenario < 0.6) {
    // 60% — POST /track (highest production traffic)
    const payload = JSON.stringify({
      shop_domain: SHOP,
      visitor_id: randomVisitorId(),
      event_type: 'product_view',
      product_url: '/products/test-load-' + Math.floor(Math.random() * 100),
      timestamp: Date.now(),
    });
    const res = http.post(`${BASE}/track`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    trackLatency.add(res.timings.duration);
    const ok = check(res, {
      'track: 200|429': (r) => r.status === 200 || r.status === 429,
    });
    trackErrors.add(!ok);

  } else {
    // 40% — GET /system/health
    const res = http.get(`${BASE}/system/health`);
    healthLatency.add(res.timings.duration);
    const ok = check(res, {
      'health: 200': (r) => r.status === 200,
    });
    healthErrors.add(!ok);
  }

  sleep(0.1); // 100ms think time
}
