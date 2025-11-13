# Labour Type Rates Implementation - Summary

## Objective
Add rates to the manage labour types table so these rates will be used for billing tickets to Xero instead of always trying to retrieve the price from Xero.

## Implementation Complete ✅

### Changes Made

#### 1. Database Schema (Migration 131)
- Added `rate DECIMAL(10,2) NULL` column to `ticket_labour_types` table
- Field is nullable for backward compatibility
- Placed after the `name` column

#### 2. Repository Layer (`app/repositories/labour_types.py`)
- Updated `list_labour_types()` to select rate field
- Updated `get_labour_type()` to select rate field
- Updated `get_labour_type_by_code()` to select rate field
- Updated `create_labour_type()` to accept and insert rate parameter
- Updated `update_labour_type()` to accept and update rate parameter
- Updated `replace_labour_types()` to handle rate in batch operations

#### 3. Service Layer (`app/services/labour_types.py`)
- Updated `create_labour_type()` to accept rate parameter
- Updated `update_labour_type()` to accept rate parameter
- Updated `replace_labour_types()` to process rate values from definitions

#### 4. API Layer
**Schemas (`app/schemas/tickets.py`):**
- Added `rate: Optional[float] = None` to `LabourTypeModel`
- Added `rate: Optional[float] = Field(default=None, ge=0)` to `LabourTypeUpdateRequest`
- Added `rate: Optional[float] = Field(default=None, ge=0)` to `LabourTypeCreateRequest`

**Routes (`app/api/routes/tickets.py`):**
- Updated `list_labour_types_endpoint()` to include rate in response
- Updated `create_labour_type_endpoint()` to pass rate parameter
- Updated `update_labour_type_endpoint()` to pass rate parameter

#### 5. Web UI (`app/templates/admin/tickets.html`)
- Added "Rate ($/hr)" column header to labour types table
- Added rate input field for each labour type row
- Input type: number, step: 0.01, min: 0, placeholder: "0.00"
- Field name: `labourRate`

#### 6. Form Handler (`app/main.py`)
- Updated `admin_replace_labour_types()` to:
  - Extract `labourRate` values from form
  - Parse rate strings to float values
  - Handle empty/invalid rates gracefully (stores as NULL)
  - Pass rates to service layer

#### 7. Xero Integration (`app/services/xero.py`)
**Updated ticket reply queries (`app/repositories/tickets.py`):**
- Added `labour_type_rate` to SELECT in joins with labour types

**Updated rate priority logic:**
- Modified `build_ticket_invoices()` to:
  1. First check for local labour type rate
  2. Fall back to Xero item rate if local rate is NULL
  3. Fall back to default hourly rate if no rates available

- Applied same logic in `sync_company()` function for consistency

#### 8. Tests (`tests/test_labour_type_rates.py`)
Created 6 comprehensive tests:
1. ✅ `test_create_labour_type_with_rate` - Verify creating with rate works
2. ✅ `test_update_labour_type_with_rate` - Verify updating rate works
3. ✅ `test_replace_labour_types_with_rates` - Verify batch operations work
4. ✅ `test_xero_uses_local_rate_first` - Verify local rate has priority
5. ✅ `test_xero_falls_back_to_xero_rate_when_no_local_rate` - Verify Xero fallback
6. ✅ `test_xero_falls_back_to_default_rate` - Verify default fallback

**All tests pass successfully!**

#### 9. Documentation
- Created `LABOUR_TYPE_RATES_UI.md` documenting the UI changes
- Includes field specifications, data flow, and benefits

## Rate Priority System

When billing tickets to Xero, the system now uses this priority:

```
1. Local Labour Type Rate (from ticket_labour_types.rate)
   ↓ (if NULL)
2. Xero Item Rate (from Xero API)
   ↓ (if not found)
3. Default Hourly Rate (from Xero settings)
```

## Benefits

1. **Performance**: Reduced API calls to Xero for every sync
2. **Speed**: Local rates are immediately available
3. **Flexibility**: Can set rates even if Xero items don't exist
4. **Control**: Centralized rate management in MyPortal
5. **Compatibility**: Maintains backward compatibility with existing setups

## Backward Compatibility

✅ **Fully backward compatible:**
- Rate field is nullable (NULL allowed)
- Existing labour types without rates continue to work
- System falls back to Xero API rates when local rate is NULL
- No breaking changes to existing functionality
- Migration adds column without requiring data

## Security

✅ **Security scan completed:**
- CodeQL analysis: 0 alerts found
- No security vulnerabilities introduced
- Input validation: rate >= 0 enforced
- Type safety: Decimal type used for monetary values

## Testing Status

✅ **All tests passing:**
- 6 new tests for labour type rates
- Existing Xero integration tests pass
- No regressions detected

## Files Changed

1. `migrations/131_labour_type_rates.sql` - Database migration
2. `app/repositories/labour_types.py` - Repository layer updates
3. `app/repositories/tickets.py` - Added rate to reply queries
4. `app/services/labour_types.py` - Service layer updates
5. `app/services/xero.py` - Rate priority logic
6. `app/schemas/tickets.py` - API schema updates
7. `app/api/routes/tickets.py` - API route updates
8. `app/main.py` - Form handler updates
9. `app/templates/admin/tickets.html` - UI updates
10. `tests/test_labour_type_rates.py` - New tests
11. `LABOUR_TYPE_RATES_UI.md` - Documentation

## Usage Example

**Admin Interface:**
1. Navigate to Admin > Tickets
2. Click "Manage labour types" button
3. Add/edit labour types with rates:
   - Code: REMOTE, Name: Remote Support, Rate: 95.00
   - Code: ONSITE, Name: On-site Support, Rate: 150.00
   - Code: PHONE, Name: Phone Support, Rate: 75.00
4. Click "Save changes"

**Result:**
When tickets are billed to Xero, the system will use these local rates instead of fetching from Xero, making billing faster and more reliable.

## Deployment Notes

1. Run migration 131 to add the rate column
2. No data migration needed - existing records will have NULL rates
3. Rates can be added to existing labour types via the admin UI
4. System continues to work with NULL rates (falls back to Xero)

## Conclusion

✅ **Implementation complete and tested**
✅ **All tests passing**
✅ **No security issues**
✅ **Backward compatible**
✅ **Documentation provided**

The feature is ready for production deployment and will significantly improve the efficiency of billing tickets to Xero by using local rates as the primary source.
