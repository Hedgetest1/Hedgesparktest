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
//
// POST-MORTEM 2026-04-30 (commits 029f108 → 9a794e5 → fd6d1d5):
// the survey did not render from v7 through v11 because
// shopify.extension.toml had api_version = "2026-04" — a
// non-existent Shopify API version (npm dist-tag latest is
// "2025-07") — while package.json pinned @shopify/ui-extensions
// {,-react} = "2024.10.x". Runtime threw `TypeError: Cannot read
// properties of undefined (reading 'channel')` in
// _evalExtensionSource. Fixed in v12 by aligning both to
// "2025-07" / "2025.7.x". Lesson: Shopify CLI does NOT validate
// api_version against published API versions on deploy.

import {BlockStack, Heading, Text, ChoiceList, Choice, TextField, Button, Banner} from "@shopify/ui-extensions-react/checkout";
import {useEffect, useState} from "react";

const API_BASE = "https://api.hedgesparkhq.com";

export default function SurveyCard({api, surface}) {
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

  // Render debug primitives on dev/test/staging stores only. Real
  // customer stores stay silent-fail. Tightened to match KNOWN dev
  // patterns — the empty-shopDomain fallback was removed in v13
  // because it could leak the probe to real customers if the api
  // object failed to populate `shop` for any reason.
  const isDevStore =
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

  // Dev-store probe — invisible to real customers, useful for future
  // bring-up diagnostics. Stripped to nothing on production stores.
  const probe = isDevStore ? (
    <Banner status="info" title="HS Survey debug">
      <Text>{`phase=${phase} shop=${shopDomain || "(empty)"} order=${orderId || "(empty)"} surface=${surface || "?"}`}</Text>
    </Banner>
  ) : null;

  if (phase === "loading" || phase === "hidden") {
    return isDevStore ? <BlockStack spacing="tight">{probe}</BlockStack> : null;
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
