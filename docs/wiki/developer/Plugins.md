# Plugins

MyPortal supports two extension models:

- **Feature packs** (`app/features/<slug>/`) are in-tree modules shipped with core.
- **Plugins** (out-of-tree) live in directories configured by `PLUGIN_DIRS` and are loaded at runtime.

Plugins run as normal Python code inside the app process. Only install plugins from trusted sources.

## Directory layout

Set plugin search paths with:

```env
PLUGIN_DIRS=./plugins
```

Each plugin is a Python package:

```text
plugins/
  hello_world/
    __init__.py
    README.md
```

The package must expose `PACK` (or `get_pack()`) returning `FeaturePack`.

## Plugin manifest contract

Plugins use the same `FeaturePack` contract as feature packs, with extra optional metadata:

- `author`
- `description`
- `homepage`
- `min_app_version`

Plugin slugs must start with `plugin.` (for example `plugin.hello_world`).

## Enable/disable and reload

- Admin UI: **Admin → Feature Packs**
- APIs:
  - `POST /api/plugins/{slug}/enable`
  - `POST /api/plugins/{slug}/disable`
  - `POST /api/features/{slug}/reload`
  - `POST /api/plugins/install` (`plugin_path` or `plugin_zip`)

Enable/disable state is persisted in `plugin_registry`.

## Install flows

### Install from directory

Provide an absolute path to a plugin package directory containing `__init__.py`.

### Install from zip

Upload a zip containing one or more top-level plugin package directories.
Zip extraction rejects unsafe paths containing `../` or absolute paths.

## Writing your first plugin

1. Create a package under one of the `PLUGIN_DIRS`.
2. Define an `APIRouter` and endpoint(s).
3. Export `PACK = FeaturePack(...)` with slug `plugin.<name>`.
4. Enable it in Admin → Feature Packs (or via API).
5. Reload with `POST /api/features/plugin.<name>/reload` after changes.

## Data and services access

Plugins can import and use existing repository/service modules from `app.repositories.*` and `app.services.*`.
Follow normal app patterns for DB access and permissions.

## Background jobs and hooks

Use `startup`, `shutdown`, and `background_jobs` fields on `FeaturePack` for lifecycle behavior.
Background jobs are cancelled on unload/reload.

## Security and limitations

- Plugins are **not sandboxed**.
- Plugins execute with the same privileges as MyPortal.
- Do not install untrusted plugins.
- Hot reload does not replace full deploy flows for framework, dependency, or destructive schema changes.

## Testing plugins

Copy patterns from:

- `app/features/_example_pack/`
- `tests/test_feature_registry.py`

Recommended tests:

1. Load plugin via `FeatureRegistry.load`.
2. Assert route behavior with `TestClient`.
3. Reload plugin and assert behavior still works.
