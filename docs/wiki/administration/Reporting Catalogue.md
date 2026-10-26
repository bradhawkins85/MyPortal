# Reporting Catalogue

The **Reporting** menu (`/reporting`, managed from `/admin/reporting`) ships
with a catalogue of system reports. Super admins can run them, export them,
grant technicians access, clone them to change filters, and reuse them in
configurable dashboards, stat strips and company reports.

Migration `523_reporting_catalogue_new_features.sql` adds the entries below
for the RMM, health check, tray, Windows Update, warranty, AI usage, marketing
campaign, custom form, client onboarding and register features. Entries are
inserted with `INSERT IGNORE` on the slug, so an administrator's edits to an
existing entry are never overwritten.

## Conventions

- **Global reports** are named `<Area>: <Report>` and cover every company.
- **Company report sections** are named `Report - …`, and **company stat
  strips** `Stat Strip - Company Overview - …`. Both filter on
  `{{current.company}}`, so add them to a company report layout or run them
  with a company selected.
- **Dashboard widgets** are named `Dashboard - …` and return `X` and `Y`
  columns for charts. `Dashboard - Global …` widgets cover all companies.
- *Online* means an RMM agent checked in within the last 15 minutes. Agent and
  device times are UTC.
- Reporting redacts any column whose name contains *token*, so AI usage
  reports label token totals `input_tok` and `output_tok`.
- Reports that filter on an example value (for example
  *RMM Software: Devices With an Application*, which matches `Chrome`) are
  meant to be cloned and edited.

## RMM agents and deployment

| Report | Slug | What it shows |
| --- | --- | --- |
| RMM: Agent Coverage and Status by Company | `rmm-agents-by-company` | Active RMM agents per company with online (seen in the last 15 minutes), offline, offline-over-7-days and per-platform counts. |
| RMM: Agents Offline for More Than 7 Days | `rmm-agents-offline-over-7-days` | Active RMM agents that have not checked in for over 7 days, oldest first, with the linked asset and days offline. |
| RMM: Agent Version Distribution | `rmm-agent-version-distribution` | Active RMM agents grouped by platform and agent version, with how many support script files, run-as-user and run-anyway. |
| RMM: Agent Install Attempts (Last 30 Days) | `rmm-agent-install-attempts-last-30-days` | RMM agent deployment attempts in the last 30 days by company, source (auto, manual, repair) and outcome. |
| RMM: Tray Devices Without an RMM Agent | `rmm-agent-deployment-gaps` | Active tray devices that have no active RMM agent yet, with the company's automatic deployment setting and the latest install attempt. |
| RMM: Patch Policies | `rmm-patch-policies` | Windows Update and Chocolatey patch policies with scope, schedule, next run and last change. |
| RMM: Remote Control Sessions (Last 30 Days) | `rmm-remote-sessions-30-days` | Remote control sessions in the last 30 days per technician and provider, with how many became ready and the average time to connect. |
| RMM: Device Onboarding Runs (Last 30 Days) | `rmm-onboarding-runs-30-days` | Automatic device onboarding runs in the last 30 days per company: completed, failed and still running, with failed steps and average duration. |
| RMM: Onboarding Steps by Company | `rmm-onboarding-steps-by-company` | Configured device onboarding steps per company in order, with the script or package step, run-as account and failure handling. |

## RMM inventory and software

| Report | Slug | What it shows |
| --- | --- | --- |
| RMM Inventory: Hardware Summary | `rmm-hardware-inventory` | Latest RMM hardware inventory per device: manufacturer, model, serial, BIOS, CPU, memory, disk capacity, free system disk and last boot. |
| RMM Inventory: Devices by Manufacturer and Model | `rmm-inventory-manufacturer-model` | Device counts by manufacturer and model from RMM inventory, with average memory and the oldest and newest BIOS seen. |
| RMM Inventory: Operating System Builds | `rmm-inventory-os-builds` | Device counts by operating system name, version and build from RMM inventory, to spot out-of-date feature releases. |
| RMM Inventory: Low System Disk Space | `rmm-inventory-low-system-disk` | Devices whose system disk has less than 15% or less than 20 GB free, lowest free space first. |
| RMM Inventory: Devices With Less Than 8 GB RAM | `rmm-inventory-low-memory` | Devices reporting under 8 GB of memory in RMM inventory, typically hardware refresh candidates. |
| RMM Inventory: Devices Not Restarted in 30 Days | `rmm-inventory-long-uptime` | Online RMM devices whose last boot was more than 30 days ago, longest uptime first. |
| RMM Inventory: Stale or Missing Inventory | `rmm-inventory-stale` | Active RMM agents with no inventory or an inventory older than 7 days. |
| RMM Software: Most Installed Applications | `rmm-software-most-installed` | Applications from RMM software inventory ranked by the number of devices they are installed on, with version and company spread. |
| RMM Software: Applications Running Multiple Versions | `rmm-software-version-drift` | Applications installed in more than one version across active devices, with the number of devices on each version. |
| RMM Software: Devices With an Application | `rmm-software-devices-with-application` | Every device with a matching application installed. Clone and replace "Chrome" in the filter with the application to find. |
| RMM Software: Installs and Removals (Last 30 Days) | `rmm-software-changes-last-30-days` | Software first seen or removed on a device in the last 30 days from RMM software history, newest first. |
| RMM Software: Change Volume by Company (Last 30 Days) | `rmm-software-change-volume-by-company` | Counts of software installs, upgrades and removals detected by RMM Findings per company in the last 30 days. |
| RMM Software: Chocolatey Packages Behind the Catalogue | `rmm-software-chocolatey-version-gaps` | Installed applications matched to a Chocolatey package whose installed version differs from the latest version in the Chocolatey catalogue. |

## Windows Updates

| Report | Slug | What it shows |
| --- | --- | --- |
| Windows Updates: Missing Updates by Device | `rmm-windows-updates-missing-by-device` | Latest Windows Update scan per device with missing, security and critical update counts, pending reboot and scan age. |
| Windows Updates: Most Commonly Missing Updates | `rmm-windows-updates-most-missing` | Missing (not hidden) Windows updates ranked by the number of devices that need them, with KB, classification and severity. |
| Windows Updates: Missing Updates by Company and Classification | `rmm-windows-updates-missing-by-classification` | Missing (not hidden) Windows updates per company split by classification, counting update instances and affected devices. |
| Windows Updates: Failed Installs (Last 30 Days) | `rmm-windows-updates-failed-installs-30-days` | Windows update installs that failed in the last 30 days, with the HRESULT error code and the application that ran the install. |
| Windows Updates: Install Failures by Error Code (Last 90 Days) | `rmm-windows-updates-failure-codes` | Failed Windows update installs in the last 90 days grouped by HRESULT so recurring causes stand out. |
| Windows Updates: Install Results by Month | `rmm-windows-updates-install-history-monthly` | Windows update install operations per month over the last 12 months: succeeded, succeeded with errors, failed and cancelled. |
| Windows Updates: Devices Awaiting a Reboot | `rmm-windows-updates-reboot-pending` | Active devices whose latest Windows Update scan reports a pending reboot, with last boot and check-in times. |
| Windows Updates: Devices Not Scanned in 7 Days | `rmm-windows-updates-scan-stale` | Active Windows RMM agents with no Windows Update scan, or whose last scan is more than 7 days old. |
| Windows Updates: Blocked Updates Register | `rmm-windows-update-blocks` | Windows updates blocked from installation, with scope (all companies, a company or a single asset), reason and who blocked them. |

## RMM scripts

| Report | Slug | What it shows |
| --- | --- | --- |
| RMM Scripts: Run Outcomes by Script (Last 30 Days) | `rmm-script-run-outcomes-30-days` | Script runs in the last 30 days per script with completed, failed, timed-out, expired/cancelled counts, success rate and average duration. |
| RMM Scripts: Failed and Timed-Out Runs (Last 7 Days) | `rmm-script-runs-failed-last-7-days` | Script runs that failed or timed out in the last 7 days with device, exit code, error message and how the run was started. |
| RMM Scripts: Daily Run Volume by Source (Last 30 Days) | `rmm-script-runs-by-source-daily` | Script runs per day over the last 30 days split by source: manual, schedule, onboarding, patch, ticket, check, event log and result rule. |
| RMM Scripts: Runs by Technician (Last 30 Days) | `rmm-script-runs-by-technician` | Manually requested script runs per technician in the last 30 days, with the number of scripts, devices and companies touched. |
| RMM Scripts: Queued and Running for Over an Hour | `rmm-script-runs-in-flight` | Script runs still queued, dispatched or running more than an hour after they were queued, oldest first. |
| RMM Scripts: Library Usage (Last 90 Days) | `rmm-scripts-library-usage` | Every active script in the library with its scope, platforms, timeout, run count and last run in the last 90 days; unused scripts are listed first. |
| RMM Scripts: Scheduled Script Overview | `rmm-schedules` | Scheduled scripts with scope, cron, run-as account, next and last run and the last run summary. |
| RMM Scripts: Runs Triggered From Tickets (Last 30 Days) | `rmm-ticket-script-runs` | Script runs launched by the ticket automation action in the last 30 days, with the ticket, result and when it was reported back. |

## RMM checks, rules and findings

| Report | Slug | What it shows |
| --- | --- | --- |
| RMM Checks: Devices Currently Failing or Warning | `rmm-checks-failing-now` | Check results currently failing or warning, with the value seen, how long the problem has lasted and any linked ticket. |
| RMM Checks: Result Summary by Check | `rmm-check-health-by-check` | Every RMM check with passing, problem, failing and waiting device counts and the last time it failed. |
| RMM Checks: Problems by Company | `rmm-checks-by-company` | Check results per company for active agents: passing, warning and failing counts and devices with at least one problem. |
| RMM: Event Log Rule Activity | `rmm-event-log-rules-activity` | Event log rules with scope, action, match count, last match, devices triggered and open tickets they raised. |
| RMM: Script Result Rule Activity | `rmm-script-result-rules-activity` | Script result rules with the script they watch, the action taken, match count, last match and open tickets they raised. |
| RMM: Open Tickets Raised by Automation | `rmm-automation-open-tickets` | Open tickets raised by RMM checks, event log rules, script result rules and health check detections, with the rule or detection that raised them. |
| RMM Findings: Open Findings by Severity | `rmm-findings-open` | Active RMM findings that are open or acknowledged, most severe first, with category, occurrences and first and last seen. |
| RMM Findings: Summary by Category and Severity | `rmm-findings-by-category` | Active RMM findings grouped by category and severity with open, acknowledged and dismissed counts. |
| RMM Findings: Analysis Run History (Last 30 Days) | `rmm-finding-runs` | RMM Findings analysis runs in the last 30 days with trigger, status, AI analysis status and model, findings produced and duration. |

## Health checks

| Report | Slug | What it shows |
| --- | --- | --- |
| Health Checks: Latest Result per Device | `health-checks-latest-by-asset` | The most recent completed AI health check for each device with overall status, health score, platform and summary, worst first. |
| Health Checks: Device Health by Company | `health-checks-score-by-company` | Per company, the latest completed health check per device summarised as average score and healthy, attention and critical device counts. |
| Health Checks: Monthly Score Trend | `health-checks-monthly-trend` | Completed health checks per month over the last 12 months with average score and the share of healthy, attention and critical results. |
| Health Checks: Run Outcomes (Last 30 Days) | `health-checks-run-outcomes-30-days` | Health check runs in the last 30 days by source (manual, schedule, ticket) and status, with average time to complete. |
| Health Checks: Failed Runs (Last 30 Days) | `health-checks-failed-runs` | Health check runs that failed in the last 30 days with the device, last stage reached and error. |
| Health Checks: Monthly Schedule Status | `health-check-schedules` | Scheduled monthly health checks per device with the next window, last start and result, flagging windows that ended without a run. |
| Health Checks: Tickets by Detection | `health-check-detection-tickets` | Tickets raised by health check detections grouped by detection, with open and total ticket counts and devices affected. |
| Health Checks: Remediation Activity | `health-check-remediations` | Automatic remediations configured for health check detections with scope, script, ticket option, run count and last result. |
| Health Checks: Open Required Logs | `health-check-required-logs-open` | Logs the AI health check and troubleshooter asked for but could not read, still open for review, most frequent first. |
| Health Checks: Device Rule Groups | `health-check-groups` | Health check device-rule groups with scope, whether they are enabled or the default, and when they last changed. |

## Tray

| Report | Slug | What it shows |
| --- | --- | --- |
| Tray: Devices by App Version and Platform | `tray-devices-by-version` | Active tray devices by platform and app version with how many checked in within the last day. |
| Tray: Devices Not Seen in 30 Days | `tray-devices-not-seen-30-days` | Active tray devices that have not checked in for over 30 days, with the last user, IP and version. |
| Tray: Device Enrolments by Month | `tray-enrolments-by-month` | Tray devices enrolled per month over the last 12 months by platform. |
| Tray: Deployment URL Usage | `tray-deployment-links` | Tray deployment URLs per company with downloads, last download, expiry, revocation and the latest installer build status. |
| Tray: Failed Installer Builds (Last 30 Days) | `tray-deployment-builds-failed` | Tray deployment installer builds that failed in the last 30 days with the company, release and error. |

## Assets, warranty and network map

| Report | Slug | What it shows |
| --- | --- | --- |
| Assets: Warranties Expiring in the Next 90 Days | `assets-warranty-expiring-next-90-days` | Active assets whose warranty ends in the next 90 days, with the warranty source and when it was last looked up. |
| Assets: Out of Warranty | `assets-out-of-warranty` | Active assets whose warranty has already ended, longest expired first. |
| Assets: Warranty Lookup Coverage by Company | `assets-warranty-lookup-status` | Per company, active assets with a warranty found, not found, unsupported, errored or never looked up. |
| Assets: Count by Asset Type and Company | `assets-by-asset-type` | Active assets per company and asset type (as used by the network map and rack views), with how many were classified automatically. |
| Assets: Count by Tag | `assets-by-tag` | Tags with the number of active assets carrying them, split into manually applied and automatically applied. |
| Assets: Workstations and Servers Without RMM | `assets-without-rmm-agent` | Active assets classified as workstations, laptops, thin clients, servers, hypervisors or virtual machines that have no active RMM agent. |
| Network Map: Documentation Coverage by Company | `network-map-documentation-coverage` | Per company, documented network interfaces and links by medium (copper, fibre, wireless and other), to show which sites have a mapped network. |

## AI usage, feedback and privacy

| Report | Slug | What it shows |
| --- | --- | --- |
| AI Usage: Requests and Tokens by Feature (Last 30 Days) | `llm-usage-by-feature-30-days` | LLM requests in the last 30 days per feature and model with error count, input and output tokens (input_tok, output_tok) and average latency. |
| AI Usage: Daily Requests and Tokens (Last 30 Days) | `llm-usage-daily` | LLM requests, failures and input and output tokens (input_tok, output_tok) per day for the last 30 days. |
| AI Usage: Failed Requests by Feature (Last 7 Days) | `llm-usage-errors-7-days` | LLM requests that errored or failed in the last 7 days per feature, provider and model with the last failure time. |
| Feedback: Ratings by Page (Last 90 Days) | `user-feedback-summary` | Thumbs up and down feedback in the last 90 days grouped by page, with the satisfaction percentage. |
| Feedback: Recent Negative Feedback | `user-feedback-negative-recent` | Thumbs-down feedback from the last 30 days with the reason, suggested improvement, page and linked ticket. |
| AI Quality: Related-Item Feedback by Type | `rag-relationship-feedback-summary` | Technician ratings of AI related-item suggestions grouped by relationship and target type, with the helpful percentage and average scores. |
| AI Agent: Conversations by User (Last 30 Days) | `agent-conversations-by-user-30-days` | AI agent conversations started in the last 30 days per user, with total turns and the last conversation time. |
| Users: AI Opt-Out and Policy Acceptance by Company | `users-ai-opt-out-and-policy-acceptance` | Active users per company with how many opted out of AI features, accepted the legal policies, or have never accepted them. |
| Users: Account Anonymisation Requests | `account-anonymisation-requests` | Account anonymisation (right to erasure) requests with status, who decided, when executed and the support ticket. |

## Marketing

| Report | Slug | What it shows |
| --- | --- | --- |
| Marketing: Campaign Performance | `marketing-campaign-performance` | Marketing campaigns with recipients, sent, delivered, opened, clicked, bounced, skipped and replied counts, plus open, click and reply rates. |
| Marketing: Campaign Engagement by Company | `marketing-campaign-engagement-by-company` | Marketing email engagement per recipient company across all campaigns: emails sent, opened, clicked, replied and bounced. |
| Marketing: Bounced and Skipped Recipients | `marketing-bounces-and-skips` | Campaign recipients that bounced, failed or were skipped, with the skip reason or error, to clean up contact data. |
| Marketing: Email Opt-Outs by Month and Category | `marketing-opt-outs-by-month` | Marketing email opt-outs per month and category over the last 12 months, with how many came from a campaign. |
| Marketing: AI Assistant Runs | `marketing-assistant-runs` | Marketing page assistant runs with page, mode, status, sources read, sections written, images used and claims verified or rejected. |

## Forms, onboarding, registers and other

| Report | Slug | What it shows |
| --- | --- | --- |
| Custom Forms: Submissions by Form | `custom-form-submissions-by-form` | Custom forms with field count, total submissions, submissions in the last 30 days, tickets raised, active share links and last submission. |
| Custom Forms: Recent Submissions | `custom-form-submissions-recent` | Custom form submissions in the last 30 days with the form, submitter and resulting ticket. |
| Client Onboarding: Invitation Pipeline | `client-onboarding-pipeline` | Client onboarding invitations with status, recipient, expiry, submission and approval times and the resulting company. |
| Companies: Awaiting Approval | `companies-pending-approval` | Companies created by client onboarding that are still awaiting approval, with the onboarding submission time. |
| Applications: Register by Company | `applications-register` | Documented line-of-business applications per company with type, version, importance, champion and linked knowledge base articles. |
| Applications: Importance Summary by Company | `applications-importance-by-company` | Count of documented applications per company by importance, and how many have no champion or no linked documentation. |
| Backups: Backup Register | `backup-register` | Documented backups per company with backup application, frequency, linked monitored job and its latest status. |
| Automations: Deferred by Business Hours | `business-hours-deferred-automations` | Automation runs deferred until business hours, with status, when they are due and any error, newest first. |
| Business Hours: Upcoming Closures | `business-hours-closures-upcoming` | Global and company-specific business hours closures from today onwards. |
| SMB1001: Compliance Status by Company | `smb1001-compliance-by-company` | SMB1001 control status per company: compliant or not applicable, in progress, non-compliant and not started, with the completion percentage. |
| SMB1001: Evidence Uploaded by Company | `smb1001-evidence-by-company` | Current SMB1001 evidence files per company with the number of controls evidenced and the last upload. |
| Tickets: Open Parent Tickets With Linked Child Tickets | `tickets-parent-child-tasks` | Open tickets that have child tickets (created from tasks or linked as sub-tickets), with how many children are still open. |
| Knowledge Base: Recurring Issue Reviews | `kb-recurring-issue-reviews` | Recurring-issue clusters reviewed for knowledge base articles: whether an article was written or the cluster ignored, and who reviewed it. |

## Company report sections (current company)

| Report | Slug | What it shows |
| --- | --- | --- |
| Report - RMM Devices | `report-rmm-devices` | The current company's RMM-managed devices with platform, OS, last check-in, free system disk, missing updates, pending reboot and latest health score. |
| Report - Windows Updates Missing | `report-windows-updates-missing` | Windows updates still missing on the current company's devices, by update, with the number of devices affected. |
| Report - Device Health Checks | `report-health-checks` | The latest completed AI health check for each of the current company's devices with status, score and summary. |
| Report - Asset Warranty Status | `report-warranty-status` | The current company's active assets with warranty dates, expired or expiring flags and days remaining. |
| Report - Software Inventory | `report-software-inventory` | Applications installed across the current company's RMM-managed devices with device counts and versions. |
| Report - Maintenance Script Activity (Last 30 Days) | `report-rmm-script-activity` | Scripts run on the current company's devices in the last 30 days with run, completed and failed counts. |
| Report - Applications Register | `report-applications` | The current company's documented applications with type, version, importance and champion. |

## Stat strips

| Report | Slug | What it shows |
| --- | --- | --- |
| Stat Strip - RMM - Fleet Overview | `stat-strip-rmm-overview` | Active RMM agents, online now, offline over 7 days, failing checks, script runs and failures today, and open findings. |
| Stat Strip - Windows Updates - Overview | `stat-strip-windows-updates` | Devices scanned, devices with missing updates, missing security updates, devices awaiting a reboot and failed installs in the last 30 days. |
| Stat Strip - Health Checks - Overview | `stat-strip-health-checks` | Devices with a completed health check, average score, healthy, attention and critical devices, and health checks run in the last 30 days. |
| Stat Strip - AI Usage - Last 30 Days | `stat-strip-llm-usage` | LLM requests, failures, input and output tokens (input_tok, output_tok), features used and average latency over the last 30 days. |
| Stat Strip - Tray - Overview | `stat-strip-tray-overview` | Active tray devices, seen in the last day, not seen in 30 days, Windows and macOS devices and active deployment URLs. |
| Stat Strip - Marketing - Campaigns | `stat-strip-marketing-campaigns` | Campaigns sent, emails sent, opened, clicked, replied and bounced across all marketing campaigns, and opt-outs in the last 30 days. |
| Stat Strip - Company Overview - RMM Devices | `stat-strip-report-rmm-devices` | The current company's RMM devices: total, online, offline, awaiting reboot, devices with missing updates and failing checks. |
| Stat Strip - Company Overview - Device Health | `stat-strip-report-health-checks` | The current company's latest health check per device: devices checked, average score, healthy, attention and critical. |
| Stat Strip - Company Overview - Warranty | `stat-strip-report-warranty` | The current company's active assets by warranty: covered, expiring in 90 days, expired and unknown. |

## Dashboard widgets (X/Y)

| Report | Slug | What it shows |
| --- | --- | --- |
| Dashboard - RMM devices by operating system | `dashboard-rmm-devices-by-os` | The current company's active RMM devices grouped by operating system (X) with device counts (Y). |
| Dashboard - Missing Windows updates by classification | `dashboard-missing-updates-by-classification` | The current company's missing (not hidden) Windows updates by classification (X) with update counts (Y). |
| Dashboard - Device health status | `dashboard-health-check-status` | The current company's latest health check per device by overall status (X) with device counts (Y). |
| Dashboard - RMM script runs by day | `dashboard-script-runs-by-day` | Script runs on the current company's devices per day (X) over the last 30 days (Y). |
| Dashboard - Global AI requests by day | `dashboard-global-llm-requests-by-day` | LLM requests per day (X) across the portal over the last 30 days (Y). |
| Dashboard - Global RMM agents by company | `dashboard-global-rmm-agents-by-company` | Active RMM agents per company (X) across the portal (Y). |
