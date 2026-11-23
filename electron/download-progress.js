// Pure helper: normalise sidecar download snapshots into the shape the
// renderer consumes.
//
// Regression (v4.18.2, reported after the promo video): single-task snapshots
// emit downloaded_bytes/total_bytes and hub-job rows emit bytes_done/bytes_total,
// but renderer/modules/downloads.js reads downloaded/total. Forwarding a raw
// snapshot left every progress bar at 0% forever.

/**
 * @param {string} taskId
 * @param {any} body
 * @returns {{taskId:string, filename:string, downloaded:number, total:number, status:string}}
 */
function normalizeProgress(taskId, body) {
  const b = (body && typeof body === "object") ? body : {};
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n >= 0 ? n : 0;
  };
  const downloaded = num(b.downloaded ?? b.downloaded_bytes ?? b.bytes_done);
  const total = num(b.total ?? b.total_bytes ?? b.bytes_total);
  return {
    ...b,
    taskId,
    filename: b.filename || b.dest_path || b.path || taskId,
    downloaded,
    total,
    status: String(b.status || b.state || "running").toLowerCase(),
  };
}

module.exports = { normalizeProgress };
