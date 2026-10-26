-- phase: expand
-- compatible-from: *
-- compatible-to: *
-- maintenance: false
-- Migration 524: give reporting queries a category so the Manage reports page
-- can group, sort and filter the catalogue, and file every system report under
-- one. Only system reports without a category are updated, so a category an
-- administrator has already chosen is kept.
ALTER TABLE reporting_queries ADD COLUMN IF NOT EXISTS category VARCHAR(64) NULL;

UPDATE reporting_queries SET category = 'Tickets'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'company-sla-performance',
  'dashboard-global-tickets-by-company',
  'dashboard-issues',
  'dashboard-issues-by-status',
  'dashboard-open-tickets',
  'dashboard-sla-compliance',
  'dashboard-sla-status',
  'dashboard-ticket-replies-30-days',
  'dashboard-tickets-by-status',
  'dashboard-tickets-created-30-days',
  'report-issue-tracker-issues',
  'report-tickets-past-month',
  'stat-strip-admin-tickets',
  'stat-strip-customer-tickets',
  'stat-strip-report-tickets-month',
  'tickets-open-by-priority',
  'tickets-open-workload-by-technician',
  'tickets-parent-child-tasks',
  'tickets-resolution-time-last-30-days',
  'tickets-search-ai-tags',
  'tickets-search-all-fields-and',
  'tickets-search-all-fields-or',
  'tickets-search-description-and-ai-tags',
  'tickets-search-description-or-ai-tags',
  'tickets-search-initial-description',
  'tickets-search-subject',
  'tickets-search-subject-and-ai-tags',
  'tickets-search-subject-and-description',
  'tickets-search-subject-or-ai-tags',
  'tickets-search-subject-or-description'
);

UPDATE reporting_queries SET category = 'Billing'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'billable-amount-per-company-current-month',
  'billable-amount-per-company-previous-month',
  'billed-tickets',
  'dashboard-invoices',
  'dashboard-ticket-time-by-day',
  'invoices-status-and-overdue-by-company',
  'revenue-leakage-unbilled-time',
  'tech-billable-vs-non-billable-per-day',
  'unbilled-tickets',
  'unbilled-tickets-by-company'
);

UPDATE reporting_queries SET category = 'Shop and Orders'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-orders',
  'dashboard-orders-by-status',
  'dashboard-quotes',
  'dashboard-quotes-by-status',
  'report-orders-this-month',
  'shop-orders-by-company-last-30-days',
  'shop-product-stock-and-value',
  'shop-quotes-status-and-expiry',
  'stat-strip-report-orders-month'
);

UPDATE reporting_queries SET category = 'Licenses and Subscriptions'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-global-available-licenses',
  'dashboard-licenses',
  'dashboard-licenses-by-product',
  'dashboard-subscriptions',
  'licenses-expiring-next-90-days',
  'report-licenses',
  'report-subscriptions',
  'stat-strip-subscription-renewals',
  'subscriptions-status-by-company'
);

UPDATE reporting_queries SET category = 'Assets'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'assets-by-asset-type',
  'assets-by-status-and-type',
  'assets-by-tag',
  'assets-out-of-warranty',
  'assets-warranty-expiring-next-90-days',
  'assets-warranty-lookup-status',
  'assets-without-rmm-agent',
  'count-of-assets-per-company',
  'dashboard-assets',
  'dashboard-assets-by-type',
  'dashboard-global-assets-by-company',
  'online-device-custom-field-last-30-days',
  'online-physical-servers-last-30-days',
  'online-virtual-servers-last-30-days',
  'online-workstations-last-30-days',
  'report-asset-custom-field-values',
  'report-assets-synced-last-30-days',
  'report-warranty-status',
  'stat-strip-report-asset-custom-fields',
  'stat-strip-report-assets',
  'stat-strip-report-warranty',
  'windows-10-computers-synced-last-30-days',
  'windows-10-computers-synced-last-60-days',
  'windows-10-computers-synced-last-90-days'
);

UPDATE reporting_queries SET category = 'Network and Discovery'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'discovered-devices-all',
  'discovered-devices-all-details',
  'discovered-devices-all-details-last-30-days',
  'discovered-devices-by-scanner-example',
  'discovered-devices-last-30-days',
  'global-subnet-scanners',
  'network-map-documentation-coverage'
);

UPDATE reporting_queries SET category = 'RMM'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-global-rmm-agents-by-company',
  'dashboard-rmm-devices-by-os',
  'report-rmm-devices',
  'rmm-agent-deployment-gaps',
  'rmm-agent-install-attempts-last-30-days',
  'rmm-agent-version-distribution',
  'rmm-agents-by-company',
  'rmm-agents-offline-over-7-days',
  'rmm-automation-open-tickets',
  'rmm-check-health-by-check',
  'rmm-checks-by-company',
  'rmm-checks-failing-now',
  'rmm-event-log-rules-activity',
  'rmm-finding-runs',
  'rmm-findings-by-category',
  'rmm-findings-open',
  'rmm-onboarding-runs-30-days',
  'rmm-onboarding-steps-by-company',
  'rmm-remote-sessions-30-days',
  'rmm-script-result-rules-activity',
  'stat-strip-report-rmm-devices',
  'stat-strip-rmm-overview'
);

UPDATE reporting_queries SET category = 'RMM Scripts'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-script-runs-by-day',
  'report-rmm-script-activity',
  'rmm-schedules',
  'rmm-script-run-outcomes-30-days',
  'rmm-script-runs-by-source-daily',
  'rmm-script-runs-by-technician',
  'rmm-script-runs-failed-last-7-days',
  'rmm-script-runs-in-flight',
  'rmm-scripts-library-usage',
  'rmm-ticket-script-runs'
);

UPDATE reporting_queries SET category = 'RMM Inventory and Software'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'report-software-inventory',
  'rmm-hardware-inventory',
  'rmm-inventory-long-uptime',
  'rmm-inventory-low-memory',
  'rmm-inventory-low-system-disk',
  'rmm-inventory-manufacturer-model',
  'rmm-inventory-os-builds',
  'rmm-inventory-stale',
  'rmm-software-change-volume-by-company',
  'rmm-software-changes-last-30-days',
  'rmm-software-chocolatey-version-gaps',
  'rmm-software-devices-with-application',
  'rmm-software-most-installed',
  'rmm-software-version-drift'
);

UPDATE reporting_queries SET category = 'Patching and Windows Updates'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-missing-updates-by-classification',
  'report-windows-updates-missing',
  'rmm-patch-policies',
  'rmm-windows-update-blocks',
  'rmm-windows-updates-failed-installs-30-days',
  'rmm-windows-updates-failure-codes',
  'rmm-windows-updates-install-history-monthly',
  'rmm-windows-updates-missing-by-classification',
  'rmm-windows-updates-missing-by-device',
  'rmm-windows-updates-most-missing',
  'rmm-windows-updates-reboot-pending',
  'rmm-windows-updates-scan-stale',
  'stat-strip-windows-updates'
);

UPDATE reporting_queries SET category = 'Health Checks'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-health-check-status',
  'health-check-detection-tickets',
  'health-check-groups',
  'health-check-remediations',
  'health-check-required-logs-open',
  'health-check-schedules',
  'health-checks-failed-runs',
  'health-checks-latest-by-asset',
  'health-checks-monthly-trend',
  'health-checks-run-outcomes-30-days',
  'health-checks-score-by-company',
  'report-health-checks',
  'stat-strip-health-checks',
  'stat-strip-report-health-checks'
);

UPDATE reporting_queries SET category = 'Tray'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'stat-strip-tray-overview',
  'tray-deployment-builds-failed',
  'tray-deployment-links',
  'tray-devices-by-version',
  'tray-devices-not-seen-30-days',
  'tray-enrolments-by-month'
);

UPDATE reporting_queries SET category = 'Security'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'dashboard-defender-active-detections-by-severity',
  'dashboard-defender-devices',
  'dashboard-defender-health-by-status',
  'dashboard-defender-unhealthy-devices',
  'defender-detections',
  'defender-device-status',
  'report-huntress-edr',
  'report-huntress-itdr',
  'report-huntress-managed-siem',
  'report-huntress-security-awareness-training',
  'report-huntress-soc',
  'stat-strip-defender-overview',
  'stat-strip-dmarc-overview',
  'stat-strip-report-huntress-edr',
  'stat-strip-report-huntress-itdr',
  'stat-strip-report-huntress-sat',
  'stat-strip-report-huntress-siem',
  'stat-strip-report-huntress-soc'
);

UPDATE reporting_queries SET category = 'Microsoft 365'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'office365-best-practice-status-by-company',
  'report-m365-best-practice-summary',
  'report-top-mailboxes-by-size',
  'stat-strip-m365-best-practices-live',
  'stat-strip-report-m365-best-practices'
);

UPDATE reporting_queries SET category = 'Compliance'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'compliance-assignment-status-by-company',
  'dashboard-compliance-by-status',
  'dashboard-compliance-checks',
  'report-customer-compliance-checks',
  'report-essential-8-compliance-progress',
  'report-smb1001-compliance-progress',
  'smb1001-compliance-by-company',
  'smb1001-evidence-by-company',
  'stat-strip-report-compliance',
  'stat-strip-report-essential-8',
  'stat-strip-report-smb1001'
);

UPDATE reporting_queries SET category = 'Backups and Continuity'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'backup-jobs-health-last-30-days',
  'backup-register',
  'continuity-plan-status-by-type',
  'dashboard-backup-jobs',
  'dashboard-backup-jobs-by-status',
  'report-backup-history',
  'stat-strip-backup-today',
  'stat-strip-bcp-readiness',
  'stat-strip-report-backups'
);

UPDATE reporting_queries SET category = 'Users and Staff'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'account-anonymisation-requests',
  'dashboard-global-staff-by-company',
  'dashboard-global-users',
  'dashboard-staff',
  'report-active-staff',
  'report-active-user-accounts',
  'stat-strip-report-active-users',
  'users-ai-opt-out-and-policy-acceptance',
  'users-email-domain-distribution',
  'users-inactive-by-company'
);

UPDATE reporting_queries SET category = 'Companies and Onboarding'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'client-onboarding-pipeline',
  'companies-pending-approval',
  'companies-user-and-asset-footprint',
  'dashboard-global-companies'
);

UPDATE reporting_queries SET category = 'Knowledge and Documentation'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'applications-importance-by-company',
  'applications-register',
  'dashboard-knowledge-articles',
  'kb-published-by-scope',
  'kb-recurring-issue-reviews',
  'report-applications'
);

UPDATE reporting_queries SET category = 'AI'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'agent-conversations-by-user-30-days',
  'dashboard-global-llm-requests-by-day',
  'llm-usage-by-feature-30-days',
  'llm-usage-daily',
  'llm-usage-errors-7-days',
  'rag-relationship-feedback-summary',
  'stat-strip-llm-usage',
  'stat-strip-rag-index',
  'stat-strip-rag-matching'
);

UPDATE reporting_queries SET category = 'Automation and Integrations'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'automations-failure-summary-last-14-days',
  'business-hours-closures-upcoming',
  'business-hours-deferred-automations',
  'dashboard-notifications',
  'stat-strip-notifications',
  'stat-strip-service-status-catalogue',
  'stat-strip-service-status-company',
  'webhook-events-by-name-and-response-code'
);

UPDATE reporting_queries SET category = 'Calls and Chat'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'chat-room-activity-last-14-days',
  'dashboard-calls-30-days',
  'dashboard-calls-by-day',
  'stat-strip-report-voice-monitor'
);

UPDATE reporting_queries SET category = 'Marketing'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'marketing-assistant-runs',
  'marketing-bounces-and-skips',
  'marketing-campaign-engagement-by-company',
  'marketing-campaign-performance',
  'marketing-opt-outs-by-month',
  'stat-strip-marketing-campaigns'
);

UPDATE reporting_queries SET category = 'Forms and Feedback'
WHERE category IS NULL AND is_system = 1 AND slug IN (
  'custom-form-submissions-by-form',
  'custom-form-submissions-recent',
  'dashboard-forms',
  'user-feedback-negative-recent',
  'user-feedback-summary'
);
