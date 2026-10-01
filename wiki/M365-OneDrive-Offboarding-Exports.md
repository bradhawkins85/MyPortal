# M365 OneDrive offboarding exports

OneDrive export is a verified copy checkpoint, not a retention backup. The workflow stores the destination folder ID and each asynchronous copy monitor URL in its durable step log. A retry reopens that folder, resumes outstanding monitors, and inventories its immediate children rather than creating another folder or replacing unrelated content.

## Destination and permissions

Configure a SharePoint **document library drive ID** and, optionally, a parent folder item ID. Before copying, MyPortal reads the source drive root, destination drive, and parent folder to validate access and compatibility. The destination must be a SharePoint document library. Copy operation URLs are accepted only over HTTPS from Microsoft Graph or SharePoint hosts so bearer credentials are not sent to arbitrary hosts.

Prefer `Sites.Selected` for a fixed export site when it supports all configured destination operations. It requires both tenant admin consent and an explicit site permission grant for the application; consent alone grants no site access. Source OneDrive reads are a separate limitation: `Sites.Selected` scoped to the export site does not grant access to each user's personal OneDrive. An established broader source permission may therefore still be needed. MyPortal does not automatically grant `Sites.FullControl.All`, `Files.ReadWrite.All`, or site permissions.

## Completion and restart behavior

A `202 Accepted` response is recorded as `in_progress`, never completed. Every non-empty source item requires a valid monitor URL and a successful terminal monitor state. MyPortal refreshes an expired application token once after a monitor `401`, honors bounded `Retry-After` delays for throttling/service unavailability, and then verifies destination names. Empty drives complete after a successful empty inventory.

Missing monitor URLs, failed operations, timeouts, incompatible destinations, and partial inventories fail explicitly. Configure a later destructive step with `depends_on_onedrive_export: true` and a pause failure policy to create a recoverable checkpoint. An operator exception must contain both `authorized_by` and `reason`; a bare boolean cannot bypass the checkpoint.

## Source protection

A sharing invitation with `roles: [read]` does not remove the OneDrive owner's write access and is not used. Select `disable_account` when disabling the Entra account is the intended supported protection, or `none` to report that protection was not applied. Set `require_source_protection` to stop the workflow unless the selected operation succeeds. Disabling an account affects all sign-in, not only OneDrive, and should be selected deliberately.
