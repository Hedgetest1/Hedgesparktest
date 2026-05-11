"use client";

/**
 * BiQueryCard — Pro #3 query builder over merchant data.
 *
 * Builder (NOT raw SQL) mode: table dropdown + column multi-select +
 * filter rows + group-by + order-by + LIMIT. Backend reconstructs the
 * SQL server-side from this structured payload with all 8 safety
 * layers (table allowlist, column allowlist, op allowlist, hardcoded
 * tenant filter, parameterized values, row cap, statement_timeout,
 * rate limit). See app/services/bi_query_builder.py.
 *
 * Minimal first cut: no autocomplete chips, no shared queries, no
 * scheduled export. Functional + honest + safe.
 */

import { useEffect, useState } from "react";
import { apiClient } from "@/app/lib/api-client";


type Column = { name: string; label: string; type: string };
type Table = { name: string; label: string; description: string; columns: Column[] };
type Schema = {
  tables: Table[];
  operators: string[];
  aggregations: string[];
  limits: { max_rows: number; default_limit: number };
};

type FilterRow = { column: string; op: string; value: string };

type QueryResult = {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  duration_ms: number;
  truncated: boolean;
};


type SavedQuery = {
  id: number;
  name: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  query_json: any;
  updated_at: string;
};


const OPS_WITH_VALUE = new Set([
  "=", "!=", ">", ">=", "<", "<=", "LIKE", "IN",
]);


function _fmtCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}


export function BiQueryCard({
  apiBase,
  shop,
  isProUser,
}: {
  apiBase: string;
  shop: string;
  isProUser: boolean;
}) {
  const [schema, setSchema] = useState<Schema | null>(null);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const [tableName, setTableName] = useState<string>("");
  const [selectedColumns, setSelectedColumns] = useState<string[]>([]);
  const [filters, setFilters] = useState<FilterRow[]>([]);
  const [limit, setLimit] = useState<number>(100);

  const [result, setResult] = useState<QueryResult | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [savedQueries, setSavedQueries] = useState<SavedQuery[]>([]);
  const [saveName, setSaveName] = useState<string>("");
  const [savingQuery, setSavingQuery] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Fetch schema on mount via typed apiClient
  useEffect(() => {
    if (!apiBase || !shop || !isProUser) return;
    let cancelled = false;
    async function loadSchema() {
      try {
        const { data, error } = await apiClient.GET("/pro/bi/schema");
        if (error || !data) throw new Error("schema load failed");
        if (!cancelled) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const s = data as any as Schema;
          setSchema(s);
          if (s.tables[0]) setTableName(s.tables[0].name);
        }
      } catch (e) {
        if (!cancelled) {
          setSchemaError(
            e instanceof Error ? e.message : "schema load failed"
          );
        }
      }
    }
    void loadSchema();
    return () => {
      cancelled = true;
    };
  }, [apiBase, shop, isProUser]);

  const currentTable = schema?.tables.find((t) => t.name === tableName);
  const availableCols = currentTable?.columns || [];

  // Reset columns + filters when table changes
  useEffect(() => {
    setSelectedColumns([]);
    setFilters([]);
    setResult(null);
    setRunError(null);
  }, [tableName]);

  // Load saved queries list on mount
  useEffect(() => {
    if (!apiBase || !shop || !isProUser) return;
    let cancelled = false;
    async function loadSaved() {
      try {
        const { data } = await apiClient.GET("/pro/bi/saved-queries");
        if (!cancelled && data) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const list = ((data as any).queries || []) as SavedQuery[];
          setSavedQueries(list);
        }
      } catch {
        // silent — saved queries are non-critical
      }
    }
    void loadSaved();
    return () => {
      cancelled = true;
    };
  }, [apiBase, shop, isProUser]);

  async function reloadSavedQueries() {
    try {
      const { data } = await apiClient.GET("/pro/bi/saved-queries");
      if (data) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        setSavedQueries(((data as any).queries || []) as SavedQuery[]);
      }
    } catch {
      // silent
    }
  }

  async function saveCurrentQuery() {
    setSaveError(null);
    const trimmed = saveName.trim();
    if (!trimmed) {
      setSaveError("Name required");
      return;
    }
    if (!tableName || selectedColumns.length === 0) {
      setSaveError("Build a query first (pick table + at least one column)");
      return;
    }
    setSavingQuery(true);
    try {
      const body = {
        name: trimmed,
        query: {
          table: tableName,
          select: selectedColumns.map((c) => ({ column: c })),
          where: filters
            .filter((f) => OPS_WITH_VALUE.has(f.op) ? f.value !== "" : true)
            .map((f) =>
              OPS_WITH_VALUE.has(f.op)
                ? { column: f.column, op: f.op, value: f.value }
                : { column: f.column, op: f.op }
            ),
          limit,
        },
      };
      const { error } = await apiClient.POST("/pro/bi/saved-queries", {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: body as any,
      });
      if (error) {
        setSaveError("Save failed (cap of 50 saved queries per shop?)");
        return;
      }
      setSaveName("");
      await reloadSavedQueries();
    } catch {
      setSaveError("Save failed");
    } finally {
      setSavingQuery(false);
    }
  }

  function loadSavedQuery(q: SavedQuery) {
    const qj = q.query_json || {};
    if (qj.table && typeof qj.table === "string") setTableName(qj.table);
    if (Array.isArray(qj.select)) {
      const cols: string[] = [];
      for (const it of qj.select) {
        if (it && typeof it.column === "string" && !it.agg) {
          cols.push(it.column);
        }
      }
      setSelectedColumns(cols);
    }
    if (Array.isArray(qj.where)) {
      setFilters(
        qj.where.map((f: { column: string; op: string; value?: unknown }) => ({
          column: f.column,
          op: f.op,
          value: f.value !== undefined && f.value !== null ? String(f.value) : "",
        }))
      );
    }
    if (typeof qj.limit === "number") setLimit(qj.limit);
    setResult(null);
    setRunError(null);
  }

  async function deleteSavedQuery(id: number) {
    try {
      await apiClient.DELETE("/pro/bi/saved-queries/{query_id}", {
        params: { path: { query_id: id } },
      });
      await reloadSavedQueries();
    } catch {
      // silent
    }
  }

  function toggleColumn(name: string) {
    setSelectedColumns((prev) =>
      prev.includes(name) ? prev.filter((c) => c !== name) : [...prev, name]
    );
  }

  function addFilter() {
    if (!availableCols[0]) return;
    setFilters((prev) => [
      ...prev,
      { column: availableCols[0].name, op: "=", value: "" },
    ]);
  }

  function updateFilter(idx: number, patch: Partial<FilterRow>) {
    setFilters((prev) =>
      prev.map((f, i) => (i === idx ? { ...f, ...patch } : f))
    );
  }

  function removeFilter(idx: number) {
    setFilters((prev) => prev.filter((_, i) => i !== idx));
  }

  async function runQuery() {
    if (!tableName || selectedColumns.length === 0) {
      setRunError("Pick a table and at least one column to display.");
      return;
    }
    setRunning(true);
    setRunError(null);
    setResult(null);

    const body = {
      table: tableName,
      select: selectedColumns.map((c) => ({ column: c })),
      where: filters
        .filter((f) => OPS_WITH_VALUE.has(f.op) ? f.value !== "" : true)
        .map((f) => {
          if (!OPS_WITH_VALUE.has(f.op)) {
            return { column: f.column, op: f.op };
          }
          return { column: f.column, op: f.op, value: f.value };
        }),
      limit,
    };

    try {
      const { data, error } = await apiClient.POST("/pro/bi/query", {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        body: body as any,
      });
      if (error || !data) throw new Error("query failed");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setResult(data as any as QueryResult);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "query failed");
    } finally {
      setRunning(false);
    }
  }

  if (!isProUser) return null;

  if (schemaError) {
    return (
      <div className="rounded-2xl border border-rose-500/[0.20] bg-rose-500/[0.04] p-5">
        <div className="text-[12px] text-rose-300">
          BI builder couldn’t load schema. Retry in a moment.
        </div>
      </div>
    );
  }

  if (!schema) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <div className="h-32 animate-pulse rounded bg-white/[0.04]" />
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-bold uppercase tracking-[0.18em] text-[#a78bfa]">
            Query builder · Read-only over your data
          </div>
          <h3 className="text-[15px] font-bold text-white">
            Slice your store data — orders, events, nudges
          </h3>
          <p className="mt-1 text-[11px] text-slate-400">
            Builder mode (not raw SQL) · all queries auto-scoped to your store · LIMIT {schema.limits.max_rows}
          </p>
        </div>
      </div>

      {/* Table picker */}
      <div className="mb-4">
        <label className="mb-1 block text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
          Table
        </label>
        <select
          value={tableName}
          onChange={(e) => setTableName(e.target.value)}
          className="w-full rounded-md border border-white/[0.08] bg-[#0b0b14] px-2 py-1 text-[12px] text-slate-200"
        >
          {schema.tables.map((t) => (
            <option key={t.name} value={t.name}>
              {t.label} — {t.description}
            </option>
          ))}
        </select>
      </div>

      {/* Columns */}
      <div className="mb-4">
        <label className="mb-1 block text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
          Columns to display ({selectedColumns.length}/10)
        </label>
        <div className="flex flex-wrap gap-1.5">
          {availableCols.map((c) => {
            const on = selectedColumns.includes(c.name);
            return (
              <button
                key={c.name}
                type="button"
                onClick={() => toggleColumn(c.name)}
                className={`rounded-md border px-2 py-1 text-[11px] font-medium transition-colors ${
                  on
                    ? "border-violet-400/40 bg-violet-500/15 text-violet-200"
                    : "border-white/[0.08] bg-white/[0.02] text-slate-400 hover:border-white/[0.18] hover:text-slate-200"
                }`}
              >
                {c.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4">
        <div className="mb-1 flex items-center justify-between">
          <label className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Filters ({filters.length}/10)
          </label>
          <button
            type="button"
            onClick={addFilter}
            disabled={filters.length >= 10}
            className="rounded-md border border-white/[0.08] bg-white/[0.02] px-2 py-0.5 text-[10px] font-semibold text-slate-300 transition-colors hover:border-white/[0.18] hover:text-white disabled:opacity-40"
          >
            + Add filter
          </button>
        </div>
        <div className="space-y-1.5">
          {filters.map((f, idx) => (
            <div key={idx} className="flex flex-wrap items-center gap-1.5">
              <select
                value={f.column}
                onChange={(e) => updateFilter(idx, { column: e.target.value })}
                className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-1.5 py-0.5 text-[11px] text-slate-200"
              >
                {availableCols.map((c) => (
                  <option key={c.name} value={c.name}>
                    {c.label}
                  </option>
                ))}
              </select>
              <select
                value={f.op}
                onChange={(e) => updateFilter(idx, { op: e.target.value })}
                className="rounded-md border border-white/[0.08] bg-[#0b0b14] px-1.5 py-0.5 text-[11px] text-slate-200"
              >
                {schema.operators.map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>
              {OPS_WITH_VALUE.has(f.op) && (
                <input
                  type="text"
                  value={f.value}
                  onChange={(e) => updateFilter(idx, { value: e.target.value })}
                  placeholder="value"
                  className="w-32 rounded-md border border-white/[0.08] bg-[#0b0b14] px-1.5 py-0.5 text-[11px] text-slate-200 placeholder-slate-600"
                />
              )}
              <button
                type="button"
                onClick={() => removeFilter(idx)}
                className="text-[11px] text-slate-400 hover:text-rose-400"
                aria-label="Remove filter"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Limit + Run */}
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <label className="mb-1 block text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Limit
          </label>
          <input
            type="number"
            min={1}
            max={schema.limits.max_rows}
            value={limit}
            onChange={(e) => setLimit(parseInt(e.target.value || "100", 10))}
            className="w-24 rounded-md border border-white/[0.08] bg-[#0b0b14] px-1.5 py-1 text-[12px] text-slate-200"
          />
        </div>
        <button
          type="button"
          onClick={runQuery}
          disabled={running || !tableName || selectedColumns.length === 0}
          className="rounded-md bg-[#a78bfa] px-4 py-1.5 text-[12px] font-bold text-[#0b0b14] transition-colors hover:bg-[#b8a4ff] disabled:opacity-40"
        >
          {running ? "Running…" : "Run query"}
        </button>
      </div>

      {runError && (
        <div className="mb-4 rounded-md border border-rose-500/[0.20] bg-rose-500/[0.04] p-2 text-[11px] text-rose-300">
          {runError}
        </div>
      )}

      {/* Results */}
      {result && (
        <div className="overflow-hidden rounded-xl border border-white/[0.06]">
          <div className="flex items-center justify-between border-b border-white/[0.06] bg-white/[0.02] px-3 py-2">
            <div className="text-[11px] text-slate-300">
              <span className="font-mono tabular-nums">{result.row_count}</span> rows ·{" "}
              <span className="font-mono tabular-nums">{result.duration_ms}ms</span>
              {result.truncated && (
                <span className="ml-2 rounded-full bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.14em] text-amber-300">
                  Truncated
                </span>
              )}
            </div>
          </div>
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-[#0b0b14]">
                <tr>
                  {result.columns.map((c) => (
                    <th
                      key={c}
                      className="border-b border-white/[0.08] px-3 py-2 text-left font-bold text-slate-300"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, ri) => (
                  <tr
                    key={ri}
                    className="border-b border-white/[0.03] hover:bg-white/[0.015]"
                  >
                    {row.map((cell, ci) => (
                      <td
                        key={ci}
                        className="px-3 py-1.5 font-mono tabular-nums text-slate-300"
                      >
                        {_fmtCell(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Saved queries — list / save / load / delete */}
      <div className="mt-5 border-t border-white/[0.05] pt-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-400">
            Saved queries ({savedQueries.length}/50)
          </div>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="Query name"
              maxLength={128}
              className="w-36 rounded-md border border-white/[0.08] bg-[#0b0b14] px-1.5 py-1 text-[11px] text-slate-200 placeholder-slate-600"
            />
            <button
              type="button"
              onClick={saveCurrentQuery}
              disabled={savingQuery || !saveName.trim()}
              className="rounded-md border border-violet-400/40 bg-violet-500/15 px-2 py-1 text-[11px] font-semibold text-violet-200 transition-colors hover:bg-violet-500/25 disabled:opacity-40"
            >
              {savingQuery ? "Saving…" : "Save current"}
            </button>
          </div>
        </div>
        {saveError && (
          <div className="mb-2 text-[10px] text-rose-300">{saveError}</div>
        )}
        {savedQueries.length === 0 ? (
          <p className="text-[10px] text-slate-400">
            None yet — build a query and click &quot;Save current&quot;.
          </p>
        ) : (
          <div className="space-y-1">
            {savedQueries.map((q) => (
              <div
                key={q.id}
                className="flex items-center justify-between gap-2 rounded-md border border-white/[0.04] bg-white/[0.015] px-2 py-1.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[11px] font-semibold text-slate-200">
                    {q.name}
                  </div>
                  <div className="text-[9px] text-slate-400">
                    {q.query_json?.table || "—"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => loadSavedQuery(q)}
                  className="rounded border border-white/[0.08] px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-white/[0.18] hover:text-white"
                >
                  Load
                </button>
                <button
                  type="button"
                  onClick={() => deleteSavedQuery(q.id)}
                  className="text-[10px] text-slate-400 hover:text-rose-400"
                  aria-label="Delete saved query"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="mt-3 text-[10px] text-slate-400">
        Scheduled email export — coming next sprint.
      </p>
    </div>
  );
}
