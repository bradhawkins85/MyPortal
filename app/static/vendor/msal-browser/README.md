# Vendored MSAL.js

MyPortal serves **@azure/msal-browser 4.30.0** from this directory for the
Outlook signature add-in, which signs users in with nested app authentication
(`createNestablePublicClientApplication`). The add-in bundle served from
`/outlook-addin/{id}/launch.js` inlines this file, because Classic Outlook on
Windows runs event handlers in a JavaScript-only runtime that cannot load
further scripts.

| File | Upstream package path | SHA-256 |
| --- | --- | --- |
| `msal-browser-4.30.0.min.js` | `@azure/msal-browser@4.30.0/lib/msal-browser.min.js` | `2b580165064a6a4ec041ebd44832299172688a23a42c3b81d65314799dba04eb` |

The package tarball matched the npm registry's published integrity
(`sha512-HBBKfbZkMVzzF5bofvS1cXuNHFVc+gt4/HOnCmG/0hsHuZRJvJvDg/+7nTwIpoqvJc8BQp5o23rBUfisOLxR+w==`).
The upstream MIT license is retained in `LICENSE`.

## Updating

1. Select a fixed release after reviewing its release notes. Never use a
   floating version or CDN URL.
2. `npm pack @azure/msal-browser@<version>`, confirm the tarball's SHA-512
   matches `npm view @azure/msal-browser@<version> dist.integrity`, and where
   possible compare `lib/msal-browser.min.js` with a second registry using `cmp`.
3. Rename the file to `msal-browser-<version>.min.js`, update this document,
   `MSAL_BUNDLE` in `app/services/m365_signature_addin.py` and the expected
   digest in `tests/test_m365_signature_addin.py`.
