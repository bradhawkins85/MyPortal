# Xero Line Item Description Template Investigation

## Issue Description
**Original Issue**: "The item description in Xero does not follow the 'Line item description template' set in the Xero module settings."

## Investigation Summary

After comprehensive analysis of the codebase, including code review, test execution, and creation of new tests, I determined that **the template mechanism is working correctly**. The issue appears to have been related to user understanding or documentation clarity rather than a code defect.

## Technical Analysis

### How the Template Works

1. **Template Retrieval** (`app/services/xero.py`, line 1355):
   ```python
   description_template = str(settings.get("line_item_description_template", "")).strip()
   ```
   The template is retrieved from the Xero module settings during the `sync_company` operation.

2. **Template Application** (`app/services/xero.py`, lines 1452-1457 and 1485-1490):
   ```python
   description = _format_line_description(
       description_template,
       ticket,
       group,
       group_minutes,
   )
   ```
   The template is applied to each billable ticket line item.

3. **Template Formatting** (`app/services/xero.py`, lines 90-121):
   The `_format_line_description` function:
   - Takes the template string
   - Substitutes placeholders with actual values from the ticket and labour data
   - Falls back to a default format if the template is empty or invalid
   - Handles missing placeholders gracefully (replaces with empty string)

### Available Placeholders

The template supports the following placeholders:
- `{ticket_id}` - The ticket number
- `{ticket_subject}` - The ticket subject/title
- `{ticket_status}` - The ticket status
- `{labour_name}` - Name of the labour type (e.g., "Remote Support")
- `{labour_code}` - Code of the labour type (e.g., "REMOTE")
- `{labour_minutes}` - Minutes spent (integer)
- `{labour_hours}` - Hours spent (decimal)
- `{labour_suffix}` - Formatted suffix like " · Remote Support" (includes labour name if present)

### Default Template

If no template is set or the template is empty/whitespace, the system uses:
```
Ticket {ticket_id}: {ticket_subject}{labour_suffix}
```

This produces descriptions like:
- `Ticket 123: Network Issue · Remote Support` (with labour type)
- `Ticket 123: Network Issue` (without labour type)

## Testing

### Existing Tests (All Passing)
- `test_build_ticket_invoices_respects_status_filters_and_templates` - Verifies template is applied correctly
- `test_sync_company_creates_line_items_with_labour_rates` - Verifies labour information is included

### New Tests Created (All Passing)
1. **`tests/test_template_usage.py::test_sync_company_uses_exact_template`**
   - Verifies that a custom template like `"CUSTOM: Ticket #{ticket_id} | Subject: {ticket_subject} | Labour: {labour_name}"` produces exactly the expected output
   - Confirms the template mechanism works end-to-end

2. **`tests/test_xero_template_edge_cases.py::test_empty_template_uses_default`**
   - Verifies that an empty template string falls back to the default template
   - Ensures graceful handling of misconfiguration

3. **`tests/test_xero_template_edge_cases.py::test_template_with_invalid_placeholder`**
   - Verifies that invalid placeholders (e.g., `{invalid_placeholder}`) are replaced with empty strings
   - Confirms the template doesn't break with user error

## Changes Made

### 1. Enhanced UI Documentation
**File**: `app/templates/admin/modules.html` (lines 300-305)

**Before**:
```html
<p class="form-help">
  Available placeholders include {ticket_id}, {ticket_subject}, {ticket_status}, 
  {labour_name}, {labour_code}, {labour_minutes}, {labour_hours}, and {labour_suffix}.
</p>
```

**After**:
```html
<p class="form-help">
  Template for invoice line item descriptions when syncing billable tickets to Xero. 
  Available placeholders: {ticket_id}, {ticket_subject}, {ticket_status}, {labour_name}, 
  {labour_code}, {labour_minutes}, {labour_hours}, and {labour_suffix}. 
  Default: "Ticket {ticket_id}: {ticket_subject}{labour_suffix}"
</p>
```

**Rationale**: The enhanced help text clarifies:
- **What** the template affects (invoice line items for billable tickets)
- **When** it's used (during Xero sync)
- **What** the default value is

### 2. Comprehensive Test Coverage
Added two new test files with 3 new test cases that specifically verify template behavior and edge cases.

## Root Cause Assessment

Based on the investigation, the most likely explanations for the reported issue are:

1. **User Expectation Mismatch**: 
   - The user may have expected the template to affect something other than invoice line item descriptions
   - For example, they might have expected it to affect the invoice reference or ticket fields

2. **Empty Template Configuration**:
   - If the template field was left empty, it would use the default template
   - The user may not have realized this was the expected behavior

3. **Documentation Clarity**:
   - The original help text didn't clearly explain what the template affected
   - Users might not have understood where to find the setting

4. **Caching or Timing**:
   - If the user updated the template but didn't trigger a new sync, they wouldn't see the change
   - This is normal behavior but might be confusing

## Recommendations

### For Users
1. **Setting the Template**: Navigate to Admin → Modules → Xero, and locate the "Line item description template" field
2. **Using Placeholders**: Use the documented placeholders (e.g., `{ticket_id}`, `{ticket_subject}`)
3. **Testing**: After changing the template, trigger a new Xero sync to see the changes
4. **Default Behavior**: Leaving the field empty will use the default template

### For Future Development
1. ✅ **Improved Documentation**: Now includes clearer help text explaining what the template affects
2. ✅ **Comprehensive Tests**: Now includes tests for edge cases and exact matching
3. **Consider**: Adding a template preview feature in the UI to show users what their template will produce
4. **Consider**: Adding validation to warn users about invalid placeholders

## Security Analysis

CodeQL analysis found **0 security alerts** in the changed code. The template substitution uses Python's `str.format_map()` which is safe from injection attacks when used with a controlled dictionary of values (which we do via the `_TemplateValues` class).

## Conclusion

The line item description template feature is working as designed and all tests confirm its correctness. The changes made improve documentation and test coverage, making it clearer for users how to use this feature effectively.

**Status**: ✅ Feature working correctly, documentation enhanced, comprehensive tests added.
