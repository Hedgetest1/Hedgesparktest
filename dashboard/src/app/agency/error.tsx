"use client";
import { RouteErrorFallback } from "../components/RouteErrorFallback";
export default function AgencyError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteErrorFallback {...props} route="agency" />;
}
