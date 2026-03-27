/**
 * Spark Reputation System — trust through validated accuracy.
 *
 * Reads proof status from SparkAction[] (already computed by actionEngine v3).
 * Tracks cumulative improving/worsening counts in localStorage.
 * Produces a reputation score shown only when sufficient evidence exists.
 *
 * No new data sources. No LLM. No fake percentages.
 */

const REPUTATION_KEY = "hs_spark_reputation";
const MIN_EVENTS = 3; // minimum proof events before showing score

type ReputationData = {
  improving: number;
  worsening: number;
  total: number;
  lastUpdated: number;
};

function load(): ReputationData {
  try {
    const raw = localStorage.getItem(REPUTATION_KEY);
    if (!raw) return { improving: 0, worsening: 0, total: 0, lastUpdated: 0 };
    return JSON.parse(raw);
  } catch {
    return { improving: 0, worsening: 0, total: 0, lastUpdated: 0 };
  }
}

function save(data: ReputationData): void {
  try { localStorage.setItem(REPUTATION_KEY, JSON.stringify(data)); } catch { /* noop */ }
}

export type ReputationScore = {
  /** Accuracy percentage (0-100). null if insufficient data. */
  accuracy: number | null;
  improving: number;
  worsening: number;
  total: number;
  /** Whether enough data exists to display the score */
  ready: boolean;
};

/**
 * Update reputation from current action proof statuses.
 * Call once per dashboard load with the current sparkActions.
 * Deduplicates by only updating once per 4-hour window.
 */
export function updateReputation(
  proofStatuses: Array<{ id: string; proofStatus?: string }>
): ReputationScore {
  const data = load();

  // Only update once per 4 hours to avoid inflation from page refreshes
  const now = Date.now();
  const cooldown = 4 * 60 * 60 * 1000;

  if (now - data.lastUpdated > cooldown) {
    let newImproving = 0;
    let newWorsening = 0;

    for (const a of proofStatuses) {
      if (a.proofStatus === "improving") newImproving++;
      if (a.proofStatus === "worsening") newWorsening++;
    }

    if (newImproving > 0 || newWorsening > 0) {
      data.improving += newImproving;
      data.worsening += newWorsening;
      data.total += newImproving + newWorsening;
      data.lastUpdated = now;
      save(data);
    }
  }

  const ready = data.total >= MIN_EVENTS;
  const accuracy = ready ? Math.round((data.improving / data.total) * 100) : null;

  return { accuracy, improving: data.improving, worsening: data.worsening, total: data.total, ready };
}
