"use client";
import { RouteErrorFallback } from "../components/RouteErrorFallback";
export default function PricingError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteErrorFallback {...props} route="pricing" />;
}
