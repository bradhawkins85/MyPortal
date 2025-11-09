-- Add WhisperX integration module for call transcription
INSERT INTO integration_modules (slug, name, description, icon, enabled, settings)
VALUES (
    'whisperx',
    'WhisperX',
    'Transcribe call recordings using WhisperX speech-to-text service. Configure the WhisperX server endpoint and API credentials.',
    '🎙️',
    0,
    JSON_OBJECT(
        'base_url', '',
        'api_key', '',
        'language', 'en'
    )
) ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    description = VALUES(description),
    icon = VALUES(icon);
