# Admin company membership endpoints

The admin portal exposes a collection of POST endpoints that allow super administrators to manage staff access to customer companies. All requests must include a valid authenticated session with super administrator privileges and a CSRF token when invoked from browser contexts.

| Endpoint | Description |
| --- | --- |
| `POST /admin/companies/assignment/{company_id}/{user_id}/permission` | Toggle granular company permissions such as licence ordering or shop access for an existing membership. |
| `POST /admin/companies/assignment/{company_id}/{user_id}/staff-permission` | Adjust the staff permission level that governs knowledge base and ticket capabilities for an assigned user. |
| `POST /admin/companies/assignment/{company_id}/{user_id}/role` | Update the membership role associated with the company assignment. |
| `POST /admin/companies/assignment/{company_id}/{user_id}/remove` | Remove an active membership. The user immediately loses access to the company. |
| `POST /admin/companies/assignment/{company_id}/{staff_id}/pending/remove` | Cancel a pending staff access assignment before the staff member completes portal sign-up. |

All endpoints return a JSON payload with a `success` flag on completion. Validation errors return `400 Bad Request`, missing resources return `404 Not Found`, and insufficient privileges return `303 See Other` redirects to the login screen.
