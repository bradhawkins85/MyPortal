# BCP Business Impact Analysis (BIA) - Implementation Summary

## Overview
This implementation adds comprehensive Business Impact Analysis functionality to the BCP module, allowing organizations to identify and assess critical business activities and their recovery requirements.

## Key Features Implemented

### 1. Critical Activity Management
- **Create**: Add new critical activities with comprehensive details
- **Read**: View all activities in sortable list
- **Update**: Edit existing activities and their impact assessments
- **Delete**: Remove activities (cascade deletes associated impact data)

### 2. Data Captured for Each Critical Activity

#### Basic Information
- **Name**: Short identifier for the activity (e.g., "Email Services")
- **Description**: Detailed explanation of the activity
- **Priority**: High, Medium, or Low classification
- **Importance Rating**: 1-5 scale (1 = most critical, 5 = least critical)
- **Supplier Dependency**: None, Sole, Major, or Many alternatives
- **Notes**: Additional context or requirements

#### Impact Assessment
- **RTO (Recovery Time Objective)**: Maximum acceptable downtime in hours
  - Stored as integer hours in database
  - Displayed in human-readable format (e.g., "2 days", "1 week")
- **Financial Impact**: Revenue loss description
- **Increased Costs**: Additional expenses during disruption
- **Staffing Impact**: Effect on workforce productivity
- **Product/Service Impact**: Ability to deliver to customers
- **Reputation Impact**: Effect on brand and customer trust
- **Fines & Penalties**: Regulatory or contractual penalties
- **Legal Liability**: Potential legal consequences
- **Additional Comments**: Other relevant impact information

### 3. User Interface

#### BIA List Page (`/bcp/bia`)
- **Sortable table** with the following views:
  - Sort by Importance (1=most important)
  - Sort by Priority (High to Low)
  - Sort by Activity Name (alphabetical)
- **Columns displayed**:
  - Activity Name
  - Description
  - Priority (with color-coded badges)
  - Importance (with numbered badges 1-5)
  - Impact Summary (abbreviated key impacts)
  - RTO (humanized format)
  - Actions (Edit/Delete buttons)
- **Actions available**:
  - "Add Critical Activity" button
  - "Export CSV" button
  - Edit/Delete per row

#### BIA Edit/New Page (`/bcp/bia/new` and `/bcp/bia/{id}/edit`)
- **Two-section form**:
  1. Basic Information section
  2. Impact Assessment section
- **Form fields**:
  - All fields mentioned in data capture above
  - Input validation (importance 1-5, RTO >= 0)
  - Text areas for longer descriptions
  - Dropdowns for enum fields (priority, supplier dependency)
- **Actions**:
  - Cancel (returns to list)
  - Save (creates/updates and returns to list)

#### CSV Export (`/bcp/bia/export`)
- **Exports all critical activities** with columns:
  - Activity, Description, Priority, Importance, RTO
  - Supplier Dependency
  - All impact fields (financial, costs, staffing, etc.)
- **Filename format**: `bia_summary_YYYYMMDD_HHMMSS.csv`

### 4. RTO Humanization

The `humanize_hours()` function converts integer hours to human-readable formats:

| Hours | Output |
|-------|--------|
| 0 | Immediate |
| 1 | 1 hour |
| 2 | 2 hours |
| 24 | 1 day |
| 48 | 2 days |
| 25 | 1 day, 1 hour |
| 168 | 1 week |
| 336 | 2 weeks |
| 192 | 1 week, 1 day |
| 730 | 1 month |
| 898 | 1 month, 1 week |

### 5. Database Schema

#### New Fields Added (migration 127)
```sql
-- bcp_critical_activity table
ALTER TABLE bcp_critical_activity 
ADD COLUMN importance INT;  -- 1-5 rating

-- bcp_impact table
ALTER TABLE bcp_impact 
ADD COLUMN losses_increased_costs TEXT;
ADD COLUMN losses_product_service TEXT;
ADD COLUMN losses_comments TEXT;
```

### 6. API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/bcp/bia` | List all critical activities |
| GET | `/bcp/bia/new` | Show new activity form |
| GET | `/bcp/bia/{id}/edit` | Show edit activity form |
| POST | `/bcp/bia` | Create new activity |
| POST | `/bcp/bia/{id}/update` | Update existing activity |
| POST | `/bcp/bia/{id}/delete` | Delete activity |
| GET | `/bcp/bia/export` | Export BIA to CSV |

### 7. Test Coverage

#### Time Utils Tests (22 tests)
- Humanization of various hour ranges
- Edge cases (0, 1, boundaries between units)
- Parsing humanized strings back to hours
- Real-world RTO examples

#### Repository Tests (9 tests)
- List activities (empty and with data)
- Create activity
- Update activity
- Delete activity
- Create impact data
- Update impact data
- Sort by priority, importance, name

All tests passing ✅

## Usage Example

### Creating a Critical Activity

1. Navigate to `/bcp/bia`
2. Click "Add Critical Activity"
3. Fill in:
   - **Name**: "Email Services"
   - **Description**: "Corporate email system (Exchange Online)"
   - **Priority**: High
   - **Importance**: 1
   - **Supplier Dependency**: Sole (Microsoft)
   - **RTO**: 4 hours
   - **Financial Impact**: "Unable to communicate with customers, $5000/hour in lost sales"
   - **Staffing Impact**: "Staff unable to collaborate effectively"
   - **Reputation Impact**: "Customers may perceive unreliability"
4. Click "Create Activity"

### Viewing the BIA Summary

The activity appears in the table showing:
- Activity: **Email Services**
- Priority: 🔴 **High**
- Importance: **1** (most critical)
- Impact: "Financial: Unable to communicate with customers...; Staffing: Staff unable to..."
- RTO: **4 hours**

### Exporting to CSV

Click "Export CSV" to download a comprehensive spreadsheet with all activities and their impact assessments for reporting and stakeholder communication.

## Acceptance Criteria ✅

- [x] RTO is persisted as hours and rendered human-readable
- [x] Activities sortable by priority/importance/name
- [x] CSV export matches visible columns
- [x] Importance rating 1-5 with validation
- [x] All impact fields captured
- [x] Supplier dependency tracked
- [x] Full CRUD functionality
- [x] Comprehensive test coverage

## Files Modified/Created

1. **Database**: `migrations/127_bc05_bia_enhancements.sql`
2. **Backend**:
   - `app/services/time_utils.py` (new)
   - `app/repositories/bcp.py` (extended)
   - `app/api/routes/bcp.py` (extended)
3. **Frontend**:
   - `app/templates/bcp/bia.html` (new)
   - `app/templates/bcp/bia_edit.html` (new)
4. **Tests**:
   - `tests/test_time_utils.py` (new)
   - `tests/test_bcp_bia_repository.py` (new)

## Notes

- The implementation follows existing BCP patterns (insurance, backups, risks)
- Uses the same permission model (bcp:view, bcp:edit)
- Integrates seamlessly with the existing BCP overview navigation
- Ready for production deployment after migration runs
