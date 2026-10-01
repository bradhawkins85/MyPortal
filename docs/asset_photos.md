# Asset photo policy

Asset photos belong to the canonical asset and its company. Archiving, restoring,
editing, or reconciling an asset does not change its ID and therefore does not
detach its photos. Permanent asset deletion cascades the database records; normal
photo deletion removes both the private original and thumbnail.

Photos are internal by default. A technician with asset write permission may
explicitly mark an individual photo as customer-visible. Every original and
thumbnail request re-checks current company membership, asset visibility, and
that per-photo setting; storage files are never exposed as static URLs.

Uploads are limited to 15 MB and 40 megapixels. The server identifies JPEG, PNG,
and WebP content with Pillow, applies EXIF orientation, and re-encodes it as JPEG.
Re-encoding strips embedded metadata and executable payloads. Random server-side
names and resolved-path checks prevent filename and path traversal attacks.
Client-generated idempotency keys make retries safe after an interrupted or
uncertain response. Audit records contain IDs, byte counts, ordering, and
visibility only; image contents and captions are not logged.
