"""Regression test for _host_of().

The old implementation used ``h.lower().lstrip("www.")``, which strips
every character in the set {w, .} — so ``www.world.com`` was mangled to
``orld.com``, breaking any host that starts with ``w`` or ``.``. This
test pins the correct behaviour.
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sources import _host_of  # noqa: E402


def test_strips_single_www_prefix():
    assert _host_of("https://www.world.com/") == "world.com"


def test_no_www_prefix():
    assert _host_of("https://world.com/") == "world.com"


def test_double_www_only_strips_once():
    # Only one leading www. should be stripped.
    assert _host_of("https://www.www.org/") == "www.org"


def test_host_starting_with_w_not_mangled():
    # The bug: lstrip("www.") would strip the leading w, yielding "orld.com".
    assert _host_of("https://www.world.com/") != "orld.com"
    assert _host_of("https://www.web.dev/") == "web.dev"
    assert _host_of("https://world.example.com/") == "world.example.com"


def test_host_starting_with_dot_preserved():
    # The bug: lstrip would strip a leading dot from hosts.
    assert _host_of("https://.odd.com/") == ".odd.com"


def test_uppercase_is_lowered():
    assert _host_of("https://WWW.WORLD.COM/") == "world.com"


def test_malformed_url_returns_empty():
    assert _host_of("") == ""
    assert _host_of("not a url") == ""
