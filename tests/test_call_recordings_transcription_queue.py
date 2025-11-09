"""Tests for call recording transcription queuing and processing."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services import call_recordings as call_recordings_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _RecordingsRepositoryStub:
    """Stub repository for testing call recordings operations."""

    def __init__(self):
        self.recordings: dict[int, dict] = {}
        self.next_id = 1
        self.updates: list[tuple[int, dict]] = []

    async def list_call_recordings(
        self, 
        *, 
        transcription_status=None, 
        limit=100,
        offset=0,
        search=None,
        linked_ticket_id=None,
    ):
        """Return recordings filtered by transcription status."""
        results = []
        for recording in self.recordings.values():
            if transcription_status and recording.get("transcription_status") != transcription_status:
                continue
            results.append(recording)
        
        # Sort by ID (oldest first)
        results.sort(key=lambda r: r["id"])
        return results[:limit]

    async def get_call_recording_by_id(self, recording_id: int):
        """Get a recording by ID."""
        return self.recordings.get(recording_id)

    async def update_call_recording(
        self, 
        recording_id: int, 
        *, 
        transcription=None, 
        transcription_status=None,
        linked_ticket_id=None,
    ):
        """Update a recording."""
        if recording_id not in self.recordings:
            raise ValueError(f"Recording {recording_id} not found")
        
        recording = self.recordings[recording_id]
        
        if transcription is not None:
            recording["transcription"] = transcription
        if transcription_status is not None:
            recording["transcription_status"] = transcription_status
        if linked_ticket_id is not None:
            recording["linked_ticket_id"] = linked_ticket_id
        
        self.updates.append((recording_id, {
            "transcription": transcription,
            "transcription_status": transcription_status,
        }))
        
        return recording

    def add_recording(self, **kwargs):
        """Helper to add a test recording."""
        recording_id = self.next_id
        self.next_id += 1
        
        recording = {
            "id": recording_id,
            "file_path": f"/path/to/recording{recording_id}.wav",
            "file_name": f"recording{recording_id}.wav",
            "transcription": None,
            "transcription_status": "pending",
            **kwargs
        }
        
        self.recordings[recording_id] = recording
        return recording


class _ModulesRepositoryStub:
    """Stub repository for integration modules."""

    def __init__(self):
        self.modules = {}

    async def get_module(self, slug: str):
        """Get a module by slug."""
        return self.modules.get(slug)

    def add_module(self, slug: str, enabled: bool = True, settings: dict = None):
        """Helper to add a test module."""
        self.modules[slug] = {
            "slug": slug,
            "enabled": enabled,
            "settings": settings or {},
        }


@pytest.mark.anyio
async def test_queue_pending_transcriptions_marks_recordings_as_queued(monkeypatch):
    """Test that pending recordings are marked as queued."""
    repo = _RecordingsRepositoryStub()
    
    # Add some test recordings
    repo.add_recording(transcription_status="pending")
    repo.add_recording(transcription_status="pending")
    repo.add_recording(transcription_status="completed")  # Should be ignored
    
    # Patch the repository
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    
    # Run the queueing function
    result = await call_recordings_service.queue_pending_transcriptions()
    
    # Verify results
    assert result["status"] == "ok"
    assert result["queued"] == 2
    
    # Check that recordings were updated
    assert len(repo.updates) == 2
    for recording_id, updates in repo.updates:
        assert updates["transcription_status"] == "queued"


@pytest.mark.anyio
async def test_queue_pending_transcriptions_handles_no_pending_recordings(monkeypatch):
    """Test that function handles no pending recordings gracefully."""
    repo = _RecordingsRepositoryStub()
    
    # Add only completed recordings
    repo.add_recording(transcription_status="completed")
    repo.add_recording(transcription_status="failed")
    
    # Patch the repository
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    
    # Run the queueing function
    result = await call_recordings_service.queue_pending_transcriptions()
    
    # Verify results
    assert result["status"] == "ok"
    assert result["queued"] == 0
    assert len(repo.updates) == 0


@pytest.mark.anyio
async def test_process_queued_transcriptions_processes_one_recording(monkeypatch):
    """Test that only one recording is processed at a time."""
    repo = _RecordingsRepositoryStub()
    modules_repo = _ModulesRepositoryStub()
    
    # Add test recordings
    recording1 = repo.add_recording(transcription_status="queued")
    recording2 = repo.add_recording(transcription_status="queued")
    
    # Add WhisperX module
    modules_repo.add_module("whisperx", enabled=True, settings={
        "base_url": "http://whisperx.test",
    })
    
    # Mock the transcription service call
    transcribe_called = []
    
    async def mock_transcribe(recording_id, *, force=False):
        transcribe_called.append(recording_id)
        # Simulate successful transcription
        recording = repo.recordings[recording_id]
        recording["transcription"] = f"Transcribed text for {recording_id}"
        recording["transcription_status"] = "completed"
        return recording
    
    # Patch the repositories and transcribe function
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.modules_repo",
        modules_repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.transcribe_recording",
        mock_transcribe,
    )
    
    # Run the processing function
    result = await call_recordings_service.process_queued_transcriptions()
    
    # Verify only one recording was processed
    assert result["status"] == "ok"
    assert result["processed"] == 1
    assert result["recording_id"] == recording1["id"]
    assert len(transcribe_called) == 1
    assert transcribe_called[0] == recording1["id"]


@pytest.mark.anyio
async def test_process_queued_transcriptions_handles_no_queued_recordings(monkeypatch):
    """Test that function handles no queued recordings gracefully."""
    repo = _RecordingsRepositoryStub()
    
    # Add only completed recordings
    repo.add_recording(transcription_status="completed")
    
    # Patch the repository
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    
    # Run the processing function
    result = await call_recordings_service.process_queued_transcriptions()
    
    # Verify results
    assert result["status"] == "ok"
    assert result["processed"] == 0
    assert result["message"] == "No recordings to process"


@pytest.mark.anyio
async def test_process_queued_transcriptions_retries_failed_recordings(monkeypatch):
    """Test that failed recordings are retried."""
    repo = _RecordingsRepositoryStub()
    modules_repo = _ModulesRepositoryStub()
    
    # Add a failed recording (and no queued ones)
    failed_recording = repo.add_recording(transcription_status="failed")
    
    # Add WhisperX module
    modules_repo.add_module("whisperx", enabled=True, settings={
        "base_url": "http://whisperx.test",
    })
    
    # Mock the transcription service call
    async def mock_transcribe(recording_id, *, force=False):
        recording = repo.recordings[recording_id]
        recording["transcription"] = "Successful retry"
        recording["transcription_status"] = "completed"
        return recording
    
    # Patch the repositories and transcribe function
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.modules_repo",
        modules_repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.transcribe_recording",
        mock_transcribe,
    )
    
    # Run the processing function
    result = await call_recordings_service.process_queued_transcriptions()
    
    # Verify the failed recording was retried
    assert result["status"] == "ok"
    assert result["processed"] == 1
    assert result["recording_id"] == failed_recording["id"]


@pytest.mark.anyio
async def test_process_queued_transcriptions_handles_transcription_error(monkeypatch):
    """Test that transcription errors are handled gracefully."""
    repo = _RecordingsRepositoryStub()
    
    # Add a queued recording
    recording = repo.add_recording(transcription_status="queued")
    
    # Mock transcription to raise an error
    async def mock_transcribe(recording_id, *, force=False):
        raise ValueError("WhisperX service unavailable")
    
    # Patch the repository and transcribe function
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.transcribe_recording",
        mock_transcribe,
    )
    
    # Run the processing function
    result = await call_recordings_service.process_queued_transcriptions()
    
    # Verify error was handled
    assert result["status"] == "error"
    assert result["processed"] == 0
    assert result["recording_id"] == recording["id"]
    assert "WhisperX service unavailable" in result["error"]


@pytest.mark.anyio
async def test_transcribe_recording_skips_processing_status(monkeypatch):
    """Test that recordings already being processed are skipped."""
    repo = _RecordingsRepositoryStub()
    
    # Add a recording that's already being processed
    recording = repo.add_recording(transcription_status="processing")
    
    # Patch the repository
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    
    # Try to transcribe
    result = await call_recordings_service.transcribe_recording(
        recording["id"],
        force=False,
    )
    
    # Verify it was skipped (no changes)
    assert result["transcription_status"] == "processing"
    assert len(repo.updates) == 0


@pytest.mark.anyio
async def test_transcribe_recording_skips_completed_status(monkeypatch):
    """Test that completed recordings are not re-transcribed."""
    repo = _RecordingsRepositoryStub()
    
    # Add a completed recording
    recording = repo.add_recording(
        transcription_status="completed",
        transcription="Already transcribed",
    )
    
    # Patch the repository
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    
    # Try to transcribe
    result = await call_recordings_service.transcribe_recording(
        recording["id"],
        force=False,
    )
    
    # Verify it was skipped (no changes)
    assert result["transcription_status"] == "completed"
    assert result["transcription"] == "Already transcribed"
    assert len(repo.updates) == 0


@pytest.mark.anyio
async def test_transcribe_recording_allows_force_reprocessing(monkeypatch):
    """Test that force=True allows re-transcribing completed recordings."""
    repo = _RecordingsRepositoryStub()
    modules_repo = _ModulesRepositoryStub()
    
    # Add a completed recording
    recording = repo.add_recording(
        transcription_status="completed",
        transcription="Old transcription",
    )
    
    # Add WhisperX module
    modules_repo.add_module("whisperx", enabled=True, settings={
        "base_url": "http://whisperx.test",
        "api_key": "test-key",
    })
    
    # Mock HTTP client
    class MockResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"text": "New transcription"}'
        text = '{"text": "New transcription"}'
        
        def json(self):
            return {"text": "New transcription"}
        
        def raise_for_status(self):
            pass
    
    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, **kwargs):
            return MockResponse()
    
    # Patch dependencies
    monkeypatch.setattr(
        "app.services.call_recordings.call_recordings_repo",
        repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.modules_repo",
        modules_repo,
    )
    monkeypatch.setattr(
        "app.services.call_recordings.httpx.AsyncClient",
        MockClient,
    )
    
    # Mock file open
    from unittest.mock import mock_open, patch
    mock_file = mock_open(read_data=b"audio data")
    
    with patch("builtins.open", mock_file):
        # Mock Path.stat()
        class MockStat:
            st_size = 1000
        
        class MockPath:
            def stat(self):
                return MockStat()
        
        monkeypatch.setattr(
            "app.services.call_recordings.Path",
            lambda x: MockPath(),
        )
        
        # Try to transcribe with force=True
        result = await call_recordings_service.transcribe_recording(
            recording["id"],
            force=True,
        )
    
    # Verify it was re-transcribed
    assert result["transcription"] == "New transcription"
    assert result["transcription_status"] == "completed"
