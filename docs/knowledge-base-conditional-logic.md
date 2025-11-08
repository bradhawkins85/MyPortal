# Knowledge Base Conditional Logic

## Overview

The knowledge base system supports conditional logic that allows articles to display different content based on the company viewing the article. This feature enables you to maintain a single article while customizing specific sections for different companies.

## Use Cases

- **Company-Specific Instructions**: Display different setup or configuration steps for each company
- **Branded Content**: Show company-specific logos, images, or branding
- **Custom Procedures**: Provide tailored workflows for different organizations
- **Localized Information**: Display region or company-specific contact details

## Syntax

Use the `<kb-if>` tag to create conditional blocks:

```html
<kb-if company="Company Name">
  Content specific to Company Name
</kb-if>
```

### Examples

#### Simple Text Conditional

```html
<p>This content is visible to everyone.</p>

<kb-if company="ACME Corp">
  <p>This content is only visible to ACME Corp users.</p>
</kb-if>

<p>This content is visible to everyone again.</p>
```

#### Multiple Companies

You can have multiple conditional blocks in the same section:

```html
<kb-if company="ACME Corp">
  <h3>ACME Setup Instructions</h3>
  <p>Follow these steps for ACME...</p>
</kb-if>

<kb-if company="XYZ Inc">
  <h3>XYZ Setup Instructions</h3>
  <p>Follow these steps for XYZ...</p>
</kb-if>
```

#### Images and Rich Content

Conditional blocks can contain any HTML content, including images:

```html
<kb-if company="ACME Corp">
  <h3>Your Company Logo</h3>
  <img src="/uploads/acme-logo.png" alt="ACME Corp Logo" />
  <p>For support, contact support@acme.com</p>
</kb-if>
```

## How It Works

### Server-Side Processing

- Conditional blocks are processed on the server before sending content to the browser
- Only content matching the user's company is included in the response
- Non-matching content is completely removed, not just hidden with CSS
- This ensures secure content filtering - users cannot view source code to see hidden content

### Company Matching

- The system uses the user's **primary company** (their first company membership)
- Company name matching is **case-insensitive**
- Users with no company memberships see no conditional content

### Admin View

- Super admins in edit mode see all conditional blocks unprocessed
- This allows easy editing of all company-specific content
- The editor shows visual indicators for conditional blocks

## Using the Editor

### Adding Conditional Content

1. Open the knowledge base article editor
2. Create or edit a section
3. Click the **"If Company..."** button in the formatting toolbar
4. Enter the company name when prompted
5. Type or paste the content that should be visible for that company
6. The conditional block will be highlighted in the editor with a blue border

### Visual Indicators

In the editor, conditional blocks appear with:
- A dashed blue border
- A label showing "If company: [Company Name]"
- A light blue background

### Editing Conditional Content

You can edit the content inside conditional blocks like any other content. To change the company name, you'll need to:
1. Select the conditional block's HTML
2. Edit the `company` attribute directly
3. Or delete and recreate the block with the correct company name

## Validation

The system validates conditional syntax when you save an article:

### Valid Syntax
- All `<kb-if>` tags must have matching closing `</kb-if>` tags
- The `company` attribute must not be empty
- Nested conditional blocks are not supported

### Invalid Examples

```html
<!-- Missing closing tag -->
<kb-if company="ACME">Content

<!-- Empty company attribute -->
<kb-if company="">Content</kb-if>

<!-- Nested conditionals (not supported) -->
<kb-if company="ACME">
  <kb-if company="XYZ">Content</kb-if>
</kb-if>
```

If validation fails, you'll see an error message indicating what needs to be fixed.

## Best Practices

### Content Organization

1. **Keep common content outside conditionals**: Only use conditional blocks for content that truly differs between companies
2. **Use descriptive headings**: Help users understand what the conditional content provides
3. **Test with multiple companies**: Preview your article as different company users to ensure it works as expected

### Company Names

1. **Use exact company names**: The company name in the conditional must match exactly (case-insensitive) the name in the system
2. **Be consistent**: Use the same company name format throughout your articles
3. **Document company names**: Keep a list of company names used in conditionals for reference

### Performance

1. **Limit conditional blocks**: While there's no hard limit, excessive conditional blocks can make articles harder to maintain
2. **Consider article splitting**: If an article has many company-specific sections, consider creating separate articles per company instead

## Technical Details

### HTML Sanitization

- Conditional tags (`<kb-if>`) are allowed through the HTML sanitizer
- Only the `company` attribute is permitted on conditional tags
- Standard HTML security measures apply to content within conditional blocks

### Database Storage

- Conditional blocks are stored in the article content as-is
- No special database schema changes are required
- The conditional processing happens during article retrieval, not storage

### API Behavior

- Public API endpoints process conditionals based on the requesting user's company
- Admin API endpoints with `include_permissions=True` return unprocessed content
- Search and listing endpoints do not process conditionals (for performance)

## Troubleshooting

### Content Not Showing

**Problem**: Conditional content isn't displaying for a company
- Check that the company name in the conditional exactly matches the company name in the system
- Verify the user is a member of the expected company
- Check that the conditional block is properly closed

### Content Showing for Wrong Company

**Problem**: Content appears for users from a different company
- Verify there are no typos in the company name
- Check that the conditional block is properly structured
- Review the company name in the user's membership data

### Editor Issues

**Problem**: Conditional blocks look wrong in the editor
- Try refreshing the page
- Check browser console for JavaScript errors
- Verify you're using a supported browser

## Future Enhancements

Potential future improvements to the conditional system:

- Support for multiple conditions (OR logic)
- User-based conditionals in addition to company-based
- Permission scope conditionals
- Date/time-based conditionals
- Visual conditional block editor with preview

## Examples Library

### Example 1: Software Setup by Company

```html
<h2>Installation Instructions</h2>

<p>Download the software from your company portal.</p>

<kb-if company="ACME Corp">
  <h3>ACME Corp Portal</h3>
  <p>Visit <a href="https://portal.acme.com">https://portal.acme.com</a></p>
  <p>Login credentials: Use your ACME email and password</p>
  <img src="/uploads/acme-portal-screenshot.png" alt="ACME Portal" />
</kb-if>

<kb-if company="Globex Corporation">
  <h3>Globex Portal</h3>
  <p>Visit <a href="https://downloads.globex.com">https://downloads.globex.com</a></p>
  <p>Login credentials: Use your SSO credentials</p>
</kb-if>

<h3>After Download</h3>
<p>Run the installer and follow the on-screen instructions.</p>
```

### Example 2: Support Contact Information

```html
<h2>Getting Support</h2>

<p>If you need help, contact your company's support team:</p>

<kb-if company="ACME Corp">
  <ul>
    <li>Email: support@acme.com</li>
    <li>Phone: 555-0100</li>
    <li>Hours: 9 AM - 5 PM EST</li>
  </ul>
</kb-if>

<kb-if company="XYZ Industries">
  <ul>
    <li>Email: help@xyzind.com</li>
    <li>Phone: 555-0200</li>
    <li>Hours: 24/7</li>
  </ul>
</kb-if>

<p>Please have your employee ID ready when contacting support.</p>
```

### Example 3: Feature Availability

```html
<h2>Available Features</h2>

<p>Your account includes the following features:</p>

<ul>
  <li>Basic reporting</li>
  <li>Email notifications</li>
  
  <kb-if company="Enterprise Client A">
    <li>Advanced analytics</li>
    <li>Custom integrations</li>
    <li>Priority support</li>
  </kb-if>
  
  <kb-if company="Enterprise Client B">
    <li>Advanced analytics</li>
    <li>API access</li>
    <li>Dedicated account manager</li>
  </kb-if>
</ul>

<p>To learn more about upgrading your plan, contact your account manager.</p>
```

## Support

For questions or issues with conditional logic:
1. Review this documentation
2. Check the troubleshooting section
3. Contact your administrator
4. Submit a support ticket if the issue persists
