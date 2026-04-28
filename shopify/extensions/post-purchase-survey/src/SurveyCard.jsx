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

export default function SurveyCard({api, surface}) {
  const {shop, orderConfirmation, storage, customerPrivacy} = api;
  const shopDomain = shop?.myshopifyDomain || "";
  const orderId = orderConfirmation?.current?.order?.id || "";

  const [phase, setPhase] = useState("loading"); // loading|hidden|prompt|submitting|done|already
  const [config, setConfig] = useState(null);
  const [choice, setChoice] = useState(null);
  const [otherText, setOtherText] = useState("");
  const [errorMsg, setErrorMsg] = useState("");

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
      } catch (_err) {
        // Silent failure — never break the Thank-You experience
        if (!cancelled) setPhase("hidden");
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

  if (phase === "loading" || phase === "hidden") return null;
  if (phase === "already") {
    return (
      <BlockStack spacing="tight">
        <Text appearance="subdued">Thanks for sharing earlier ✓</Text>
      </BlockStack>
    );
  }
  if (phase === "done") {
    return (
      <BlockStack spacing="tight">
        <Text emphasis="bold">Thanks! ✓</Text>
        <Text appearance="subdued">Your answer helps the store improve.</Text>
      </BlockStack>
    );
  }

  const options = (config?.options || []).map((opt) => ({label: opt.label, value: opt.value}));
  if (config?.allow_other) options.push({label: "Other", value: "other"});

  return (
    <BlockStack spacing="base">
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
