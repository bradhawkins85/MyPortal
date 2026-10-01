import importlib
import sys
import types

import pytest


def _import_fresh_app_package():
    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_app_package_lazy_loads_asgi_app_and_caches_it(monkeypatch):
    marker = object()
    fake_main = types.ModuleType("app.main")
    fake_main.app = marker
    monkeypatch.setitem(sys.modules, "app.main", fake_main)

    package = _import_fresh_app_package()
    assert "app" not in package.__dict__
    assert package.__all__ == ["app"]

    exported = package.app
    assert exported is marker
    assert package.app is marker


def test_app_package_unknown_attribute_raises_attribute_error(monkeypatch):
    fake_main = types.ModuleType("app.main")
    fake_main.app = object()
    monkeypatch.setitem(sys.modules, "app.main", fake_main)

    package = _import_fresh_app_package()

    with pytest.raises(AttributeError):
        package.missing_export
