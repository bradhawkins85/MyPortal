# Labour Types Table - Visual Representation

## Before This Change

```
┌────────────────────────────────────────────────────────────────┐
│ Manage labour types                                            │
├────────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌──────────┬──────────────────────┬──────────┐              │
│  │ Code     │ Name                 │ Actions  │              │
│  ├──────────┼──────────────────────┼──────────┤              │
│  │ REMOTE   │ Remote Support       │ Remove   │              │
│  │ ONSITE   │ On-site Support      │ Remove   │              │
│  │ PHONE    │ Phone Support        │ Remove   │              │
│  └──────────┴──────────────────────┴──────────┘              │
│                                                                │
│  [ + Add labour type ]                                        │
│                                                                │
│  [ Save changes ]  [ Cancel ]                                 │
└────────────────────────────────────────────────────────────────┘
```

## After This Change

```
┌────────────────────────────────────────────────────────────────────────┐
│ Manage labour types                                                    │
├────────────────────────────────────────────────────────────────────────┤
│ Link technician time entries to Xero products by defining            │
│ reusable labour codes.                                                │
│                                                                        │
│  ┌──────────┬──────────────────────┬──────────────┬──────────┐       │
│  │ Code     │ Name                 │ Rate ($/hr)  │ Actions  │       │
│  ├──────────┼──────────────────────┼──────────────┼──────────┤       │
│  │ REMOTE   │ Remote Support       │   95.00      │ Remove   │       │
│  │ ONSITE   │ On-site Support      │  150.00      │ Remove   │       │
│  │ PHONE    │ Phone Support        │   75.00      │ Remove   │       │
│  └──────────┴──────────────────────┴──────────────┴──────────┘       │
│                                                                        │
│  [ + Add labour type ]                                                │
│                                                                        │
│  [ Save changes ]  [ Cancel ]                                         │
└────────────────────────────────────────────────────────────────────────┘
```

## Key Difference

**NEW COLUMN:** "Rate ($/hr)" - A number input field where administrators can enter the hourly billing rate for each labour type.

## Field Details

- **Type:** Number input
- **Format:** Decimal (e.g., 95.00, 150.00, 75.00)
- **Validation:** Minimum value of 0
- **Step:** 0.01 (allows cents)
- **Optional:** Can be left empty
- **Placeholder:** "0.00"

## Usage Flow

1. Admin clicks "Manage labour types" button
2. Modal opens showing labour types table with new Rate column
3. Admin can enter rates for each labour type:
   - Type a number like "95" or "95.00"
   - Use arrow keys to increment/decrement
   - Leave blank if no rate needed
4. Click "Save changes" to save all labour types with their rates

## Example Rates

| Labour Type Code | Name              | Typical Rate |
|-----------------|-------------------|--------------|
| REMOTE          | Remote Support    | $95/hour     |
| ONSITE          | On-site Support   | $150/hour    |
| PHONE           | Phone Support     | $75/hour     |
| EMERGENCY       | Emergency Support | $200/hour    |
| TRAINING        | Training          | $120/hour    |

## How Rates Are Used

When billing tickets to Xero, the system now:

1. **Checks local rate first** - Uses the rate from this table if set
2. **Falls back to Xero** - Queries Xero API if local rate is empty
3. **Uses default rate** - Uses global hourly rate if neither available

This means **faster billing** and **fewer API calls** to Xero!

## Benefits

✅ Set rates once in MyPortal  
✅ No need to configure in Xero  
✅ Faster ticket billing  
✅ Less API usage  
✅ More flexibility  

## Backward Compatibility

- Existing labour types without rates still work
- Empty rate fields fall back to Xero/default rates
- No changes required to existing setups
