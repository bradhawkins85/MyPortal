# Shop packages

The shop now supports curated product bundles that simplify ordering for common workflows. Packages are managed from **Admin → Shop → Package admin** and surface to end users on the `/shop/packages` page.

## Key behaviours

- Package price is calculated automatically as the sum of the current product prices in the bundle. VIP companies see their negotiated pricing when browsing packages.
- Available stock is derived from the lowest stocked item after considering its quantity within the package. If any product runs out, the package is unavailable for ordering.
- Packages respect existing per-company product exclusions.
- When a package is added to the cart each underlying product is inserted with the appropriate quantity, preserving stock validation and checkout workflows.

## Administration endpoints

The following form-driven endpoints are exposed for super administrators and documented in the internal Swagger UI:

- `GET /admin/shop/packages` – list and search packages, toggle archived bundles, and link to the detail editor.
- `GET /admin/shop/packages/{package_id}` – view package metrics, edit metadata, and manage bundled products.
- `POST /shop/admin/package` – create a new package by supplying a name, SKU, and optional description.
- `POST /shop/admin/package/{package_id}/update` – update metadata and archive status.
- `POST /shop/admin/package/{package_id}/archive` – archive or restore a package from the list view.
- `POST /shop/admin/package/{package_id}/delete` – permanently remove a package and its associations.
- `POST /shop/admin/package/{package_id}/items/add` – attach a product to the package with a quantity per bundle.
- `POST /shop/admin/package/{package_id}/items/{product_id}/update` – adjust the quantity of an existing package item.
- `POST /shop/admin/package/{package_id}/items/{product_id}/remove` – remove a product from the package.

All routes enforce super-admin access and automatically emit audit-friendly log entries via the existing logging framework.

## Front-of-house experience

Users with shop access can browse `/shop/packages` for available bundles. The page follows the standard three-panel layout with filtering, stock badges, and quick add-to-cart forms. Packages obey company restrictions, and availability reflects current stock in real time.

When testing or seeding data remember to run the new SQL migration `086_shop_packages.sql` so that the required tables exist. The migration runner executes automatically on application start.
