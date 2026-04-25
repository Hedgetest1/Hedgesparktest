"use client";

/**
 * YourTeamPanel — "Your Team"
 *
 * Multi-user access per shop: invite team members with role-based access.
 * API: GET/POST/DELETE /pro/team/members
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";

type Member = {
  id: string;
  email: string;
  display_name: string;
  role: string;
  added_at: string;
  added_by: string;
};

const ROLE_LABELS: Record<string, { label: string; color: string }> = {
  viewer: { label: "Viewer", color: "#94a3b8" },
  editor: { label: "Editor", color: "#60a5fa" },
  admin:  { label: "Admin",  color: "#d4893a" },
};

export function YourTeamPanel({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState<string>("viewer");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const { data: j, error: err } = await apiClient.GET("/pro/team/members");
      if (!err && j) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setMembers(((j as any).members || []) as Member[]);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!apiBase || !shop || !isProUser) { setLoading(false); return; }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, shop, isProUser]);

  async function handleSave() {
    setError(null);
    if (!newEmail.includes("@")) { setError("Enter a valid email."); return; }
    setSaving(true);
    try {
      const { error: err } = await apiClient.POST("/pro/team/members", {
        body: {
          email: newEmail.trim(),
          display_name: newName.trim() || newEmail.split("@")[0],
          role: newRole,
        },
      });
      if (err) {
        setError("Save failed.");
        return;
      }
      setNewEmail("");
      setNewName("");
      setAdding(false);
      await load();
    } catch {
      setError("Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove(id: string) {
    try {
      await apiClient.DELETE("/pro/team/members/{member_id}", {
        params: { path: { member_id: id } },
      });
      await load();
    } catch {
      // silent
    }
  }

  if (!isProUser) return null;

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#d4893a]">
            Your Team
          </div>
          <h3 className="text-[15px] font-bold text-white">
            Invite people to your HedgeSpark workspace
          </h3>
        </div>
        {!adding && (
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-1 text-[11px] font-semibold text-slate-300 transition-colors hover:border-white/[0.2] hover:text-white"
          >
            + Invite
          </button>
        )}
      </div>

      {adding && (
        <div className="mb-4 rounded-xl border border-white/[0.06] bg-white/[0.03] p-3">
          <div className="mb-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
            <input
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              placeholder="teammate@example.com"
              className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200 placeholder-slate-600"
            />
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Display name (optional)"
              className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200 placeholder-slate-600"
            />
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value)}
              className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200"
            >
              <option value="viewer">Viewer (read-only)</option>
              <option value="editor">Editor (can act)</option>
              <option value="admin">Admin (full access)</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="rounded-md bg-[#d4893a] px-3 py-1 text-[11px] font-bold text-white transition-colors hover:bg-[#e8a04e] disabled:opacity-50"
            >
              {saving ? "Saving…" : "Add"}
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setError(null); }}
              className="rounded-md px-3 py-1 text-[11px] text-slate-400 hover:text-slate-200"
            >
              Cancel
            </button>
          </div>
          {error && <div className="mt-1 text-[10px] text-rose-400">{error}</div>}
        </div>
      )}

      {loading ? (
        <div className="animate-pulse space-y-2">
          <div className="h-10 rounded bg-white/[0.04]" />
          <div className="h-10 rounded bg-white/[0.04]" />
        </div>
      ) : members.length === 0 ? (
        <p className="text-[12px] text-slate-400">
          Just you for now. Invite teammates to share the dashboard.
        </p>
      ) : (
        <div className="space-y-2">
          {members.map((m) => {
            const roleMeta = ROLE_LABELS[m.role] || { label: m.role, color: "#94a3b8" };
            return (
              <div
                key={m.id}
                className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.04] bg-white/[0.015] p-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12px] font-semibold text-slate-200">
                    {m.display_name || m.email}
                  </div>
                  <div className="truncate text-[10px] text-slate-400">{m.email}</div>
                </div>
                <span
                  className="flex-shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-bold"
                  style={{
                    color: roleMeta.color,
                    background: roleMeta.color + "15",
                    border: `1px solid ${roleMeta.color}30`,
                  }}
                >
                  {roleMeta.label}
                </span>
                <button
                  type="button"
                  onClick={() => handleRemove(m.id)}
                  className="flex-shrink-0 text-[11px] text-slate-400 hover:text-rose-400"
                  title="Remove member"
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
