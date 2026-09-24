# Redis Installation and Configuration

Redis is optional for a single-process MyPortal development instance, but it is
recommended for production and any deployment that runs more than one web
worker. MyPortal uses Redis for shared rate-limit state, cross-worker refresh
notifications, short-lived OAuth and PKCE transactions, message-template cache
invalidation, and Voice Monitor worker wake-ups. Without `REDIS_URL`, the
application uses process-local fallbacks where available, so state and events
are not shared between workers.

## 1. Install Redis

### Debian or Ubuntu

Install the distribution package, then enable and start the service:

```bash
sudo apt-get update
sudo apt-get install -y redis-server
sudo systemctl enable --now redis-server
```

Some distributions name the unit `redis` instead of `redis-server`. Check the
installed unit with:

```bash
systemctl status redis-server --no-pager
```

### Fedora, RHEL, or Rocky Linux

```bash
sudo dnf install -y redis
sudo systemctl enable --now redis
```

### Docker (local development)

The following command starts Redis on the loopback interface and stores data in
a named volume:

```bash
docker run --name myportal-redis \
  --detach \
  --restart unless-stopped \
  --publish 127.0.0.1:6379:6379 \
  --volume myportal-redis-data:/data \
  redis:7-alpine redis-server --appendonly yes
```

Pin the image to the Redis version approved for your environment rather than
using an unversioned image tag in production.

## 2. Verify the Server

For a local installation, confirm that Redis responds before configuring
MyPortal:

```bash
redis-cli ping
```

The expected response is `PONG`. For the Docker example, use:

```bash
docker exec myportal-redis redis-cli ping
```

## 3. Configure MyPortal

Set `REDIS_URL` in the environment file read by MyPortal. For a local Redis
server without authentication:

```dotenv
REDIS_URL=redis://127.0.0.1:6379/0
```

Restart every MyPortal web and worker process after changing this value. An
empty value disables Redis:

```dotenv
REDIS_URL=
```

### URL formats

| Deployment | Example |
| --- | --- |
| Local server | `redis://127.0.0.1:6379/0` |
| Password only | `redis://:password@redis.example.internal:6379/0` |
| ACL username and password | `redis://myportal:password@redis.example.internal:6379/0` |
| TLS | `rediss://myportal:password@redis.example.internal:6380/0` |

Percent-encode reserved characters in usernames and passwords used in a URL.
For example, encode `@` as `%40`. Do not commit a populated Redis URL to the
repository; store it in `.env`, `/etc/myportal.env`, or your deployment's
secret manager.

When MyPortal and Redis run in separate containers, use the Redis service name
instead of `127.0.0.1`, for example:

```dotenv
REDIS_URL=redis://redis:6379/0
```

## 4. Secure a Production Deployment

- Keep Redis on a private network. Do not publish port `6379` to the internet.
- Bind a host-installed Redis server to loopback or a private interface and
  retain protected mode. Firewall access so only MyPortal hosts can connect.
- Use an ACL user with only the permissions required by MyPortal, and use TLS
  (`rediss://`) when traffic crosses an untrusted network.
- Put credentials in a mode-restricted environment file or secret manager;
  never place them directly in a systemd unit or source-controlled file.
- Apply operating-system and Redis security updates, and monitor memory usage,
  connection failures, and evictions.

MyPortal stores operational and short-lived coordination data in Redis. Redis
is not a replacement for the MySQL database or its backups. Persistence can
improve recovery of cache and coordination state, but MyPortal's durable data
continues to live in MySQL.

## 5. Validate MyPortal's Connection

Load the same environment file used by MyPortal and run a ping through the
project's Redis client:

```bash
set -a
source .env
set +a
python - <<'PY'
import asyncio

from app.services.redis import close_redis_client, get_redis_client


async def main() -> None:
    client = get_redis_client()
    if client is None:
        raise SystemExit("REDIS_URL is not configured")
    print(await client.ping())
    await close_redis_client()


asyncio.run(main())
PY
```

The expected output is `True`. Then restart the relevant services, for example:

```bash
sudo systemctl restart myportal.service
sudo journalctl -u myportal.service -n 100 --no-pager
```

For a multi-worker deployment, configure every web and background worker with
the same Redis service. Separate MyPortal environments (for example, staging
and production) should use separate Redis databases or, preferably, separate
Redis instances to prevent their keys and pub/sub events from overlapping.

## Troubleshooting

### Connection refused

Confirm Redis is running and listening on the host and port in `REDIS_URL`:

```bash
sudo systemctl status redis-server --no-pager
ss -ltn | grep 6379
```

In a container, remember that `127.0.0.1` refers to that container. Use the
Redis container or service name when Redis runs elsewhere.

### Authentication errors

Test the configured URL directly without printing it in logs:

```bash
redis-cli -u "$REDIS_URL" ping
```

Check the ACL username and password and make sure reserved URL characters are
percent-encoded. Avoid passing a production URL directly on the command line,
where it can be retained in shell history.

### TLS errors

Use the `rediss://` scheme and confirm that the Redis certificate is valid for
the hostname in `REDIS_URL`. Install the issuing CA in the operating system's
trust store when the service uses a private certificate authority.

### Redis becomes unavailable after startup

Some MyPortal features fall back to process-local state after a Redis error,
but that state is not shared across workers. Restore Redis, investigate the
service logs, and restart all MyPortal processes so they create fresh Redis
connections.
