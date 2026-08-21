# Voice Monitor dispatch and workers

Voice Monitor uses MySQL as its durable queue. Redis is an optional wake-up
hint, never the source of truth. The dispatcher only finds due endpoints and
inserts a schedule-keyed attempt; it does not dial or process media. Workers
atomically claim attempts with expiring leases, renew leases while calls are
active, and recover abandoned claims. Provider originate calls use the same
durable idempotency key on every delivery.

Provider adapters implement `app.services.voice_monitor.providers.VoiceMonitorProvider`.
Set `VOICE_MONITOR_PROVIDER=package.module:create_provider`, where the factory
loads credentials from environment variables or encrypted module settings.
Never put credentials in adapter return values, attempt diagnostics, or logs.

## Enable and scale independently

Install and start one worker without restarting either `myportal@` web slot:

```bash
sudo cp deploy/systemd/myportal-voice-monitor@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now myportal-voice-monitor@1.service
```

Scale horizontally by enabling more instances (`@2`, `@3`, and so on). Database
compare-and-swap claims prevent two instances owning the same lease. Global and
per-tenant concurrency limits in each process prevent one tenant from consuming
all local capacity. Configure instance-specific provider/environment settings in
`/etc/myportal.voice-monitor.<instance>.env`.

The service has an independent restart policy, resource limits, 35-second
shutdown budget, and a health marker under `/run/myportal`. On SIGTERM it stops
claiming, drains calls, hangs up calls that exceed the grace period, and records
them as interrupted/retryable. Thus restarting `myportal@blue` or
`myportal@green` cannot terminate worker-owned calls; worker restarts leave final
state reconcilable through provider idempotency and expired leases.
