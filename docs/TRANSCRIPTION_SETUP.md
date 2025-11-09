# Call Recording Transcription Setup

This document describes how to set up automated call recording synchronization and transcription processing.

## Overview

The system provides three scheduled tasks that work together to:
1. Sync call recordings from the filesystem
2. Queue pending recordings for transcription
3. Process transcriptions one at a time

## Prerequisites

1. **Call Recordings Module**: Configure the `call-recordings` integration module with the path to your recordings directory
2. **WhisperX Module**: Configure the `whisperx` integration module with your transcription service URL and API key

## Setting Up Scheduled Tasks

Create the following scheduled tasks in the admin panel (`/admin/scheduler`):

### 1. Sync Recordings from Filesystem

**Purpose**: Discovers new call recording files and adds them to the database

- **Name**: Sync Call Recordings
- **Command**: `sync_recordings`
- **Schedule (Cron)**: `*/5 * * * *` (every 5 minutes)
- **Description**: Syncs call recording files from the filesystem to the database

### 2. Queue Pending Transcriptions

**Purpose**: Marks pending recordings as "queued" to prepare them for processing

- **Name**: Queue Pending Transcriptions
- **Command**: `queue_transcriptions`
- **Schedule (Cron)**: `*/10 * * * *` (every 10 minutes)
- **Description**: Marks pending recordings as queued for transcription processing

### 3. Process One Transcription

**Purpose**: Processes one queued transcription at a time to avoid overwhelming the service

- **Name**: Process Transcription
- **Command**: `process_transcription`
- **Schedule (Cron)**: `* * * * *` (every minute)
- **Description**: Processes one queued transcription per run

## How It Works

### Status Flow

Recordings progress through these statuses:

```
pending → queued → processing → completed
                              ↓
                           failed (can be retried)
```

### Workflow

1. **Sync Task Runs**:
   - Scans the recordings directory
   - Creates database records for new files
   - Sets status to "pending" for files without transcriptions

2. **Queue Task Runs**:
   - Finds all recordings with status "pending"
   - Updates their status to "queued"
   - Prevents duplicate queuing on subsequent runs

3. **Process Task Runs**:
   - Finds the oldest "queued" recording
   - Updates status to "processing"
   - Calls WhisperX API with the audio file
   - Updates status to "completed" or "failed" based on result
   - If no queued recordings exist, checks for "failed" recordings to retry

### Key Features

- **No Duplicates**: Once queued, a recording won't be re-queued
- **One at a Time**: Only one transcription is processed per scheduled run
- **Completed Protection**: Recordings with status "completed" are never re-sent
- **Automatic Retries**: Failed recordings are automatically retried
- **Concurrent Safety**: "processing" status prevents multiple workers from processing the same file

## Configuration Example

### Call Recordings Module Settings

```json
{
  "recordings_path": "/var/lib/myportal/call_recordings"
}
```

### WhisperX Module Settings

```json
{
  "base_url": "http://whisperx-service:8000",
  "api_key": "your-api-key-here",
  "language": "en"
}
```

## Monitoring

### Check Task Execution

View task run history in the scheduler admin panel:
- Navigate to `/admin/scheduler`
- Check the "Recent Runs" section
- Review status, duration, and any errors

### Check Recording Status

Via API:
```bash
curl -X GET "http://your-portal/api/call-recordings?transcriptionStatus=queued" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

Via SQL (if you have database access):
```sql
SELECT 
    transcription_status,
    COUNT(*) as count
FROM call_recordings
GROUP BY transcription_status;
```

## Troubleshooting

### Recordings Not Being Synced

1. Check that the `call-recordings` module is enabled
2. Verify the `recordings_path` in module settings
3. Ensure the path exists and is readable by the application
4. Check scheduled task logs for errors

### Transcriptions Not Processing

1. Verify the `whisperx` module is enabled
2. Check WhisperX service is accessible from your server
3. Verify API key is correct
4. Check for failed recordings: they may have error details
5. Review webhook monitor for transcription API call history

### Failed Transcriptions

Failed recordings will be automatically retried on the next process run. To manually retry:

1. Update the recording status back to "queued" or "pending"
2. Wait for the next scheduled run

Or via API:
```bash
curl -X PUT "http://your-portal/api/call-recordings/{recording_id}" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"transcriptionStatus": "queued"}'
```

## Performance Tuning

### High Volume Scenarios

If you have many recordings to process:

1. **Increase process frequency**: Change `process_transcription` to run every 30 seconds:
   ```
   */30 * * * * *
   ```

2. **Batch queuing**: The queue task processes up to 1000 recordings per run

3. **Monitor WhisperX capacity**: Ensure your transcription service can handle the request rate

### Low Volume Scenarios

For fewer recordings:

1. **Reduce sync frequency**: Run `sync_recordings` every 15 minutes:
   ```
   */15 * * * *
   ```

2. **Reduce queue frequency**: Run `queue_transcriptions` every 30 minutes:
   ```
   */30 * * * *
   ```

## API Endpoints

### Manual Sync
```bash
POST /api/call-recordings/sync?recordingsPath=/path/to/recordings
```

### Transcribe Specific Recording
```bash
POST /api/call-recordings/{recording_id}/transcribe
Content-Type: application/json

{
  "force": false
}
```

### List Recordings by Status
```bash
GET /api/call-recordings?transcriptionStatus=queued&limit=100
```

## Security Notes

- Scheduled tasks run with system-level permissions
- API endpoints require super-admin authentication
- WhisperX API keys are stored encrypted in the database
- Transcription webhook calls are monitored and logged
