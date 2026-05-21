# Ticket Booking Calls with Cal.com

MyPortal integrates with Cal.com to allow customers to book calls directly with assigned technicians from the ticket detail page.

## Overview

When a ticket has an assigned technician who has configured their booking link, a "Book a Call" button appears on the ticket detail page. Clicking this button opens a Cal.com embed that allows the user to schedule a meeting with the technician.

## Configuration

### Setting Up Booking Links for Technicians

1. Navigate to **My Profile** (accessible from the user menu)
2. Scroll to the **Booking link** section
3. Enter your Cal.com booking URL in the **Booking link URL** field
   - Example: `https://cal.com/your-username`
   - Or use your custom Cal.com domain
4. Click **Save booking link**

### Supported Booking Platforms

While the field is labeled "Booking link URL" and accepts any URL format, the ticket detail page specifically implements Cal.com embed functionality. For full integration:

- Use a Cal.com booking link (e.g., `https://cal.com/your-username` or `https://cal.com/your-username/30min`)
- Other calendar booking platforms can be entered but will not provide the embedded booking experience

## User Experience

### For Customers

When viewing a ticket with an assigned technician who has a booking link configured:

1. A "Book a Call" card appears in the left sidebar of the ticket detail page
2. The card displays a message: "Schedule a call with [technician-email] to discuss this ticket"
3. Clicking the "Book a Call" button opens a Cal.com booking widget inline
4. The customer can select an available time slot from the technician's calendar
5. After booking, the customer receives confirmation via email from Cal.com

### For Technicians

To enable booking for your tickets:

1. Set up your Cal.com account and create event types
2. Configure your booking link in **My Profile**
3. When assigned to tickets, customers will see the booking option automatically

## Technical Details

### Template Implementation

The booking button is rendered conditionally in the ticket detail template (`app/templates/admin/ticket_detail.html`) based on:

- Ticket has an assigned user (`ticket.assigned_user_id` is set)
- The assigned user has a booking link configured (`ticket_assigned_user.booking_link_url` is not empty)

### Cal.com Integration

The implementation uses Cal.com's official embed code:

- The embed script is loaded only when a booking link is present
- The button uses the `data-cal-link` attribute pointing to the technician's booking URL
- Cal.com's embed library handles the booking flow and calendar display

### Data Flow

1. User configures booking link in profile → Saved to `users.booking_link_url` in database
2. Ticket is assigned to user → `ticket.assigned_user_id` references the user
3. Ticket detail page loads → Context includes `ticket_assigned_user` with booking link
4. Template renders → Booking card appears if link is configured
5. Customer clicks button → Cal.com embed opens with technician's availability

## Best Practices

### For Administrators

- Encourage technicians to set up Cal.com accounts and configure booking links
- Consider creating team event types in Cal.com for round-robin booking
- Monitor booking usage to ensure technicians are responsive to scheduled calls

### For Technicians

- Keep your Cal.com availability up to date
- Use descriptive event type names (e.g., "Ticket Support Call - 30 min")
- Set appropriate buffer times between calls
- Configure email notifications for new bookings
- Add the ticket number in booking confirmation emails for easy reference

## Troubleshooting

### Booking Button Not Appearing

**Problem**: The "Book a Call" card doesn't show on ticket detail page

**Solutions**:
1. Verify the ticket has an assigned user
2. Check that the assigned user has a booking link configured in their profile
3. Ensure the booking link URL field is not empty

### Booking Widget Not Loading

**Problem**: Clicking the button doesn't open the Cal.com widget

**Solutions**:
1. Verify the booking link URL is a valid Cal.com URL
2. Check browser console for JavaScript errors
3. Ensure Cal.com's embed script is not blocked by ad blockers or content security policies
4. Test the booking link directly by visiting it in a new tab

### Wrong Calendar Displayed

**Problem**: The booking widget shows the wrong technician's calendar

**Solutions**:
1. Verify the assigned user's profile has the correct booking link
2. Check that the ticket assignment is up to date
3. Refresh the page to reload the ticket data

## Future Enhancements

Potential improvements to the booking integration:

- Support for other calendar platforms (Calendly, Microsoft Bookings, etc.)
- Auto-populate ticket number in booking notes
- Display upcoming booked calls in ticket timeline
- Send booking confirmations to ticket watchers
- Integrate booking data into ticket automation workflows
