# Call Recording Transcription Setup

This document describes how to set up automated call recording synchronization and transcription processing.

## Overview

The system provides three scheduled tasks that work together to:
1. Sync call recordings from the filesystem
2. Queue pending recordings for transcription
3. Process transcriptions one at a time

## Prerequisites

1. **Call Recordings Module**: Configure the `call-recordings` integration module with the path to your recordings directory
2. **Speech Transcription Module**: Enable the `whisperx` integration module and configure a transcription engine — either [WhisperX](https://github.com/ahmetoner/whisper-asr-webservice) or [whisper.cpp](https://github.com/ggml-org/whisper.cpp) — in `.env` (see [Choosing a transcription engine](#choosing-a-transcription-engine))

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
                           failed (manual retry required)
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
   - Sends the audio file to the configured transcription engine (WhisperX or whisper.cpp)
   - Updates status to "completed" or "failed" based on result
   - Failed recordings remain in "failed" status and require manual retry

### Key Features

- **No Duplicates**: Once queued, a recording won't be re-queued
- **One at a Time**: Only one transcription is processed per scheduled run
- **Completed Protection**: Recordings with status "completed" are never re-sent
- **Manual Retry for Failures**: Failed recordings must be manually reset to "queued" or "pending" to retry
- **Concurrent Safety**: "processing" status prevents multiple workers from processing the same file

## Configuration Example

### Call Recordings Module Settings

```json
{
  "recordings_path": "/var/lib/myportal/call_recordings"
}
```

### Choosing a transcription engine

Two speech-to-text engines are supported. Both are configured in `.env`
and share the same module, scheduled tasks and automation action.

| Engine | `TRANSCRIPTION_ENGINE` | Endpoint called | Settings |
| --- | --- | --- | --- |
| WhisperX (`whisper-asr-webservice`) | `whisperx` | `POST {base}/asr` | `WHISPERX_BASE_URL`, `WHISPERX_API_KEY`, `WHISPERX_LANGUAGE`, `WHISPERX_STEREO_SPLIT` |
| whisper.cpp (`whisper-server`) | `whispercpp` | `POST {base}/v1/audio/transcriptions` | `WHISPERCPP_BASE_URL`, `WHISPERCPP_API_KEY`, `WHISPERCPP_LANGUAGE`, `WHISPERCPP_STEREO_SPLIT` |

When `TRANSCRIPTION_ENGINE` is blank, the first engine with a base URL is
used (WhisperX before whisper.cpp), so existing WhisperX installs keep
working unchanged.

```env
# WhisperX
TRANSCRIPTION_ENGINE=whisperx
WHISPERX_BASE_URL=http://whisperx-service:9000
WHISPERX_API_KEY=your-api-key-here
WHISPERX_LANGUAGE=en

# or whisper.cpp
TRANSCRIPTION_ENGINE=whispercpp
WHISPERCPP_BASE_URL=http://whisper-cpp:8080
WHISPERCPP_LANGUAGE=en
```

#### Running whisper.cpp

MyPortal calls whisper.cpp on the OpenAI-compatible
`/v1/audio/transcriptions` route. Start `whisper-server` with
`--inference-path /v1/audio/transcriptions` (its default route is
`/inference`) and `--convert` so it can accept MP3 and WAV files that are not
16 kHz mono (this requires `ffmpeg` on the server):

```bash
whisper-server -m models/ggml-base.en.bin --host 0.0.0.0 --port 8080 \
  --inference-path /v1/audio/transcriptions --convert
```

`WHISPERCPP_BASE_URL` may be the bare server address
(`http://whisper-cpp:8080`), an OpenAI-style `/v1` base or the full
`/v1/audio/transcriptions` URL.

If both `WHISPERX_BASE_URL` and `WHISPERCPP_BASE_URL` are set, WhisperX
(`/asr`) is used unless `TRANSCRIPTION_ENGINE=whispercpp`.

MyPortal requests `response_format=verbose_json`, so segment timestamps are
available for stereo (caller/callee) transcripts. `whisper-server` has no
built-in authentication; if you put it behind an authenticating reverse
proxy, set `WHISPERCPP_API_KEY` and it is sent as a `Bearer` token. Keep the
server on a private network either way.

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

Failed recordings will NOT be automatically retried. To manually retry:

1. Update the recording status back to "queued" or "pending"
2. Wait for the next scheduled run

Or via API:
```bash
curl -X PUT "http://your-portal/api/call-recordings/{recording_id}" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"transcriptionStatus": "queued"}'
```

**Why Manual Retry?** Automatic retries could cause repeated failures if there's an issue with:
- The audio file format
- Network connectivity
- WhisperX service availability
- API authentication

Manual retry allows you to investigate and fix the underlying issue before retrying.

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
