-- Add scheduled tasks for subscription price change notifications and price updates
INSERT INTO scheduled_tasks (name, command, cron, active, description)
VALUES 
  (
    'Send subscription price change notifications',
    'send_price_change_notifications',
    '0 9 * * *',
    1,
    'Send price change notifications to billing contacts for subscriptions with scheduled price changes'
  ),
  (
    'Apply scheduled product price changes',
    'apply_scheduled_price_changes',
    '0 10 * * *',
    1,
    'Apply scheduled price changes to products after notifications have been sent'
  )
ON DUPLICATE KEY UPDATE name = name;
