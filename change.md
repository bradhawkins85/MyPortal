# Change Log

- 2025-09-17, 06:44 UTC, Feature, Setup change log file
- 2025-09-17, 06:59 UTC, Feature, Consolidated Company Assets column toggles into a secure header view menu
- 2025-09-17, 07:02 UTC, Fix, Added server-rendered CSRF tokens to Apps management forms to prevent invalid token errors when saving changes
- 2025-09-17, 07:08 UTC, Fix, Repositioned asset search/view controls above the table with a default-collapsed column accordion
- 2025-09-17, 07:18 UTC, Fix, Added CSRF protection tokens to scheduled task forms to prevent invalid token errors when managing schedules
- 2025-09-17, 07:26 UTC, Feature, Split scheduled tasks into system and company tables for clearer administration and visibility
- 2025-09-17, 07:38 UTC, Feature, Added automated product DBP threshold alerts with super admin email and popup notifications
- 2025-09-17, 07:47 UTC, Fix, Normalised Syncro CPUAge import to populate asset Approx Age
- 2025-09-17, 07:54 UTC, Feature, Added OpnForm deployment guidance, nginx proxy config, and super admin builder link
- 2025-09-17, 09:16 UTC, Fix, Updated CSP frame policy to allow embedding OpnForm frames from form.hawkinsit.au
- 2025-09-17, 09:34 UTC, Feature, Added dynamic template variables for app URLs and documented usage in README
- 2025-09-17, 13:27 UTC, Fix, Implemented authenticated Hawkins forms proxy with UI fallback to bypass X-Frame-Options errors
- 2025-09-17, 13:34 UTC, Fix, Rewrote proxied form asset URLs to avoid cross-origin module preloads failing via CORS
- 2025-09-17, 13:44 UTC, Fix, Forced proxied form assets to use the portal origin so scripts and styles load without CORS failures
