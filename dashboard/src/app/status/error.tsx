"use client";
import { RouteErrorFallback } from "../components/RouteErrorFallback";
export default function StatusError(props: { error: Error & { digest?: string }; reset: () => void }) {
  return <RouteErrorFallback {...props} route="status" />;
}
