# Companies API

## List company assets

`GET /api/companies/{company_id}/assets` returns the hardware assets that belong to the specified company. The endpoint is restricted to super administrators and is primarily consumed by the admin ticket workspace when technicians link assets to tickets.

### Query parameters

None. The company is identified by the path segment.

### Response

The response is an array of asset records ordered alphabetically by name. Each record can include identifiers from Syncro and Tactical RMM along with hardware metadata.

```json
[
  {
    "id": 12,
    "company_id": 7,
    "name": "FS-LAPTOP-03",
    "type": "Laptop",
    "serial_number": "ABC123456",
    "status": "active",
    "os_name": "Windows 11 Pro",
    "cpu_name": "Intel Core i7-1255U",
    "ram_gb": 16.0,
    "hdd_size": "512 GB",
    "last_sync": "2025-12-23T10:15:00Z",
    "last_user": "alice@example.com",
    "approx_age": 1.5,
    "performance_score": 87.0,
    "warranty_status": "In warranty",
    "warranty_end_date": "2026-06-01",
    "syncro_asset_id": "98765",
    "tactical_asset_id": "dd0c1e28-ffab-4f29-97c8-0f63a5d151f0"
  }
]
```

### Error responses

* `404 Not Found` – Returned when the company does not exist.
* `403 Forbidden` – Returned when the caller is not a super administrator.

### Notes

* Numeric values such as `ram_gb`, `approx_age`, and `performance_score` are normalised to floating-point numbers. Missing values are returned as `null`.
* Timestamps are emitted in UTC ISO-8601 format. The UI is responsible for rendering them in the viewer's local timezone.
