# Vendored htmx

MyPortal serves **htmx 1.9.10** from this directory so authenticated pages do
not execute JavaScript supplied at request time by a third-party CDN.

| File | Upstream package path | SHA-256 |
| --- | --- | --- |
| `htmx-1.9.10.min.js` | `htmx.org@1.9.10/dist/htmx.min.js` | `b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e` |

The upstream BSD 2-Clause license is retained in `LICENSE`.

## Updating

1. Select a fixed htmx release after reviewing its release notes and source.
   Never use a floating version, branch, or CDN URL in a template.
2. Download `dist/htmx.min.js` from the release package. Fetch the same fixed
   package path from two independent registries (for example, unpkg and
   jsDelivr) and use `cmp` to confirm that the files are identical.
3. Review the diff from the currently approved asset, rename the file to
   `htmx-<version>.min.js`, and calculate its digest with
   `sha256sum htmx-<version>.min.js`.
4. Update the version, filename, and SHA-256 in this document, the script path
   in `app/templates/base.html`, and the expected digest in
   `tests/test_frontend_dependencies.py`. Retain the matching upstream license.
5. Run the dependency and CSP tests below, then exercise an htmx-backed page
   in a browser. Remove the previous asset only after all references move to
   the reviewed version.

```shell
pytest -q tests/test_frontend_dependencies.py tests/test_security_headers.py
```

## Third-party script review

The authenticated Jinja templates were checked when this asset was added.
Their script elements use same-origin static assets; htmx was the only direct
third-party script include in the shared authenticated layout. The CSP still
lists narrowly scoped external origins for optional calendar, analytics, and
reCAPTCHA functionality. Those are not the source of the portal's htmx build.
The tray support form emits Google's reCAPTCHA script only when that optional
challenge is configured; it is outside the authenticated portal template.
Dynamic analytics sources continue to pass the middleware's CSP-source
validation before being added.
