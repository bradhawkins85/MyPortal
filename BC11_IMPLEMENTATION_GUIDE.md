# BCP Training & Review Schedules Implementation Summary

## Overview
This implementation adds comprehensive training and review schedule management to the Business Continuity Plan (BCP) module, including automated notifications for upcoming events.

## Features Implemented

### 1. Schedules Management Page (`/bcp/schedules`)

The new schedules page provides a centralized location to manage both training sessions and plan reviews:

#### Training Schedule Table
- **Columns**: Date, Type, Comments
- **Functionality**: 
  - View all scheduled training sessions
  - Sort by date (descending by default)
  - Add new training sessions via modal dialog
  - Edit existing entries
  - Delete entries with confirmation
  - Training types include: Tabletop Exercise, Full-scale Drill, etc.

#### Review Schedule Table
- **Columns**: Date, Reason for Review, Changes Made
- **Functionality**:
  - Track all plan reviews
  - Sort by date (descending by default)
  - Add new reviews via modal dialog
  - Edit existing entries
  - Delete entries with confirmation
  - Document reasons (e.g., Annual review, Organizational change) and changes made

### 2. Automated Notifications

Two new scheduler commands have been implemented:

#### `bcp_notify_upcoming_training`
- **Purpose**: Sends notifications about upcoming training sessions
- **Configuration**: 
  ```json
  {
    "days_ahead": 7  // Check for training in next N days (default: 7)
  }
  ```
- **Behavior**:
  - Queries for training sessions within the specified timeframe
  - Creates notification for each upcoming session
  - Notification includes: training date, type, plan title
  - Event type: `bcp_training_reminder`

#### `bcp_notify_upcoming_review`
- **Purpose**: Sends notifications about upcoming plan reviews
- **Configuration**:
  ```json
  {
    "days_ahead": 7  // Check for reviews in next N days (default: 7)
  }
  ```
- **Behavior**:
  - Queries for review sessions within the specified timeframe
  - Creates notification for each upcoming review
  - Notification includes: review date, reason, plan title
  - Event type: `bcp_review_reminder`

### 3. Setting Up Automated Notifications

Administrators can schedule the notification commands via the Scheduler UI:

1. Navigate to the Scheduler management page
2. Create a new scheduled task
3. Set the command to either:
   - `bcp_notify_upcoming_training`
   - `bcp_notify_upcoming_review`
4. Configure the cron schedule (e.g., `0 9 * * MON` for Monday 9 AM)
5. Optionally set description with JSON config:
   ```json
   {"days_ahead": 14}
   ```
6. Activate the task

## API Endpoints

### Training Schedule Endpoints
- `GET /bcp/schedules` - View schedules page
- `POST /bcp/training` - Create training item
- `POST /bcp/training/{id}/update` - Update training item
- `POST /bcp/training/{id}/delete` - Delete training item

### Review Schedule Endpoints
- `POST /bcp/review` - Create review item
- `POST /bcp/review/{id}/update` - Update review item
- `POST /bcp/review/{id}/delete` - Delete review item

## Repository Methods

New methods added to `app/repositories/bcp.py`:

### Training Operations
- `list_training_items(plan_id)` - Get all training items for a plan
- `get_training_item_by_id(training_id)` - Get a specific training item
- `create_training_item(plan_id, training_date, training_type, comments)` - Create new training
- `update_training_item(training_id, ...)` - Update training item
- `delete_training_item(training_id)` - Delete training item
- `get_upcoming_training_items(days_ahead)` - Get upcoming training (for notifications)

### Review Operations
- `list_review_items(plan_id)` - Get all review items for a plan
- `get_review_item_by_id(review_id)` - Get a specific review item
- `create_review_item(plan_id, review_date, reason, changes_made)` - Create new review
- `update_review_item(review_id, ...)` - Update review item
- `delete_review_item(review_id)` - Delete review item
- `get_upcoming_review_items(days_ahead)` - Get upcoming reviews (for notifications)

## Database Schema

The implementation uses existing tables from migration `126_bc02_bcp_data_model.sql`:

### `bcp_training_item`
```sql
CREATE TABLE bcp_training_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  training_date DATETIME NOT NULL,
  training_type VARCHAR(255),
  comments TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE
);
```

### `bcp_review_item`
```sql
CREATE TABLE bcp_review_item (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plan_id INT NOT NULL,
  review_date DATETIME NOT NULL,
  reason TEXT,
  changes_made TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (plan_id) REFERENCES bcp_plan(id) ON DELETE CASCADE
);
```

## Testing

Comprehensive test coverage with 10 passing tests in `tests/test_bc11_schedules.py`:

- `test_create_training_item` - Verify training creation
- `test_list_training_items` - Verify training listing
- `test_update_training_item` - Verify training updates
- `test_delete_training_item` - Verify training deletion
- `test_get_upcoming_training_items` - Verify upcoming training query
- `test_create_review_item` - Verify review creation
- `test_list_review_items` - Verify review listing
- `test_update_review_item` - Verify review updates
- `test_delete_review_item` - Verify review deletion
- `test_get_upcoming_review_items` - Verify upcoming review query

All tests use mocked database connections to ensure fast, isolated testing.

## Security

- **Authorization**: All schedule operations require `bcp:edit` permission
- **Input Validation**: Date formats are validated, text inputs are sanitized
- **SQL Injection**: Parameterized queries prevent SQL injection
- **XSS Prevention**: Template engine handles output escaping
- **CodeQL Scan**: 0 vulnerabilities detected

## Usage Example

### Scheduling a Training Session

1. Navigate to `/bcp/schedules`
2. Click "Add Training" button
3. Fill in the form:
   - Training Date: Select date and time
   - Type: e.g., "Tabletop Exercise"
   - Comments: "All personnel accounted within acceptable timeframe"
4. Click "Create"

### Scheduling a Plan Review

1. Navigate to `/bcp/schedules`
2. Click "Add Review" button
3. Fill in the form:
   - Review Date: Select date and time
   - Reason: e.g., "Annual review"
   - Changes Made: "Updated contact information and recovery procedures"
4. Click "Create"

### Setting Up Automatic Notifications

1. Go to Scheduler page (admin only)
2. Create new task:
   - Name: "BCP Training Reminders"
   - Command: `bcp_notify_upcoming_training`
   - Cron: `0 9 * * MON` (Every Monday at 9 AM)
   - Description: `{"days_ahead": 7}`
3. Activate the task
4. Repeat for review notifications with `bcp_notify_upcoming_review`

## Notes

- Training and review schedules are stored in UTC and displayed in local time
- Notifications are broadcast to all users (can be refined to specific distribution list members)
- The notification metadata includes links to the plan for easy navigation
- Empty states guide users to create their first entries
- Responsive design works on mobile, tablet, and desktop
