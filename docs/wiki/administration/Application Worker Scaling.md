# Application Worker Scaling

MyPortal's production deployment runs two application services, `blue` and
`green`. Each service starts four Uvicorn workers by default. Although nginx
sends normal traffic to only one service at a time, both services are kept
running to support health checks and zero-downtime upgrades. The default
therefore allows up to eight application worker processes across the host.

Set `WEB_CONCURRENCY=4` in `/etc/myportal.env`, or use
`/etc/myportal.blue.env` and `/etc/myportal.green.env` for per-service overrides.
The updated systemd unit honors this setting; older units that hardcode
`--workers 2` must first be updated through the normal deployment process.
Restart or roll the services to apply changes. Docker uses the same setting
in its application environment file; apply it with `myportal-docker restart`.
An existing explicit value of `2` remains in effect until changed.

## Recommended limits

Increasing the worker count is supported when production uses MySQL and the
host, database, and external services have sufficient capacity.

| Workers per service | Total application processes | Guidance |
| ---: | ---: | --- |
| 2 | 4 | Lower-capacity hosts |
| 3 | 6 | Intermediate setting |
| 4 | 8 | Default. Conservative ceiling without dedicated load testing |
| 5–8 | 10–16 | Use only after load testing and capacity verification |
| More than 8 | More than 16 | Not recommended without application-specific benchmarking and tuning |

Four workers per service is the default for hosts with sufficient capacity.
Observe a representative peak-load period and reduce to two or three if
database or external-service contention increases. Treat four workers per
service as the operational ceiling until a repeatable load test demonstrates
that a higher value is useful and has sufficient capacity.

> **SQLite limitation:** Do not increase the worker count on a production
> deployment using SQLite. SQLite does not provide the distributed named locks
> used by MySQL, and additional processes increase write contention. Use MySQL
> before scaling application workers.

## MySQL connection capacity

Each application process has a query pool (`DB_POOL_MAX_SIZE`, default 100)
and an advisory lock pool (`DB_LOCK_POOL_MAX_SIZE`, default 10). Both blue/green
services must be included when estimating the potential connection demand:

```text
maximum main-database capacity = 2 services × workers per service × (100 + 10)
```

| Workers per service | Potential application connections |
| ---: | ---: |
| 2 | 440 |
| 3 | 660 |
| 4 | 880 |
| 6 | 1320 |
| 8 | 1760 |

These totals do not include event-log and stats pools, database administration,
monitoring, voice-monitor services, or other applications. Reserve at least
20–30% of the MySQL connection limit for those uses. A conservative upper-bound
calculation is:

```text
workers per service <= floor(MyPortal's usable main-database connections / 220)
```

For example, if 880 connections are available exclusively to the two MyPortal
services' main-database pools, use no more than four workers per service with
these pool limits. Recalculate when changing either pool maximum.

## Background-work multiplication

Each Uvicorn process starts its own scheduler and enabled background integration
loops. Scheduled tasks use database locks under MySQL to prevent simultaneous
execution across processes, but every process still consumes memory, polls the
database, and participates in scheduling.

The relationship engine also starts `RAG_RELATIONSHIP_WORKERS` loops in every
application process. With its default value of two:

```text
total RAG loops = 2 services × workers per service × RAG_RELATIONSHIP_WORKERS
```

| Workers per service | Total RAG loops at the default setting |
| ---: | ---: |
| 2 | 8 |
| 3 | 12 |
| 4 | 16 |
| 8 | 32 |

If RAG, Matrix, Xero, or other external integrations are enabled, provider rate
limits and background concurrency may become constraints before CPU capacity.
Do not increase `RAG_RELATIONSHIP_WORKERS` at the same time as the Uvicorn worker
count; change and measure one setting at a time.

## Safe rollout checklist

1. Confirm that production is using MySQL rather than SQLite.
2. Record current peak request latency, error rate, CPU, memory, and database
   connections.
3. Confirm that MySQL has sufficient connection headroom using the calculation
   above.
4. Set `WEB_CONCURRENCY=4` for each blue/green service after installing the
   updated service unit, and confirm the intended `DB_POOL_MAX_SIZE` value.
5. Restart the services using the normal deployment process and verify both
   readiness endpoints.
6. Observe at least one representative peak-load period.
7. Adjust the worker count only when measurements show a benefit and
   sufficient capacity remains.

Monitor the following during and after the rollout:

- HTTP p95 and p99 latency, throughput, and 5xx responses;
- process memory and host available memory;
- CPU saturation and load average;
- MySQL connected/running threads and connection errors;
- scheduler lock contention or duplicate-work warnings;
- external API throttling and provider errors;
- RAG queue depth and provider latency; and
- application startup, readiness, and graceful-reload duration.

Always size the host for both blue and green services running simultaneously.
The inactive service is started and validated before an upgrade cutover, so
sizing only for the active service can make deployments fail even when normal
request traffic appears healthy.

