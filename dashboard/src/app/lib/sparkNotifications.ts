/**
 * Spark Smart Notification System — high-value, low-noise.
 *
 * Triggers ONLY for:
 * 1. Proof events (improvement detected)
 * 2. Critical actions (high revenue loss)
 * 3. Store-wide patterns
 *
 * Throttling:
 * - Max 2 notifications per session
 * - 12-hour cooldown per notification type
 * - Deduplicates identical events
 *
 * No spam. No fake urgency. No backend dependency.
 */

export type SparkNotification = {
  id: string;
  type: "proof" | "critical" | "pattern";
  message: string;
  detail?: string;
  target?: string; // section to navigate to
  timestamp: number;
};

// Settings stored in localStorage
const SETTINGS_KEY = "hs_notification_settings";
const HISTORY_KEY = "hs_notification_history";
const SESSION_COUNT_KEY = "hs_notification_session_count";
const MAX_PER_SESSION = 2;
const COOLDOWN_MS = 12 * 60 * 60 * 1000; // 12 hours

export type NotificationSettings = {
  enabled: boolean;
  proofUpdates: boolean;
  criticalActions: boolean;
  patterns: boolean;
};

const DEFAULT_SETTINGS: NotificationSettings = {
  enabled: false,
  proofUpdates: true,
  criticalActions: true,
  patterns: true,
};

export function loadSettings(): NotificationSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: NotificationSettings): void {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* noop */ }
}

type HistoryEntry = { id: string; ts: number };

function loadHistory(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveHistory(history: HistoryEntry[]): void {
  try {
    // Keep only last 50 entries
    localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-50)));
  } catch { /* noop */ }
}

function getSessionCount(): number {
  try {
    return parseInt(sessionStorage.getItem(SESSION_COUNT_KEY) || "0", 10);
  } catch { return 0; }
}

function incrementSessionCount(): void {
  try {
    const c = getSessionCount();
    sessionStorage.setItem(SESSION_COUNT_KEY, String(c + 1));
  } catch { /* noop */ }
}

function isThrottled(notifId: string, history: HistoryEntry[]): boolean {
  const now = Date.now();
  return history.some(h => h.id === notifId && (now - h.ts) < COOLDOWN_MS);
}

/**
 * Generate notifications from current sparkActions.
 * Returns only the notifications that should be shown (after throttling).
 */
export function generateNotifications(
  actions: Array<{
    id: string;
    title: string;
    priority: string;
    isPattern: boolean;
    proofStatus?: string;
    proofDetail?: string;
    impactValue: number;
    targetSection: string;
  }>,
  settings: NotificationSettings,
): SparkNotification[] {
  if (!settings.enabled) return [];

  const history = loadHistory();
  const sessionCount = getSessionCount();
  const notifications: SparkNotification[] = [];
  const now = Date.now();

  // Don't exceed session limit
  if (sessionCount >= MAX_PER_SESSION) return [];

  // 1. Proof events (highest priority notification)
  if (settings.proofUpdates) {
    for (const a of actions) {
      if (a.proofStatus === "improving" && a.proofDetail) {
        const id = `proof-${a.id}`;
        if (!isThrottled(id, history)) {
          notifications.push({
            id,
            type: "proof",
            message: `That's working. ${a.proofDetail}`,
            target: "what-next",
            timestamp: now,
          });
        }
      }
    }
  }

  // 2. Critical actions (revenue loss > $100/week)
  if (settings.criticalActions) {
    for (const a of actions) {
      if (a.priority === "CRITICAL" && a.impactValue >= 100 && a.proofStatus !== "improving") {
        const id = `critical-${a.id}`;
        if (!isThrottled(id, history)) {
          notifications.push({
            id,
            type: "critical",
            message: a.title,
            detail: `~$${a.impactValue}/week at risk`,
            target: "what-next",
            timestamp: now,
          });
        }
      }
    }
  }

  // 3. Store-wide patterns
  if (settings.patterns) {
    for (const a of actions) {
      if (a.isPattern) {
        const id = `pattern-${a.id}`;
        if (!isThrottled(id, history)) {
          notifications.push({
            id,
            type: "pattern",
            message: a.title,
            target: "what-next",
            timestamp: now,
          });
        }
      }
    }
  }

  // Limit to remaining session capacity
  const remaining = MAX_PER_SESSION - sessionCount;
  const toShow = notifications.slice(0, remaining);

  // Record shown notifications
  if (toShow.length > 0) {
    const newHistory = [...history, ...toShow.map(n => ({ id: n.id, ts: n.timestamp }))];
    saveHistory(newHistory);
    for (let i = 0; i < toShow.length; i++) incrementSessionCount();
  }

  return toShow;
}
