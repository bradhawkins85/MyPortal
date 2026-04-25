-- Add stereo_split setting to the WhisperX module.
-- When enabled, stereo WAV recordings are split into two mono channels before
-- transcription. The right channel is treated as the Caller and the left
-- channel as the Callee (Grandstream UCM convention).
UPDATE integration_modules
SET settings = JSON_SET(
        COALESCE(settings, '{}'),
        '$.stereo_split', FALSE
    )
WHERE slug = 'whisperx'
  AND NOT JSON_CONTAINS_PATH(settings, 'one', '$.stereo_split');
