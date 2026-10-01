# Application Worker Scaling

MyPortal's production deployment runs two application services, `blue` and
`green`. Each service starts two Uvicorn workers by default. Although nginx
sends normal traffic to only one service at a time, both services are kept
running to support health checks and zero-downtime upgrades. The default
therefore allows up to four application worker processes across the host.

## Recommended limits

Increasing the worker count is supported when production uses MySQL and the
host, database, and external services have sufficient capacity.

| Workers per service | Total application processes | Guidance |
| ---: | ---: | --- |
| 2 | 4 | Default and safest starting point |
| 3 | 6 | Recommended first increase |
| 4 | 8 | Conservative ceiling without dedicated load testing |
| 5–8 | 10–16 | Use only after load testing and capacity verification |
| More than 8 | More than 16 | Not recommended without application-specific benchmarking and tuning |

Increase to three workers per service first and observe a representative
peak-load period. Increase to four only if request latency or throughput still
benefits. Treat four workers per service as the operational ceiling until a
repeatable load test demonstrates that a higher value is both useful and safe.

> **SQLite limitation:** Do not increase the worker count on a production
> deployment using SQLite. SQLite does not provide the distributed named locks
> used by MySQL, and additional processes increase write contention. Use MySQL
> before scaling application workers.

## MySQL connection capacity

Each application process has a MySQL connection pool with a maximum of ten
connections. Both blue/green services must be included when estimating the
potential connection demand:

```text
maximum application pool capacity = 2 services × workers per service × 10
```

| Workers per service | Potential application connections |
| ---: | ---: |
| 2 | 40 |
| 3 | 60 |
| 4 | 80 |
| 6 | 120 |
| 8 | 160 |

These totals do not include database administration, deployments, monitoring,
voice-monitor services, or other applications. Reserve at least 20–30% of the
MySQL connection limit for those uses. A conservative upper-bound calculation
is:

```text
workers per service <= floor(MyPortal's usable MySQL connections / 20)
```

For example, if 80 connections are safely available exclusively to the two
MyPortal application services, use no more than four workers per service.

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
4. Increase each blue/green service to three workers.
5. Restart the services using the normal deployment process and verify both
   readiness endpoints.
6. Observe at least one representative peak-load period.
7. Increase to four only when the measurements show that another worker is
   needed and sufficient capacity remains.

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

