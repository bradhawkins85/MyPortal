import importlib
import sys
import types

import pytest


def _import_fresh_app_package(monkeypatch):
    # delitem restores the original package afterwards; a lingering fresh
    # ``app`` module has no ``app.services``/``app.main`` attributes and
    # breaks every later string-target monkeypatch.
    monkeypatch.delitem(sys.modules, "app", raising=False)
    return importlib.import_module("app")


def test_app_package_lazy_loads_asgi_app_and_caches_it(monkeypatch):
    marker = object()
    fake_main = types.ModuleType("app.main")
    fake_main.app = marker
    monkeypatch.setitem(sys.modules, "app.main", fake_main)

    package = _import_fresh_app_package(monkeypatch)
    assert "app" not in package.__dict__
    assert package.__all__ == ["app"]

    exported = package.app
    assert exported is marker
    assert package.app is marker


def test_app_package_unknown_attribute_raises_attribute_error(monkeypatch):
    fake_main = types.ModuleType("app.main")
    fake_main.app = object()
    monkeypatch.setitem(sys.modules, "app.main", fake_main)

    package = _import_fresh_app_package(monkeypatch)

    with pytest.raises(AttributeError):
        package.missing_export
