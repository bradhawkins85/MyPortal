# Vendored xterm.js

MyPortal serves **xterm.js 5.5.0** and its **fit addon 0.10.0** from this
directory so the RMM live tools terminal does not load JavaScript from a
third-party CDN onto authenticated pages.

| File | Upstream package | SHA-256 |
| --- | --- | --- |
| `xterm-5.5.0.js` | `@xterm/xterm@5.5.0/lib/xterm.js` | see `tests/test_frontend_dependencies.py` |
| `xterm-5.5.0.css` | `@xterm/xterm@5.5.0/css/xterm.css` | see `tests/test_frontend_dependencies.py` |
| `xterm-addon-fit-0.10.0.js` | `@xterm/addon-fit@0.10.0/lib/addon-fit.js` | see `tests/test_frontend_dependencies.py` |

The trailing `//# sourceMappingURL=` comment is removed; the bytes are
otherwise upstream. The upstream MIT license is retained in `LICENSE`.

## Updating

Follow the same process as `app/static/vendor/htmx/README.md`: pin a fixed
release, fetch the package from two registries and `cmp` them, review the
diff, record the new digests here and in `tests/test_frontend_dependencies.py`,
run the dependency and CSP tests, and exercise the live tools terminal.
