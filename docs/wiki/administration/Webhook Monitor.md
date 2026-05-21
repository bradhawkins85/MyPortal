# Webhook Monitor Enhancements

The webhook monitor keeps track of outbound delivery attempts, retry history,
and queue state. The following additions expand the operational tooling:

## Automatic cleanup of delivered events

- Delivered webhook events are automatically purged 24 hours after their last
  update. Cleanup runs hourly and only removes events with a `succeeded` status.
- Failed or pending events remain available for investigation until they are
  manually cleared or retried.

## Manual deletion API

- Super admins can remove webhook events directly from the queue when a
  delivery should be cancelled.
- `DELETE /scheduler/webhooks/{event_id}` removes the record (and its attempt
  history) when the event is not currently being processed. If the event status
  is `in_progress` the API returns HTTP 409 so the caller can retry once the
  delivery attempt finishes.
- Deletion cascades to attempt records via existing foreign keys; no additional
  cleanup is required.

## Admin UI controls

- The **Webhook delivery queue** page now includes a **Delete** action next to
  each event. For pending events the confirmation dialogue explicitly states
  that the pending request will be cancelled.
- When all events are removed the table displays the existing empty-state
  message to confirm the queue is clear.

## Bulk cleanup for completed investigations

- Super admins can bulk-delete events that no longer require attention using
  the API or the admin UI.
- `DELETE /scheduler/webhooks?status=failed` removes all failed deliveries.
- `DELETE /scheduler/webhooks?status=succeeded` removes all successful
  deliveries.
- The admin console now surfaces **Delete all failed** and **Delete all
  successful** controls alongside the delivery queue filter. Each action
  displays a confirmation prompt, reports how many records were removed, and
  refreshes the table so administrators can see the updated queue.

These changes complement the retry workflow and keep the monitor focused on
actionable events while leaving a short retention period for successful
deliveries.
