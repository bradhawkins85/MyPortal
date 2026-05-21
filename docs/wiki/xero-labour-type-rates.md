# Xero Labour Type Rates

This guide explains how to set up different billing rates for different types of work using Xero items.

## Overview

Instead of using a single default hourly rate for all billable time, MyPortal can automatically fetch different rates from Xero based on the type of work performed. For example:

- Remote Support: $95/hour
- On-site Support: $150/hour
- After Hours Support: $200/hour
- Project Work: $175/hour

## How It Works

1. **Labour Types in MyPortal**: Each labour type has a unique **code** (e.g., "REMOTE", "ONSITE")
2. **Items in Xero**: Create items with matching codes and set their unit prices
3. **Automatic Lookup**: When syncing, MyPortal fetches the unit price for each labour type from Xero
4. **Invoice Generation**: Line items use the Xero item price instead of the default hourly rate

## Setup Instructions

### Step 1: Create Labour Types in MyPortal

1. Navigate to **Admin > Tickets > Labour Types**
2. Click **Add Labour Type**
3. Enter a unique **Code** (e.g., "REMOTE") - this must match the Xero item code exactly
4. Enter a descriptive **Name** (e.g., "Remote Support")
5. Click **Save**

Repeat for each type of work you want to bill separately.

**Example Labour Types:**

| Code | Name |
|------|------|
| REMOTE | Remote Support |
| ONSITE | On-site Support |
| AFTERHOURS | After Hours Support |
| PROJECT | Project Work |

### Step 2: Create Matching Items in Xero

1. Log in to Xero
2. Navigate to **Business > Products and Services** (or **Inventory > Items** depending on your Xero version)
3. Click **New Item**
4. Set the **Item Code** to match your MyPortal labour type code exactly (e.g., "REMOTE")
5. Set the **Item Name** (e.g., "Remote Support")
6. Under **Sales Details**:
   - Select your sales account (usually "Sales" or "Service Revenue")
   - Set the **Unit Price** (e.g., $95.00)
   - Set the **Tax Rate** as appropriate
7. Click **Save**

Repeat for each labour type.

**Important Notes:**
- ⚠️ **Item codes are case-sensitive**: "REMOTE" ≠ "remote" ≠ "Remote"
- ✅ Use uppercase codes for consistency
- ✅ Codes must be unique in both MyPortal and Xero
- ✅ The Unit Price in Xero determines the hourly rate

### Step 3: Configure Default Hourly Rate (Fallback)

The default hourly rate in MyPortal (**Admin > Modules > Xero**) is used when:
- A labour type doesn't have a code set
- No matching Xero item is found
- The Xero item doesn't have a unit price configured

## Usage

### Adding Time to Tickets

When technicians add time entries to tickets:

1. Enter the **Minutes Spent**
2. Check **Billable** if the time should be invoiced
3. Select a **Labour Type** from the dropdown
4. Add notes as needed
5. Save the time entry

The labour type code is automatically associated with the time entry.

### Syncing to Xero

When the "Sync to Xero" scheduled task runs:

1. MyPortal identifies tickets in billable statuses
2. Groups time entries by labour type
3. **Fetches the unit price for each labour type code from Xero**
4. Creates invoice line items using the Xero prices
5. Falls back to default hourly rate if no Xero item is found

**Example Invoice Line Items:**

```
Remote Support: 2.5 hours × $95.00 = $237.50
On-site Support: 1.0 hour × $150.00 = $150.00
```

## Verification

### Check Labour Type Codes

```sql
SELECT * FROM ticket_labour_types ORDER BY code;
```

### Check Time Entries with Labour Types

```sql
SELECT 
    tr.id,
    tr.ticket_id,
    tr.minutes_spent,
    tr.is_billable,
    lt.code AS labour_code,
    lt.name AS labour_name
FROM ticket_replies tr
LEFT JOIN ticket_labour_types lt ON tr.labour_type_id = lt.id
WHERE tr.is_billable = 1
ORDER BY tr.created_at DESC
LIMIT 10;
```

### Check Application Logs

When syncing, look for log messages like:

```
Fetching Xero item rates for labour types
  company_id=1
  labour_codes=['REMOTE', 'ONSITE']

Fetched Xero item rates
  company_id=1
  rates_found=2
  item_codes=['REMOTE', 'ONSITE']
```

### Check Webhook Monitor

In **Admin > Webhook Monitor**, you can see the individual API calls made to Xero to fetch item rates. Look for:
- Target URL: `https://api.xero.com/api.xro/2.0/Items`
- Request params: `where=Code=="REMOTE"`
- Response: Should include the item details and unit price

## Troubleshooting

### Rates Not Being Applied

**Problem**: Time is being billed at the default hourly rate instead of the Xero item rate.

**Solutions**:

1. **Verify Code Match**:
   - Check labour type code in MyPortal: `SELECT code FROM ticket_labour_types;`
   - Check item code in Xero: Navigate to the item and verify the code field
   - Codes must match exactly (case-sensitive)

2. **Verify Xero Item Exists**:
   - Go to Xero **Business > Products and Services**
   - Search for the item code
   - Ensure the item is not archived

3. **Verify Unit Price is Set**:
   - Open the item in Xero
   - Check that **Sales Details > Unit Price** has a value
   - Price must be greater than zero

4. **Check Logs**:
   - Look for "Xero item not found" messages
   - Look for "Invalid unit price for Xero item" warnings
   - These indicate the item exists but doesn't have a valid price

### Item Code Mismatch

**Problem**: Codes don't match between MyPortal and Xero.

**Solution**: Update one or the other to match exactly:

**Option A: Update MyPortal**
```sql
UPDATE ticket_labour_types 
SET code = 'REMOTE' 
WHERE code = 'remote';
```

**Option B: Update Xero**
- Edit the item in Xero
- Change the Item Code
- Save

### No Labour Type Selected

**Problem**: Time entries don't have a labour type assigned.

**Solution**: Time entries without a labour type will use the default hourly rate. To apply labour type rates, technicians must select a labour type when adding time.

### Multiple Items with Same Code

**Problem**: Multiple items in Xero have the same code (shouldn't be possible, but may occur in edge cases).

**Solution**: Xero should enforce unique item codes. If this occurs:
1. Archive or delete duplicate items
2. Keep only one item per code

## Best Practices

### Code Naming Convention

Use a consistent naming convention for codes:

✅ **Recommended**: Short, uppercase, alphanumeric codes
- REMOTE
- ONSITE
- AFTERHRS
- PROJECT

❌ **Avoid**: Long codes, special characters, spaces
- Remote-Support (hyphens can cause issues)
- On Site (spaces not recommended)
- remote_support (underscores work but uppercase is clearer)

### Organizing Items in Xero

Create a clear naming pattern for service items:

- **Item Code**: REMOTE (for API matching)
- **Item Name**: "IT Support - Remote" (for human readability)
- **Description**: "Remote technical support services"

Group similar items together:
- IT Support - Remote (REMOTE)
- IT Support - On-site (ONSITE)
- IT Support - After Hours (AFTERHRS)

### Rate Management

**Centralize in Xero**: Keep all rates in Xero as the single source of truth
- Update rates in Xero when they change
- Changes apply automatically to new invoices
- No need to update MyPortal configuration

**Default Rate**: Set a sensible default hourly rate as fallback
- Use your most common rate
- Or use a lower rate to catch misconfigured labour types

## Example Scenarios

### Scenario 1: Basic Setup

**Setup:**
- MyPortal: Remote (REMOTE), Onsite (ONSITE)
- Xero: REMOTE = $95, ONSITE = $150
- Default rate: $100

**Result:**
- Remote time: $95/hour (from Xero)
- Onsite time: $150/hour (from Xero)
- No labour type: $100/hour (default)

### Scenario 2: Missing Xero Item

**Setup:**
- MyPortal: Remote (REMOTE), Onsite (ONSITE), Project (PROJECT)
- Xero: REMOTE = $95, ONSITE = $150 (PROJECT item missing)
- Default rate: $100

**Result:**
- Remote time: $95/hour (from Xero)
- Onsite time: $150/hour (from Xero)
- Project time: $100/hour (fallback to default)

**Action**: Create PROJECT item in Xero with appropriate rate

### Scenario 3: Rate Change

**Setup:**
- Xero: REMOTE item has Unit Price = $95
- Client requests rate increase to $100

**Process:**
1. Update REMOTE item in Xero to $100
2. No changes needed in MyPortal
3. Next sync automatically uses $100/hour

**Result**: All future invoices use new rate automatically

## API Details

For developers or advanced troubleshooting, here's how the rate lookup works:

### Xero API Call

For each labour type code, MyPortal makes a GET request:

```
GET https://api.xero.com/api.xro/2.0/Items?where=Code=="REMOTE"
```

### Expected Response

```json
{
  "Items": [
    {
      "ItemID": "...",
      "Code": "REMOTE",
      "Name": "Remote Support",
      "SalesDetails": {
        "UnitPrice": 95.00,
        "AccountCode": "200"
      }
    }
  ]
}
```

### Rate Extraction

MyPortal extracts `SalesDetails.UnitPrice` and uses it as the hourly rate for that labour type.

## Support

If you continue to experience issues:

1. Check the Webhook Monitor for API errors
2. Review application logs for rate fetch messages
3. Verify Xero API credentials are valid
4. Ensure Xero module is enabled
5. Test with a simple setup (one labour type, one Xero item)
