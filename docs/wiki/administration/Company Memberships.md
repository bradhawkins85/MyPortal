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

## Job title access rules

In **Edit company → Assign staff access**, the company is always the company being edited. Select either a user or a job title, then choose a membership role and staff access level. Job titles come from this company's staff records. Saved rules can be edited or removed in the same section.

Rules apply to enabled staff who are not marked as ex-staff and whose portal account belongs to that company. Titles are matched without regard to case or surrounding spaces. Access is resolved from the current staff record on each permission check, including for newly registered staff and existing sessions after a title change. A matching job title rule takes precedence over an individual role and staff access level; invited or suspended memberships are not activated by a rule. Removing a rule or changing to an unmapped title restores any individually assigned access, otherwise the inherited access ends.

`POST /admin/companies/assign` accepts `sourceCompanyId`, either `userId` or `jobTitle`, `role_id`, and `staffPermission`. A job title requires a valid role. To remove a rule, submit `sourceCompanyId`, `jobTitle`, and `removeJobTitle=1`. Rule changes require a super administrator, retain browser CSRF protection, and are audited. Successful submissions redirect to the edited company.
