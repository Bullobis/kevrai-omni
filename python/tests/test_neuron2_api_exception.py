"""Regression tests for Neuron-API slice 2 — Task 2: global exception handler.

Endpoints that raise a plain (non-HTTPException) error must return a uniform
JSON 500 envelope instead of Starlette's default plain-text body, and must
not leak file paths / stack frames / tokens. HTTPException behavior is
preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as app_main  # noqa: E402


def test_uncaught_exception_returns_json_500():
    """A route that raises a plain exception → JSON 500, not plain text."""
    secret_path = "/home/user/secret/token_path.py"  # must NOT leak

    @app_main.app.get("/api/_neuron2_test/_boom")
    def _boom():
        raise RuntimeError(f"kaboom at {secret_path}")

    try:
        # raise_server_exceptions=False so the handler's 500 reaches us
        # instead of re-raising in the test thread.
        client = TestClient(app_main.app, raise_server_exceptions=False)
        r = client.get("/api/_neuron2_test/_boom")
        assert r.status_code == 500
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct, f"expected JSON content-type, got {ct!r}"
        body = r.json()
        assert body.get("error_type") == "internal_error"
        assert "error" in body
    finally:
        # Clean up the test route so it doesn't leak into other tests.
        app_main.app.router.routes = [
            r for r in app_main.app.router.routes
            if getattr(r, "path", None) != "/api/_neuron2_test/_boom"
        ]


def test_uncaught_exception_does_not_leak_sensitive_info():
    """The JSON body must not contain paths, traceback, or tokens."""
    secret = "hf_VERYSECRETTOKEN_12345"
    leaky_path = "/etc/passwd"

    @app_main.app.get("/api/_neuron2_test/_leak")
    def _leak():
        try:
            raise ValueError(f"token={secret} at {leaky_path}")
        except ValueError as e:
            raise RuntimeError(str(e)) from e

    try:
        client = TestClient(app_main.app, raise_server_exceptions=False)
        r = client.get("/api/_neuron2_test/_leak")
        assert r.status_code == 500
        text = r.text
        assert secret not in text, "token leaked in error response"
        assert leaky_path not in text, "file path leaked in error response"
        assert "Traceback" not in text, "stack trace leaked"
        assert "RuntimeError" not in text, "exception class leaked"
    finally:
        app_main.app.router.routes = [
            r for r in app_main.app.router.routes
            if getattr(r, "path", None) != "/api/_neuron2_test/_leak"
        ]


def test_http_exception_keeps_default_detail_format():
    """HTTPException must still produce the default ``{'detail': ...}`` body."""
    @app_main.app.get("/api/_neuron2_test/_missing")
    def _missing():
        raise HTTPException(status_code=404, detail="model xyz not found")

    try:
        client = TestClient(app_main.app, raise_server_exceptions=False)
        r = client.get("/api/_neuron2_test/_missing")
        assert r.status_code == 404
        body = r.json()
        assert "detail" in body, f"HTTPException lost its detail field: {body}"
        assert body["detail"] == "model xyz not found"
        # Must NOT be the internal_error envelope.
        assert body.get("error_type") != "internal_error"
    finally:
        app_main.app.router.routes = [
            r for r in app_main.app.router.routes
            if getattr(r, "path", None) != "/api/_neuron2_test/_missing"
        ]
