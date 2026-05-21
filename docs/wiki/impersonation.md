# Administrator impersonation

Super administrators can temporarily impersonate any user with company-level permissions. This feature makes it easier to audit
role assignments, troubleshoot access issues, and validate theme visibility without exchanging credentials.

## Starting an impersonation session

Use the **Administration ▸ Impersonation** workspace to search for eligible accounts. The table is filterable and sortable, and
summarises each user's company roles. Selecting **Impersonate** issues a POST request to `/auth/impersonate` and switches the
browser session to the selected user.

```http
POST /auth/impersonate
Content-Type: application/json
X-CSRF-Token: <csrf-token>

{
  "user_id": 42
}
```

Successful responses contain an updated `LoginResponse` payload including the impersonated session metadata.

## Ending impersonation

While impersonating, a red **Exit Impersonation** button appears at the top of the left navigation. Submitting the button (or
calling the API directly) restores the original super administrator session.

```http
POST /auth/impersonation/exit
X-CSRF-Token: <csrf-token>
```

Both APIs are documented in Swagger under the **Auth** tag. Audit logs capture impersonation start and stop events alongside the
original and impersonated session identifiers.
