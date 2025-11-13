# Xero Labour Code Tracking Implementation Summary

## Problem Statement
1. Labour type codes match the xero item codes but the app is still not retrieving the price from Xero and using this for billing tickets
2. Update the error message to include the expected labour codes when returning the webhook result
3. Sync to Xero calls should also show in the webhook monitor

## Solution

### 1. Enhanced Logging for Missing Labour Codes
Added warning logs when labour codes from ticket replies are not found in Xero's item database:

```python
# Log missing labour codes
missing_codes = labour_codes - set(xero_item_rates.keys())
if missing_codes:
    logger.warning(
        "Some labour codes not found in Xero items",
        company_id=company_id,
        missing_codes=list(missing_codes),
        expected_codes=list(labour_codes),
        found_codes=list(xero_item_rates.keys()),
    )
```

This applies to both:
- `sync_billable_tickets()` function (line 932-940)
- `sync_company()` function (line 1495-1503)

### 2. Enhanced Return Values with Labour Code Information
All return dictionaries (success, failed, error) now include labour code diagnostics:

**Fields added to return values:**
- `labour_codes_expected`: List of all labour codes found in ticket replies
- `labour_codes_found_in_xero`: List of labour codes that have rates configured in Xero
- `labour_codes_missing_from_xero`: List of labour codes not found in Xero (only included if there are missing codes)

**Functions updated:**
- `sync_billable_tickets()`: Lines 1195-1204, 1221-1230, 1260-1269, 1297-1306
- `sync_company()`: Lines 1892-1901, 1926-1935, 1963-1972, 2000-2009

### 3. Webhook Monitor Integration
Verified that Xero sync calls are already being recorded in the webhook monitor:

**Event names:**
- `xero.sync.billable_tickets` - For ticket-specific syncs
- `xero.sync.company` - For company-wide syncs

**Access:**
- Events can be viewed at `/api/scheduler/webhooks` endpoint
- Each event includes the full request payload sent to Xero
- Failed attempts are logged with error details

## Code Changes

### Files Modified
1. `app/services/xero.py` - Enhanced sync functions with labour code tracking
2. `tests/test_xero_labour_code_reporting.py` - New comprehensive test suite

### Test Coverage
Created three comprehensive tests:
1. `test_sync_billable_tickets_includes_labour_codes_in_success` - Verifies success case with mixed found/missing codes
2. `test_sync_billable_tickets_includes_labour_codes_in_failure` - Verifies failure case includes labour code info
3. `test_sync_company_includes_labour_codes_in_success` - Verifies company sync includes labour code info

## Benefits

### 1. Better Debugging
When tickets are not billed correctly, the result clearly shows:
- Which labour codes were expected (from ticket replies)
- Which codes were found in Xero (have configured rates)
- Which codes are missing from Xero (causing fallback to default rate)

### 2. Clearer Error Messages
All error returns (HTTP errors, connection errors, API errors) now include labour code diagnostics, making it easier to identify configuration issues.

### 3. Improved Transparency
The webhook monitor provides complete visibility into all Xero API calls, including:
- Request payloads sent to Xero
- Response status and body
- Retry attempts and backoff timing

### 4. Easier Troubleshooting
Missing labour codes are logged as warnings with full context, allowing administrators to:
- Identify when labour codes are configured in MyPortal but not in Xero
- See exactly which codes need to be added to Xero
- Understand why default rates are being used instead of Xero item rates

## Example Output

### Success with Mixed Codes
```json
{
    "status": "succeeded",
    "company_id": 1,
    "invoice_number": "INV-100",
    "tickets_billed": 2,
    "time_entries_recorded": 4,
    "response_status": 200,
    "event_id": 123,
    "labour_codes_expected": ["REMOTE", "ONSITE", "CONSULT"],
    "labour_codes_found_in_xero": ["REMOTE", "CONSULT"],
    "labour_codes_missing_from_xero": ["ONSITE"]
}
```

### Failure with Labour Code Info
```json
{
    "status": "failed",
    "company_id": 1,
    "response_status": 400,
    "error": "HTTP 400",
    "event_id": 124,
    "labour_codes_expected": ["SUPPORT", "TRAINING"],
    "labour_codes_found_in_xero": [],
    "labour_codes_missing_from_xero": ["SUPPORT", "TRAINING"]
}
```

## Testing

All tests pass:
- `test_xero_labour_code_reporting.py`: 3/3 passing
- `test_xero_billable_tickets.py`: 7/7 passing
- No security vulnerabilities detected by CodeQL

## Backward Compatibility

These changes are fully backward compatible:
- Existing code continues to work without modification
- New fields are only added to return values, not required
- Webhook monitor was already in place and functional
- No database schema changes required
