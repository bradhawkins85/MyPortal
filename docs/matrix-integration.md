# Matrix.org Chat Integration

## Overview

MyPortal supports built-in chat functionality backed by a Matrix homeserver. Customers can start a chat from the portal and technicians/admins can join to respond.

## Configuration

Set the following environment variables in your `.env` file:

| Variable | Description | Required |
|----------|-------------|----------|
| `MATRIX_ENABLED` | Enable Matrix chat (`true`/`false`) | Yes |
| `MATRIX_HOMESERVER_URL` | Full URL of your homeserver (e.g. `https://matrix.org`) | Yes |
| `MATRIX_SERVER_NAME` | Domain part of MXIDs (e.g. `matrix.org`) | Yes |
| `MATRIX_BOT_USER_ID` | Portal bot account MXID (e.g. `@myportal:matrix.org`) | Yes |
| `MATRIX_BOT_ACCESS_TOKEN` | Bot account access token | Yes |
| `MATRIX_DEVICE_ID` | Bot device ID (optional) | No |
| `MATRIX_IS_SELF_HOSTED` | Enable user provisioning features | No |
| `MATRIX_ADMIN_ACCESS_TOKEN` | Synapse admin API token | Self-hosted only |
| `MATRIX_DEFAULT_ROOM_PRESET` | Room privacy preset | No |
| `MATRIX_E2EE_ENABLED` | Enable E2EE (future feature) | No |
| `MATRIX_INVITE_DOMAIN` | Domain for provisioned user MXIDs | Self-hosted only |

## Using matrix.org

When using `matrix.org`, set `MATRIX_IS_SELF_HOSTED=false`. The portal relays messages through the bot account on behalf of users. External invite generation (provisioning new Matrix accounts) is disabled in this mode.

## Using a self-hosted Synapse server

When `MATRIX_IS_SELF_HOSTED=true`, the portal can:
- Provision Matrix accounts for portal users automatically
- Generate external invites that create a Matrix account and send credentials to invitees
- Allow invitees to continue the conversation via Element X on mobile

### External Invite Flow

1. Open a chat room and click **Invite to Matrix**
2. Fill in the invitee's display name, email, and/or phone number
3. Select the delivery method (Email, SMS, or Manual)
4. The portal will:
   - Create a Matrix account on your Synapse server
   - Invite the new account to the room
   - Deliver credentials via the selected method
5. The invitee receives their MXID, temporary password, and a deep link to open the room in Element X

### Revoking an Invite

Revoking an invite rotates the invitee's Matrix password, preventing further access.

## Security Notes

- All stored Matrix tokens and passwords are encrypted at rest using AES-256-GCM
- External invites expire after 72 hours by default
- Room visibility is scoped to company and role
- E2EE is disabled by default so the portal bridge can read messages; enable only after reviewing the trade-offs

## Setting up the Bot Account

1. Create a Matrix account for the bot (e.g. `@myportal-bot:example.com`)
2. Log in and obtain an access token:
   ```bash
   curl -X POST 'https://your-homeserver/_matrix/client/v3/login' \
     -H 'Content-Type: application/json' \
     -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"myportal-bot"},"password":"YOUR_PASSWORD"}'
   ```
3. Copy the `access_token` from the response into `MATRIX_BOT_ACCESS_TOKEN`
4. Use **Admin → Test connection** in MyPortal to verify the configuration
