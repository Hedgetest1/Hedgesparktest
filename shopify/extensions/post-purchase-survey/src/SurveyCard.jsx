// HedgeSpark — Post-Purchase Survey component (target-agnostic)
//
// Used by both ThankYou.jsx and OrderStatus.jsx. The component is
// pure UI; the extension target binding lives in the per-target
// entry files.
//
// CONTRACT:
//   GET  https://api.hedgesparkhq.com/survey/config?shop=<domain>
//        → { question_key, question, options:[{label,value}],
//            allow_other, version, disabled_on_order_status? }
//   POST https://api.hedgesparkhq.com/survey/response
//        body { shop_domain, order_id, question_key, answer_choice,
//               answer_text, consent_given }
//        → 200 OK | 409 already_answered | 429 rate_limited
//
// IDEMPOTENCY: DB UNIQUE + shopify.storage.local flag per order.
// CONSENT:     reads customerPrivacy; analytics=false → render null.
// FAILURE:     all network errors degrade silently (return null) —
//              never break the customer's checkout experience.

import {BlockStack, Heading, Text, ChoiceList, Choice, TextField, Button, Banner} from "@shopify/ui-extensions-react/checkout";
import {useEffect, useState} from "react";

const API_BASE = "https://api.hedgesparkhq.com";

// Module-load sentinel — fires the moment Shopify imports this bundle,
// even before any React component instantiates. If the founder sees this
// in DevTools Console but never sees the Banner, the bundle is loading
// but React rendering is failing. If the founder doesn't see this log
// AT ALL on the Thank-You page, the bundle is not being fetched —
// proving CDN propagation lag or a Shopify-side registration issue,
// NOT a code bug.
console.log("[HedgeSpark survey v11] module loaded — bundle fetched OK");

export default function SurveyCard({api, surface}) {
  // Component-mount sentinel — fires when React instantiates the
  // component on a checkout page that has the block placed.
  console.log("[HedgeSpark survey v11] SurveyCard component instantiated", {
    hasApi: !!api,
    apiKeys: api ? Object.keys(api) : [],
    surface,
  });

  // Defensive: if Shopify ever passes a bare/undefined api, don't crash
  // the whole React tree — we want at least the dev-store probe to show.
  const safeApi = api || {};
  const {shop, orderConfirmation, storage, customerPrivacy} = safeApi;
  const shopDomain = shop?.myshopifyDomain || "";
  const orderId = orderConfirmation?.current?.order?.id || "";

  const [phase, setPhase] = useState("loading"); // loading|hidden|prompt|submitting|done|already|debug_error
  const [config, setConfig] = useState(null);
  const [choice, setChoice] = useState(null);
  const [otherText, setOtherText] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [debugTrace, setDebugTrace] = useState("");

  // Render visible probe + errors on dev/test stores so we can diagnose
  // without guessing from the browser console. Real customer stores keep
  // the silent-fail behavior. The check is intentionally permissive: it
  // matches any myshopify subdomain that LOOKS like a dev/test/staging
  // store, plus the empty-domain fallback that hits when `shop` is
  // undefined in the api object (a possibility we want to make visible).
  const isDevStore =
    !shopDomain ||
    shopDomain.includes("hedgespark-dev") ||
    shopDomain.includes("dev.myshopify") ||
    shopDomain.includes("test.myshopify") ||
    shopDomain.includes("staging.myshopify");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Consent gate — analytics-denied → render nothing
        const consent = await customerPrivacy?.currentVisitorConsent?.();
        if (consent && consent.analyticsProcessingAllowed === false) {
          if (!cancelled) setPhase("hidden");
          return;
        }

        // Local dedup — already submitted/skipped for this order → hide.
        // Defensive: `storage` may be undefined on some surfaces (e.g.
        // customer-account.order-status renders without buyer-journey
        // storage in some Shopify revisions). Continue without dedup
        // rather than throwing into the silent-failure catch below.
        if (storage && typeof storage.read === "function" && orderId) {
          try {
            const local = await storage.read(`hs_survey_${orderId}`);
            if (local === "submitted" || local === "skipped") {
              if (!cancelled) setPhase("already");
              return;
            }
          } catch (_) {
            // storage unavailable — fall through; server-side UNIQUE
            // (shop_domain, order_id, question_key) still dedups
          }
        }

        // Fetch shop-level config
        const res = await fetch(`${API_BASE}/survey/config?shop=${encodeURIComponent(shopDomain)}`);
        if (!res.ok) throw new Error(`config_fetch_${res.status}`);
        const data = await res.json();
        if (cancelled) return;

        // Merchant-level gate for Order-Status surface
        if (surface === "order-status" && data.disabled_on_order_status === true) {
          setPhase("hidden");
          return;
        }

        setConfig(data);
        setPhase("prompt");
      } catch (err) {
        // Silent failure on real stores — never break the Thank-You
        // experience. On the dev store, render the trace so we can
        // diagnose without guessing from the browser console.
        if (cancelled) return;
        if (isDevStore) {
          const trace = [
            `phase: useEffect-init`,
            `surface: ${surface || "?"}`,
            `shop: ${shopDomain || "?"}`,
            `orderId: ${orderId || "?"}`,
            `error: ${err && err.name ? err.name : "?"}: ${err && err.message ? err.message : String(err)}`,
            err && err.stack ? `stack(head): ${String(err.stack).split("\n").slice(0, 3).join(" | ")}` : "",
          ].filter(Boolean).join("\n");
          setDebugTrace(trace);
          setPhase("debug_error");
        } else {
          setPhase("hidden");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [shopDomain, orderId, surface]);

  async function submit() {
    if (!choice) return;
    setPhase("submitting");
    const isOther = choice === "other";
    const body = {
      shop_domain: shopDomain,
      order_id: orderId,
      question_key: config?.question_key || "how_did_you_hear",
      answer_choice: isOther ? "other" : choice,
      answer_text: isOther ? (otherText || "").slice(0, 500) : null,
      consent_given: true,
    };
    try {
      const res = await fetch(`${API_BASE}/survey/response`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
      const writeLocal = async () => {
        if (storage && typeof storage.write === "function" && orderId) {
          try { await storage.write(`hs_survey_${orderId}`, "submitted"); } catch (_) {}
        }
      };
      if (res.ok) {
        await writeLocal();
        setPhase("done");
        return;
      }
      if (res.status === 409) {
        await writeLocal();
        setPhase("already");
        return;
      }
      throw new Error(`submit_${res.status}`);
    } catch (_err) {
      setErrorMsg("Something went wrong. Try again?");
      setPhase("prompt");
    }
  }

  // TEMPORARY UNCONDITIONAL DIAGNOSTIC PROBE — visible on EVERY store
  // (dev + real customer) to prove the extension mounted. If the founder
  // (or any tester) places a real test order and DOES NOT see this banner
  // on the Thank-You page, the extension is not mounting at all and the
  // bug is in the deploy/configuration layer, not in this component.
  // Revert to dev-only gate (or remove entirely) once render is confirmed.
  const probe = (
    <Banner status="info" title="HedgeSpark survey v11 loaded">
      <Text>{`phase=${phase} shop=${shopDomain || "(empty)"} order=${orderId || "(empty)"} surface=${surface || "?"} apiKeys=${Object.keys(safeApi).join(",") || "(none)"}`}</Text>
    </Banner>
  );

  // Always render the probe — including during loading/hidden — so the
  // diagnostic is visible no matter what state the consent gate or the
  // config fetch leave us in. Real customers will see the banner during
  // the diagnostic window; that's accepted per the temporary mandate.
  if (phase === "loading" || phase === "hidden") {
    return <BlockStack spacing="tight">{probe}</BlockStack>;
  }
  if (phase === "debug_error") {
    return (
      <BlockStack spacing="tight">
        {probe}
        <Banner status="critical" title="HedgeSpark Survey — debug (dev store only)">
          <Text>{debugTrace || "Unknown error"}</Text>
        </Banner>
      </BlockStack>
    );
  }
  if (phase === "already") {
    return (
      <BlockStack spacing="tight">
        {probe}
        <Text appearance="subdued">Thanks for sharing earlier ✓</Text>
      </BlockStack>
    );
  }
  if (phase === "done") {
    return (
      <BlockStack spacing="tight">
        {probe}
        <Text emphasis="bold">Thanks! ✓</Text>
        <Text appearance="subdued">Your answer helps the store improve.</Text>
      </BlockStack>
    );
  }

  const options = (config?.options || []).map((opt) => ({label: opt.label, value: opt.value}));
  if (config?.allow_other) options.push({label: "Other", value: "other"});

  return (
    <BlockStack spacing="base">
      {probe}
      <Heading level={3}>{config?.question || "How did you hear about us?"}</Heading>
      {errorMsg ? <Banner status="critical">{errorMsg}</Banner> : null}
      <ChoiceList
        name="hs-survey"
        value={choice || ""}
        onChange={(v) => setChoice(typeof v === "string" ? v : (Array.isArray(v) ? v[0] : ""))}
      >
        {options.map((o) => (
          <Choice key={o.value} id={o.value}>{o.label}</Choice>
        ))}
      </ChoiceList>
      {choice === "other" ? (
        <TextField
          label="Tell us more"
          value={otherText}
          onChange={setOtherText}
          maxLength={500}
        />
      ) : null}
      <Button
        kind="primary"
        disabled={!choice || (choice === "other" && !otherText.trim())}
        loading={phase === "submitting"}
        onPress={submit}
      >
        Submit
      </Button>
    </BlockStack>
  );
}
