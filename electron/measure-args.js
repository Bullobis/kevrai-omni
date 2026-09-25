"use strict";
/**
 * Pure argument normalizer for the `measureSources` context-bridge call.
 *
 * Kept in a separate file with NO `electron` import so it can be unit-tested in
 * plain Node (the real preload cannot be imported without Electron).
 *
 * Accepts either:
 *   - a plain array of http(s) URL strings (legacy callers, e.g. the
 *     environment page), or
 *   - an options object `{ urls: string[], force?: boolean, file_size?: number }`
 *     (the downloads page "force remeasure" button).
 *
 * Returns the IPC payload `{ urls, force?, file_size? }`.
 */
function normalizeMeasureArgs(opts) {
  const o = Array.isArray(opts) ? { urls: opts } : opts;
  if (o === null || typeof o !== "object") {
    throw new Error("opts must be an object or an array of urls");
  }
  const { urls } = o;
  if (!Array.isArray(urls)) throw new Error("urls must be an array of strings");
  const clean = [];
  for (const u of urls) {
    if (typeof u !== "string" || !/^https?:\/\//.test(u)) {
      throw new Error("each url must be an http(s) string");
    }
    clean.push(u.slice(0, 2048));
    if (clean.length >= 32) break;
  }
  const payload = { urls: clean };
  if (o.force === true) payload.force = true;
  if (Number.isFinite(o.file_size) && o.file_size > 0) {
    payload.file_size = Math.trunc(o.file_size);
  }
  return payload;
}

module.exports = { normalizeMeasureArgs };
