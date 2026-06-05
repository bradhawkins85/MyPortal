ALTER TABLE notification_exclusions
  ADD COLUMN message_pattern VARCHAR(500) NOT NULL DEFAULT '' AFTER event_type,
  DROP INDEX uq_notification_exclusions_user_event,
  ADD UNIQUE KEY uq_notification_exclusions_user_event_pattern (user_id, event_type, message_pattern(200));
