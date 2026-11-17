// Pure helper: unwrap the sidecarFetch envelope for /api/engines.
//
// Regression (v4.18.2, found by live GGUF chat verification):
// sidecarFetch resolves {status, body}; findLlamaServerBinary read
// `st.engines` directly, so the engine list was ALWAYS empty and every
// llama.cpp chat failed with "引擎未安装" even with the engine installed.

/**
 * @param {{status?:number, body?:any}|any[]|null|undefined} st
 * @returns {any[]} engine records
 */
function engineRecordsFromFetch(st) {
  const payload = (st && typeof st === "object" && "body" in st) ? st.body : st;
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.engines)) return payload.engines;
  return [];
}

module.exports = { engineRecordsFromFetch };
