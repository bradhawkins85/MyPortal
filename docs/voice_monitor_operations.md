# Voice Monitor safety, retention, and operator runbook

## Authority and call policy

Every destination is disabled until the customer explicitly authorises calls. The endpoint stores the consenting actor, time, policy version, and separate recording opt-in; revocation disables it. The dispatcher and worker both fail closed if consent, caller-ID verification, an active entitlement, or policy evidence is unavailable. Default quiet hours are 20:00–08:00 in the endpoint timezone. Emergency, premium, shared-cost, personal, pager, unknown and short-code destinations are blocked. Deployments must set a non-empty country allowlist and verify the outbound caller ID before enabling an endpoint.

Limits are enforced at the last possible point before originate: 30-second default duration (five-minute hard schema ceiling), zero retries by default, 10 attempts per destination per day, two tenant calls and ten calls globally, and a zero monetary cap until an operator explicitly configures one. Subscription cancellation or a pending entitlement change removes work from dispatch and claim queries immediately. Company deletion first disables endpoints, revokes consent, cancels queued/in-flight records, and deletes private content.

## Retention and deletion

* Packet capture is scoped to the negotiated call and is never persisted unless converted to an authorised media artifact. Provider-hosted media and transcripts use the content record's `retain_until`; the default is 30 days and the daily deletion task calls `delete_expired_content`.
* Call metadata and ticket links are retained for the contractual/audit period (default seven years); destination displays must be masked in support material. Tickets follow the configured ticket retention schedule.
* The immutable usage ledger and associated minimum call metadata are retained for seven financial years, or longer only under a legal hold. They contain no media or transcript text.
* Cancellation stops new calls but does not erase required billing evidence. Company erasure deletes media/transcripts immediately and de-identifies retained records where foreign-key/accounting requirements permit. Operators must verify both local deletion and provider-side media deletion.

## Monitoring and response

The admin worker-health endpoint is separate from `/health`, `/healthz`, and `/readyz`. Alert on a stale worker heartbeat, queue depth growth, calls with expired leases or no final callback after ten minutes, transcription pending/processing age, ticket-creation failures, callback authentication failures, provider latency, outcome/error rate, or differences between terminal attempts and usage-ledger rows.

1. **Provider outage:** disable the provider/module (never bypass policy), stop claiming, preserve queued work, consult provider status, then canary one consented endpoint before recovery.
2. **Worker restart:** stop claiming, allow the drain grace period, restart the independent systemd unit, confirm a fresh heartbeat and falling queue depth. Never restart web slots as a substitute.
3. **Stuck calls / missing callback:** query expired leases and `calls_missing_final_callback`, ask the provider for authoritative status, hang up active calls, then apply the final event idempotently. Do not create a second attempt.
4. **Credential rotation:** disable originates, rotate callback and API secrets at the provider and secret store, restart workers, send a signed canary callback, then revoke old credentials. Never log either value.
5. **Unexpected spend:** set the monetary cap to zero and disable endpoints, compare provider CDRs to the immutable ledger, preserve evidence, and escalate to billing/security before re-enabling.
6. **Abuse:** immediately revoke destination consent and disable the tenant; retain minimal audit/billing evidence, notify security/legal, and do not re-enable without fresh customer authority.
7. **Media deletion:** run the expiry deletion job, delete the opaque artifact at its backing provider/store, verify it is inaccessible, record the deletion audit, and retry/alert failures.
8. **Billing reconciliation:** compare terminal logical attempt IDs against one-and-only-one ledger row and provider CDR duration/cost. Add missing rows through the idempotent billing service; never edit or delete ledger rows. Escalate price or duration discrepancies.
