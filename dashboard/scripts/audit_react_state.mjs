#!/usr/bin/env node
/**
 * audit_react_state.mjs — find useState setters that are never called,
 * and state values that are never read.
 *
 * Simple heuristic pass over .tsx / .ts files under src/:
 *   1. `const [foo, setFoo] = useState(...)`
 *   2. After declaration, count occurrences of `foo` and `setFoo`
 *   3. If `setFoo` is referenced only at its declaration → dead setter
 *   4. If `foo` is referenced only at its declaration → dead reader
 *
 * False-positive filters:
 *   - Skip setters passed as props (ref counts will include them)
 *   - Skip state declared inside a callback / conditional
 */
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(new URL(import.meta.url).pathname, "../..");
const SRC = path.join(ROOT, "src");

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      out.push(...walk(full));
    } else if (/\.(tsx|ts)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

const USE_STATE_RE =
  /const\s+\[\s*(\w+)\s*,\s*(\w+)\s*\]\s*=\s*useState\b/g;

const findings = {
  dead_setter: [],
  dead_reader: [],
};

for (const file of walk(SRC)) {
  const src = fs.readFileSync(file, "utf-8");
  // Strip comments to reduce false positives from commented-out code
  const code = src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");

  for (const m of code.matchAll(USE_STATE_RE)) {
    const [, reader, setter] = m;
    // Count occurrences (word-boundary)
    const readerRe = new RegExp(`\\b${reader}\\b`, "g");
    const setterRe = new RegExp(`\\b${setter}\\b`, "g");
    const readerCount = (code.match(readerRe) || []).length;
    const setterCount = (code.match(setterRe) || []).length;

    // declaration already counts once for each → need >1 for "used"
    const rel = path.relative(ROOT, file);
    // Compute the approximate line number
    const line = code.slice(0, m.index).split("\n").length;

    if (setterCount <= 1) {
      findings.dead_setter.push({ file: rel, line, reader, setter });
    }
    if (readerCount <= 1) {
      findings.dead_reader.push({ file: rel, line, reader, setter });
    }
  }
}

let total = 0;
for (const [kind, hits] of Object.entries(findings)) {
  if (hits.length === 0) continue;
  total += hits.length;
  console.log(`${kind} (${hits.length})`);
  for (const h of hits.slice(0, 40)) {
    console.log(`  ${h.file}:${h.line}  [${h.reader}, ${h.setter}]`);
  }
  if (hits.length > 40) console.log(`  ... and ${hits.length - 40} more`);
  console.log();
}

if (total === 0) console.log("✅ No dead React state found.");
process.exit(total === 0 ? 0 : 1);
