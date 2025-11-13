# Labour Type Rates - UI Documentation

## Overview
Added a new "Rate ($/hr)" column to the manage labour types table, allowing administrators to set billing rates directly in MyPortal instead of requiring them to be configured in Xero.

## UI Changes

### Manage Labour Types Modal
**Location:** Admin > Tickets > Manage Labour Types button

#### Before:
The table had 3 columns:
- Code
- Name  
- Actions

#### After:
The table now has 4 columns:
- Code
- Name
- **Rate ($/hr)** ← NEW
- Actions

### Field Details

**Rate ($/hr) Field:**
- Type: Number input
- Format: Decimal with 2 decimal places (e.g., 95.00)
- Validation: Minimum value of 0
- Optional: Field can be left empty
- Placeholder: "0.00"
- Name attribute: `labourRate`

### Example Usage

When creating or editing labour types, administrators can now enter rates such as:
- Remote Support: Code "REMOTE", Rate 95.00
- On-site Support: Code "ONSITE", Rate 150.00
- Phone Support: Code "PHONE", Rate 75.00

## Data Flow

When rates are set in the labour types:

1. **Rate Priority (Xero Billing):**
   - First: Use local labour type rate (if set)
   - Second: Use Xero item rate (if available via API)
   - Third: Use default hourly rate

2. **Storage:**
   - Rates are stored in the `ticket_labour_types` table
   - Column: `rate DECIMAL(10,2) NULL`
   - Nullable field for backward compatibility

3. **Form Submission:**
   - Rates are submitted via POST to `/admin/tickets/labour-types`
   - Processed alongside code and name fields
   - Empty rate fields are stored as NULL

## Benefits

1. **Reduced API Calls:** No need to fetch rates from Xero for every sync
2. **Faster Billing:** Local rates are immediately available
3. **Flexibility:** Can set rates even if Xero items don't exist
4. **Fallback Support:** Still falls back to Xero rates if local rate not set
5. **Easy Management:** All rate configuration in one place

## Technical Implementation

### Form Field HTML
```html
<td data-label="Rate ($/hr)">
  <label class="visually-hidden" for="labour-rate-{{ loop.index0 }}">Rate</label>
  <input
    id="labour-rate-{{ loop.index0 }}"
    name="labourRate"
    class="form-input"
    type="number"
    step="0.01"
    min="0"
    value="{{ labour_type.rate if labour_type.rate is not none else '' }}"
    placeholder="0.00"
  />
</td>
```

### Table Header
```html
<th scope="col">Rate ($/hr)</th>
```

## Backward Compatibility

- Existing labour types without rates will show empty rate fields
- Empty rates are stored as NULL in the database
- System continues to use Xero API rates when local rate is NULL
- No breaking changes to existing functionality
