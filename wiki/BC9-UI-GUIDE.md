# BC9 UI Implementation - Visual Guide

## Overview
This document describes the 3-part layout implementation for the Business Continuity Plans UI.

## Layout Structure

The BC UI follows a consistent 3-part layout:
- **Left Sidebar**: Navigation menu (256px wide, ~16rem)
- **Right Header**: Page title, status, and action buttons
- **Right Body**: Main content area with scrollable content

## Color Scheme
- Primary Blue: #1d4ed8
- Light Blue Background: #eff6ff
- Gray Background: #f9fafb
- Border Gray: #e5e7eb
- Text Gray: #6b7280
- Success Green: #065f46
- Warning Yellow: #92400e
- Neutral Gray: #6b7280

## Pages Overview

### 1. Plans List (`/business-continuity/plans`)

```
┌────────────┬───────────────────────────────────────────────────┐
│            │ Plans                             [+ New Plan]     │
│ Business   ├───────────────────────────────────────────────────┤
│ Continuity │                                                    │
│            │ Business Continuity Plans                          │
│ • Plans ●  │ Manage disaster recovery, incident response, and  │
│ • Templates│ business continuity plans.                         │
│ • Reviews  │                                                    │
│ • Reports  │ [Search...] [Status ▼] [Owner ▼]                  │
│            │                                                    │
│            │ ┌────────────────────────────────────────────────┐│
│            │ │ Title ↕ │ Type ↕ │ Ver │ Status │ Owner │ ... ││
│            │ ├────────────────────────────────────────────────┤│
│            │ │ Primary Data Center DR │ [DR] │ v1.0 │...     ││
│            │ │ Incident Response Plan │ [IR] │ v2.1 │...     ││
│            │ │ Business Continuity... │ [BC] │ v1.5 │...     ││
│            │ └────────────────────────────────────────────────┘│
│            │                                                    │
│            │              [ ← Previous | Page 1 | Next → ]     │
└────────────┴───────────────────────────────────────────────────┘
```

**Features:**
- Search bar for filtering
- Status and Owner dropdowns for filtering
- Sortable columns (click header to toggle asc/desc)
- Status pills with color coding
- Action buttons (View, Edit) per row
- Pagination controls at bottom
- Empty state when no plans exist

### 2. Plan Detail (`/business-continuity/plans/{id}`)

```
┌────────────┬───────────────────────────────────────────────────┐
│            │ Primary Data Center DR Plan  [Active ✓]           │
│ Business   │ [Edit] [Approve] [Export ▼] [View Audit] [Ack]   │
│ Continuity ├───────────────────────────────────────────────────┤
│            │                                                    │
│ • Plans ●  │ [Content] [Versions] [Reviews] [Ack] [Attachments]│
│ • Templates│ ────────────────────────────────────────────────  │
│ • Reviews  │                                                    │
│ • Reports  │ ┌─ Plan Details ──────────────────────────────┐  │
│            │ │                                              │  │
│            │ │ Plan Type: Disaster Recovery                 │  │
│            │ │ Owner: admin@example.com                     │  │
│            │ │ Last Updated: 2024-01-15                     │  │
│            │ │ Template: Government BCP Template            │  │
│            │ │                                              │  │
│            │ │ ─── Plan Content ───                         │  │
│            │ │                                              │  │
│            │ │ [Rich text content displays here...]         │  │
│            │ │                                              │  │
│            │ └──────────────────────────────────────────────┘  │
└────────────┴───────────────────────────────────────────────────┘
```

**Features:**
- Tabbed interface (Content, Versions, Reviews, Acknowledgments, Attachments)
- Context-sensitive actions based on plan status
- Status pill in header
- Export dropdown (DOCX/PDF)
- Rich text content display
- Metadata display (owner, dates, template)

### 3. Plan Editor (`/business-continuity/plans/{id}/edit`)

```
┌────────────┬───────────────────────────────────────────────────┐
│            │ Edit Plan                    [Save Draft] [Update]│
│ Business   ├───────────────────────────────────────────────────┤
│ Continuity │                                                    │
│            │ ┌─ Plan Information ─────────────────────────────┐│
│ • Plans ●  │ │ Title: [Primary Data Center DR Plan........]  ││
│ • Templates│ │ Type: [Disaster Recovery ▼] Owner: [Admin ▼]  ││
│ • Reviews  │ │ Template: [Govt BCP ▼] Status: [Active ▼]     ││
│ • Reports  │ └────────────────────────────────────────────────┘│
│            │                                                    │
│            │ ┌─ Plan Content ─────────────── (Autosaving...) ┐│
│            │ │ [Overview] [Roles] [Procedures] [Recovery]...  ││
│            │ │ ──────────────────────────────────────────────  ││
│            │ │                                                 ││
│            │ │ Purpose *                                       ││
│            │ │ [Rich text editor with formatting...]          ││
│            │ │                                                 ││
│            │ │ Scope *                                         ││
│            │ │ [Rich text editor...]                          ││
│            │ │                                                 ││
│            │ │ Roles & Responsibilities                        ││
│            │ │ ┌────────┬──────────────┬─────────┬──┐        ││
│            │ │ │ Role   │ Responsibility│ Contact │+│        ││
│            │ │ ├────────┼──────────────┼─────────┼──┤        ││
│            │ │ │[Input] │ [Input]      │[Input]  │×│        ││
│            │ │ └────────┴──────────────┴─────────┴──┘        ││
│            │ │                                                 ││
│            │ └─────────────────────────────────────────────────┘│
└────────────┴───────────────────────────────────────────────────┘
```

**Features:**
- Tabbed sections (Overview, Roles, Procedures, etc.)
- Rich text editor with contenteditable
- Inline table editor with add/remove rows
- File upload fields
- Autosave functionality (2-second debounce)
- Save Draft vs Update/Create buttons
- Form validation

### 4. Version History (Tab in Plan Detail)

```
┌────────────┬───────────────────────────────────────────────────┐
│            │ Primary Data Center DR Plan  [Active ✓]           │
│ Business   │                                                    │
│ Continuity ├───────────────────────────────────────────────────┤
│            │                                                    │
│ • Plans ●  │ [Content] [Versions ●] [Reviews] [Ack] [Attach]  │
│ • Templates│ ────────────────────────────────────────────────  │
│ • Reviews  │                                                    │
│ • Reports  │ ┌─ Version History ──────────────────────────────┐│
│            │ │                                                 ││
│            │ │ ●── Version 2.1 ────────────[Current]          ││
│            │ │ │   By admin • 2024-01-15                      ││
│            │ │ │   "Updated recovery procedures"              ││
│            │ │ │                                               ││
│            │ │ ●── Version 2.0 ────────[View] [Compare]       ││
│            │ │ │   By john.doe • 2024-01-10                   ││
│            │ │ │   "Major revision with new BIA"              ││
│            │ │ │                                               ││
│            │ │ ●── Version 1.5 ────────[View] [Compare]       ││
│            │ │     By admin • 2023-12-20                       ││
│            │ │     "Initial version"                           ││
│            │ │                                                 ││
│            │ └─────────────────────────────────────────────────┘│
└────────────┴───────────────────────────────────────────────────┘
```

**Features:**
- Timeline view with visual markers
- Current version highlighted
- Author and date for each version
- Change summary notes
- View and Compare buttons for historical versions
- Visual connection lines between versions

### 5. Reviews Page (`/business-continuity/reviews`)

```
┌────────────┬───────────────────────────────────────────────────┐
│            │ Reviews                              [Status ▼]   │
│ Business   ├───────────────────────────────────────────────────┤
│ Continuity │                                                    │
│            │ Plan Reviews                                       │
│ • Plans    │ Review and approve business continuity plans.     │
│ • Templates│                                                    │
│ • Reviews ●│ ┌────────────────────────────────────────────────┐│
│ • Reports  │ │ Plan │ Submitted By │ Date │ Status │ Actions ││
│            │ ├────────────────────────────────────────────────┤│
│            │ │ Primary DC DR │ admin │ Jan 15│[Pending]│...  ││
│            │ │ IR Plan v2.0  │ john  │ Jan 10│[Approved]│... ││
│            │ │ BC Plan Update│ sarah │ Jan 05│[Changes]│...  ││
│            │ └────────────────────────────────────────────────┘│
│            │                                                    │
│            │ [View] [Approve] [Request Changes]                │
└────────────┴───────────────────────────────────────────────────┘
```

**Features:**
- Filter by status (Pending, Approved, Changes Requested)
- Status badges with color coding
- Reviewer name display
- Action buttons: View, Approve, Request Changes
- Empty state when no reviews pending

### 6. Reports Page (`/business-continuity/reports`)

```
┌────────────┬───────────────────────────────────────────────────┐
│            │ Reports                                            │
│ Business   ├───────────────────────────────────────────────────┤
│ Continuity │                                                    │
│            │ Business Continuity Reports                        │
│ • Plans    │ Generate and view reports on plan status, reviews │
│ • Templates│                                                    │
│ • Reviews  │ ┌───────────┬───────────┬───────────┬──────────┐ │
│ • Reports ●│ │ Total     │ Active    │ In Review │ Draft    │ │
│            │ │ Plans     │ Plans     │           │ Plans    │ │
│            │ │   15      │    12     │     2     │    1     │ │
│            │ └───────────┴───────────┴───────────┴──────────┘ │
│            │                                                    │
│            │ ─── Generate Report ───                           │
│            │ Type: [Plan Summary ▼]  From: [Date] To: [Date]  │
│            │ Format: [PDF ▼]          [🔽 Generate Report]     │
│            │                                                    │
│            │ ─── Recent Reports ───                            │
│            │ ┌────────────────────────────────────────────────┐│
│            │ │ Type │ Generated │ By │ Format │ [Download]   ││
│            │ └────────────────────────────────────────────────┘│
└────────────┴───────────────────────────────────────────────────┘
```

**Features:**
- Summary statistics cards
- Report generation form
- Recent reports list with download links
- Export formats (PDF, Excel, CSV)
- Date range selection

## Interactive Elements

### Expandable Menu
- Click "Business Continuity" to expand/collapse submenu
- Active page highlighted in blue
- Hover effects on menu items
- Smooth transitions

### Sortable Tables
- Click column header to sort ascending
- Click again to sort descending
- Arrow indicators (↑↓) show sort direction
- All data columns sortable

### Autosave
- Triggers 2 seconds after last keystroke
- Shows "Typing..." then "Saving..." then "Saved"
- Indicator appears in editor header
- Works on all form fields with data-autosave attribute

### Tabs
- Click to switch between views
- Blue underline indicates active tab
- Content panels show/hide based on selection
- Keyboard accessible (ARIA roles)

### Dropdowns
- Click to open/close
- Click outside to close
- Options list styled consistently
- Used for filters and exports

## Responsive Design

### Desktop (>1024px)
- Full 3-part layout
- 256px left sidebar
- Flexible main content area
- Tables show all columns

### Tablet (768px - 1024px)
- Sidebar collapses on mobile toggle
- Main content uses full width when sidebar hidden
- Tables remain scrollable

### Mobile (<768px)
- Sidebar becomes overlay
- Header actions stack vertically
- Tables collapse to card layout
- Form fields stack vertically

## Status Pills

- **Draft**: Yellow background (#fef3c7), brown text (#92400e)
- **In Review**: Blue background (#dbeafe), blue text (#1e40af)
- **Active**: Green background (#d1fae5), green text (#065f46)
- **Archived**: Gray background (#f3f4f6), gray text (#6b7280)

## Action Buttons

- **Primary**: Blue background, white text (Create, Update, Approve)
- **Secondary**: White background, gray border, gray text (View, Cancel)
- **Success**: Green background, white text (Approve)
- **Warning**: Yellow background, brown text (Request Changes)
- **Danger**: Red background, white text (Delete, Remove)
- **Small**: Reduced padding for inline actions

## Table Features

- **Consistent Row Heights**: 3.5rem (56px) per row
- **Zebra Striping**: Alternating row colors for readability
- **Hover Effects**: Light background on row hover
- **Responsive**: Converts to card layout on mobile
- **Borders**: Subtle gray borders (#e5e7eb)
- **Cell Padding**: 0.75rem vertical, 1rem horizontal

## Forms

- **Inline Validation**: Error messages below fields
- **Required Fields**: Asterisk (*) indicator
- **Help Text**: Gray small text below fields
- **Focus States**: Blue border and shadow on focus
- **Consistent Spacing**: 1rem gap between form groups

## Accessibility

- **ARIA Labels**: All interactive elements labeled
- **Keyboard Navigation**: Tab, Enter, Escape support
- **Screen Reader Support**: Semantic HTML, ARIA roles
- **Color Contrast**: WCAG AA compliant
- **Focus Indicators**: Visible focus states

## Browser Support

- Chrome/Edge 90+
- Firefox 88+
- Safari 14+
- Mobile browsers (iOS Safari, Chrome Mobile)

## Performance

- **Lazy Loading**: Tables paginate after 20 items
- **Debounced Search**: 300ms delay on search input
- **Autosave Throttling**: 2-second debounce
- **CSS Transitions**: Hardware accelerated
- **Minimal JavaScript**: ~50KB uncompressed

## Future Enhancements

1. Version diff comparison view with side-by-side display
2. Real-time collaboration indicators
3. Notification badges for pending reviews
4. Drag-and-drop file upload
5. Rich text editor toolbar (bold, italic, lists)
6. Advanced search with filters
7. Bulk operations (archive multiple plans)
8. Export templates customization
9. Role-based UI hiding (show/hide actions based on permissions)
10. Dark mode support
