# Orders API

The Orders API exposes read and management endpoints for customer purchase orders.
All timestamps are returned in ISO 8601 format (UTC). Provide the `companyId`
query parameter for every request to scope results to a specific customer.

## List orders

`GET /api/orders?companyId={companyId}`

Returns the recent order summaries for the specified company. The authenticated
user must either be a super admin or a company member with the `can_access_orders`
permission.

**Response example**

```json
[
  {
    "orderNumber": "ORD123456789012",
    "companyId": 42,
    "status": "pending",
    "shippingStatus": "pending",
    "poNumber": "PO-0001",
    "consignmentId": null,
    "orderDate": "2025-01-01T10:15:00+00:00",
    "eta": "2025-01-04T09:00:00+00:00"
  }
]
```

## Retrieve order detail

`GET /api/orders/{orderNumber}?companyId={companyId}`

Returns the full order payload, including the individual line items. The same
permission checks as the list endpoint apply.

**Response example**

```json
{
  "orderNumber": "ORD123456789012",
  "companyId": 42,
  "status": "processing",
  "shippingStatus": "shipped",
  "poNumber": "PO-0001",
  "consignmentId": "CONSIGN-42",
  "orderDate": "2025-01-01T10:15:00+00:00",
  "eta": "2025-01-04T09:00:00+00:00",
  "items": [
    {
      "id": 99,
      "productId": 12,
      "productName": "Example Product",
      "quantity": 3,
      "price": "149.99",
      "status": "processing",
      "shippingStatus": "shipped"
    }
  ]
}
```

## Update an order

`PATCH /api/orders/{orderNumber}?companyId={companyId}`

Allows super administrators to update an order's metadata. Supported fields:

- `status`
- `shippingStatus`
- `notes`
- `poNumber`
- `consignmentId`
- `eta`

Provide only the fields that need to change. The response mirrors the detail
endpoint and includes the updated order state.
