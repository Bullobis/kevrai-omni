"""Regression tests for sources._host_of.

Guards against the historical bug where `lstrip("www.")` was used instead of
`removeprefix("www.")`.  `lstrip` strips a *character set*, so a host like
`wandb.ai` would be mangled into `andb.ai`.  `removeprefix` strips the exact
literal prefix only.
"""

from __future__ import annotations

import pytest

from app.sources import _host_of


class TestHostOfBasic:
    def test_strips_www_prefix(self):
        assert _host_of("https://www.example.com/path") == "example.com"

    def test_no_www_unchanged(self):
        assert _host_of("https://example.com/path") == "example.com"

    def test_lowercases_host(self):
        assert _host_of("https://WWW.Example.COM/") == "example.com"

    def test_with_port_keeps_port_stripped_from_hostname(self):
        # urlparse.hostname drops the port; that is expected.
        assert _host_of("https://www.example.com:8080/path") == "example.com"

    def test_with_path_and_query(self):
        assert _host_of("https://www.huggingface.co/repo/file?rev=main") == "huggingface.co"


class TestHostOfRegressionLstripBug:
    """The critical regression: hosts starting with 'w' must not be mangled."""

    def test_wandb_ai_not_mangled(self):
        # lstrip("www.") would turn "wandb.ai" into "andb.ai".
        assert _host_of("https://wandb.ai/path") == "wandb.ai"

    def test_www_wandb_ai(self):
        assert _host_of("https://www.wandb.ai/path") == "wandb.ai"

    def test_host_starting_with_multiple_w(self):
        # "wwww.example.com" → removeprefix removes exactly one "www."
        assert _host_of("https://wwww.example.com/") == "wwww.example.com".removeprefix("www.")

    def test_host_that_is_just_www(self):
        # "www" has no "." so removeprefix does nothing.
        assert _host_of("https://www/") == "www"


class TestHostOfEdgeCases:
    def test_empty_string(self):
        assert _host_of("") == ""

    def test_none_scheme_relative(self):
        # urlparse of "//host" has hostname.
        assert _host_of("//www.example.com/path") == "example.com"

    def test_invalid_url_returns_empty(self):
        # A string that urlparse cannot extract a hostname from.
        assert _host_of("not a url at all") == ""

    def test_hf_mirror_host(self):
        assert _host_of("https://hf-mirror.com/repo/resolve/main/file") == "hf-mirror.com"

    def test_modelscope_host(self):
        assert _host_of("https://www.modelscope.cn/models/x/y") == "modelscope.cn"
