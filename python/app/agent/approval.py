"""Per-session tool-call approval gate for the ReAct loop.

Default policy is ``"auto"``: every tool runs immediately and the loop behaves
exactly as before (no pending request, no blocking). When a session is switched
to ``"confirm"``, each tool call the ReAct loop is *about* to make is parked as
a per-session pending request and the loop **blocks** until the user

  * approves the call as-is,
  * approves with edited parameters (the edited params are what actually run),
  * rejects the call,
  * or the request times out / the whole run is cancelled.

This mirrors the tool-confirmation UX of Cherry Studio and similar agent
shells: the human keeps control over side-effecting / heavy tools without
losing the convenience of autonomous mode.

Threading model (mirrors the cooperative-cancellation tokens in ``agent.py``):
the Agent is a singleton that may concurrently serve many sessions, so pending
requests are keyed by ``session_id``. The blocking wait happens on the ReAct
worker thread; the REST endpoints run on FastAPI's thread-pool and resolve the
request through a :class:`threading.Event`. The wait polls at a short interval
so it can honour *both* the approval Event and an external cancel Event / a
timeout responsively, without ever waking the lost thread.

The manager itself is deliberately free of any I/O (no SQLite, no network):
policy persistence is layered on top by the ``Agent`` wrapper, so this module
stays trivially unit-testable with fakes.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("kevrai.approval")

# How long a pending request waits for a human before it is treated as a
# rejection (the无人值守 case must never hang the ReAct loop forever).
DEFAULT_TIMEOUT_SECONDS = 60.0

# Wakeup granularity of the blocking wait. Short enough that timeout / external
# cancel feel near-instant; long enough that the poll loop costs ~nothing.
_POLL_INTERVAL_SECONDS = 0.2

VALID_POLICIES = ("auto", "confirm")


@dataclass
class ApprovalRequest:
    """One in-flight (or resolved) tool-approval request.

    The ``event`` is set exactly once, when the request reaches a terminal
    state (approve / edit / reject / timeout / cancel). The waiting ReAct loop
    blocks on it; the REST layer calls :meth:`ApprovalManager.approve` /
    :meth:`ApprovalManager.reject` to set it.
    """

    id: str
    session_id: str
    tool_name: str
    tool_params: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    mono: float = field(default_factory=time.monotonic)
    decided: bool = False
    # One of: "" (pending), "approve", "edit", "reject", "timeout", "cancel".
    decision: str = ""
    # When the user approves with edited parameters, these are the params the
    # tool actually runs with. ``None`` means "run the original parameters".
    effective_params: dict[str, Any] | None = None
    event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        """JSON-serialisable view for the REST layer / renderer."""
        return {
            "id": self.id,
            "session_id": self.session_id,
            "tool_name": self.tool_name,
            "tool_params": self.tool_params,
            "created_at": self.created_at,
            "decided": self.decided,
            "decision": self.decision,
        }


@dataclass
class GateResult:
    """What the approval gate decided about one tool call."""

    # True -> run the tool (with ``effective_params``); False -> do NOT run it.
    approved: bool
    # The parameters the tool should actually run with (original or edited).
    effective_params: dict[str, Any]
    # Why: "auto" (no gate), "approve", "edit", "reject", "timeout", "cancel".
    reason: str


class ApprovalManager:
    """Owns per-session approval policy, pending requests and the blocking gate.

    All public methods are thread-safe; the blocking :meth:`gate` is the only
    one that parks the caller's thread.
    """

    def __init__(self, default_timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._lock = threading.Lock()
        # request_id -> request. Resolved entries are pruned on resolution so
        # this never grows unbounded.
        self._requests: dict[str, ApprovalRequest] = {}
        # session_id -> the single request_id currently pending for that session
        # (a ReAct loop blocks on its first pending call, so at most one can be
        # in flight per session).
        self._active_by_session: dict[str, str] = {}
        # session_id -> "auto" | "confirm". Missing == "auto".
        self._policies: dict[str, str] = {}
        self.default_timeout = float(default_timeout)
        # Optional streaming hook: callback(event_name, payload_dict). Wired up
        # by the websocket to push approval_request / approval_resolved frames.
        self._callback: Callable[[str, dict[str, Any]], None] | None = None

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------
    def set_policy(self, session_id: str, mode: str) -> str:
        """Set this session's policy (``"auto"`` or ``"confirm"``).

        Raises ``ValueError`` for anything else. Returns the normalised mode.
        """
        mode = (mode or "auto").strip().lower()
        if mode not in VALID_POLICIES:
            raise ValueError(f"invalid approval policy: {mode!r}")
        with self._lock:
            self._policies[session_id] = mode
        return mode

    def get_policy(self, session_id: str) -> str:
        with self._lock:
            return self._policies.get(session_id, "auto")

    def load_policies(self, mapping: dict[str, str]) -> None:
        """Bulk-load persisted policies (best-effort; unknown modes ignored)."""
        with self._lock:
            for sid, mode in mapping.items():
                if mode in VALID_POLICIES:
                    self._policies[sid] = mode

    # ------------------------------------------------------------------
    # Streaming callback
    # ------------------------------------------------------------------
    def set_callback(self, cb: Callable[[str, dict[str, Any]], None] | None) -> None:
        self._callback = cb

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        cb = self._callback
        if cb is None:
            return
        try:
            cb(event, payload)
        except Exception:  # noqa: BLE001 - streaming is best-effort
            log.warning("approval: streaming callback failed for %s", event, exc_info=True)

    # ------------------------------------------------------------------
    # Inspection (used by the REST layer)
    # ------------------------------------------------------------------
    def list_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            pending = [r for r in self._requests.values() if not r.decided]
            pending.sort(key=lambda r: r.created_at)
            return [r.public() for r in pending]

    def get(self, request_id: str) -> ApprovalRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    # ------------------------------------------------------------------
    # Resolving a pending request (called from the REST layer)
    # ------------------------------------------------------------------
    def approve(
        self,
        request_id: str,
        edited_params: dict[str, Any] | None = None,
    ) -> ApprovalRequest | None:
        """Approve a pending request, optionally with edited parameters.

        Returns the resolved request, or ``None`` when the id is unknown or the
        request was already resolved (the caller should map that to a 404).
        """
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.decided:
                return None
        if isinstance(edited_params, dict) and edited_params:
            effective: dict[str, Any] | None = dict(edited_params)
            decision = "edit"
        else:
            effective = None
            decision = "approve"
        self._resolve(req, decision=decision, effective_params=effective)
        return req

    def reject(self, request_id: str) -> ApprovalRequest | None:
        """Reject a pending request. Returns the request, or ``None`` if unknown
        / already resolved."""
        with self._lock:
            req = self._requests.get(request_id)
            if req is None or req.decided:
                return None
        self._resolve(req, decision="reject", effective_params=None)
        return req

    def _resolve(
        self,
        req: ApprovalRequest,
        decision: str,
        effective_params: dict[str, Any] | None,
    ) -> None:
        """Transition a request to a terminal state exactly once."""
        with self._lock:
            if req.decided:
                return
            req.decided = True
            req.decision = decision
            req.effective_params = effective_params
            self._active_by_session.pop(req.session_id, None)
            # Prune so the pending map never grows unbounded across a long day.
            self._requests.pop(req.id, None)
        # Release the waiting ReAct thread and notify the UI *after* dropping
        # the lock, so a streaming callback that re-enters the manager can't
        # deadlock.
        req.event.set()
        self._emit("approval_resolved", req.public())

    # ------------------------------------------------------------------
    # The gate (called from the ReAct loop, on the worker thread)
    # ------------------------------------------------------------------
    def gate(
        self,
        session_id: str,
        tool_name: str,
        tool_params: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> GateResult:
        """Decide whether a tool call may run.

        ``"auto"`` policy: returns immediately with ``approved=True`` and the
        original parameters (no state touched — preserves the pre-approval
        behaviour exactly).

        ``"confirm"`` policy: parks the call as a pending request and blocks the
        calling thread until it is resolved. A timeout and an external cancel
        both resolve it gracefully. The returned :class:`GateResult` tells the
        loop whether to run the tool (and with which params) or to treat the
        call as not-approved and feed the model a "用户未批准" observation.
        """
        policy = self.get_policy(session_id)
        if policy == "auto":
            return GateResult(True, dict(tool_params), "auto")

        req = ApprovalRequest(
            id=uuid.uuid4().hex[:12],
            session_id=session_id,
            tool_name=tool_name,
            tool_params=dict(tool_params),
        )
        with self._lock:
            # Defensive: drop a stale pending entry for this session (shouldn't
            # happen — the loop blocks on the first one — but never leak it).
            old = self._active_by_session.get(session_id)
            if old and old in self._requests:
                self._requests.pop(old, None)
            self._requests[req.id] = req
            self._active_by_session[session_id] = req.id

        self._emit("approval_request", req.public())

        # Block until resolved, polling so we can honour timeout + external
        # cancel. event.wait returns True the moment _resolve() sets it.
        timeout = self.default_timeout
        while not req.event.wait(timeout=_POLL_INTERVAL_SECONDS):
            if cancel_event is not None and cancel_event.is_set():
                self._resolve(req, decision="cancel", effective_params=None)
                break
            if time.monotonic() - req.mono >= timeout:
                self._resolve(req, decision="timeout", effective_params=None)
                break

        if req.decision in ("approve", "edit"):
            effective = req.effective_params if req.effective_params is not None else dict(tool_params)
            return GateResult(True, effective, req.decision)
        # reject / timeout / cancel -> the tool must NOT run.
        return GateResult(False, dict(tool_params), req.decision or "reject")
