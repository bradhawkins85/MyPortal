# Knowledge Base Conditional Logic and Section Permissions

## Overview

The knowledge base system provides two complementary ways to control content visibility based on company membership:

1. **Section-Level Company Permissions** (Recommended): Restrict entire sections to specific companies through an easy-to-use interface
2. **Legacy Conditional Logic**: Use `<kb-if>` tags to embed company-specific content within sections (backward compatible)

## Section-Level Company Permissions

### What Are Section Permissions?

Section permissions allow you to control which companies can view specific sections of an article. This is the **recommended approach** for company-specific content as it provides:

- **Easy management**: Click a button to select companies via a modal dialog
- **Clear visibility**: See which companies can access each section at a glance
- **Secure filtering**: Sections are filtered server-side before sending to users
- **Flexible control**: Mix public and restricted sections within the same article

### How to Use Section Permissions

#### Setting Up Section Permissions

1. Open the knowledge base article editor
2. Create or edit a section
3. Click the **"Company Access"** button (🏢 icon) above the section content
4. A modal dialog appears showing all active companies
5. Check the companies that should have access to this section
6. Click **Save** to apply the permissions
7. The selected companies are displayed below the section heading

#### Understanding Visibility Rules

- **No companies selected**: Section is visible to everyone (no restrictions)
- **Companies selected**: Only users from those companies can view the section
- **Super admins**: Always see all sections regardless of restrictions
- **Anonymous users**: Only see sections with no company restrictions

#### Permission Inheritance

Section permissions work **in addition to** article-level permissions:

1. First, users must have permission to view the article itself (based on article permission scope)
2. Then, section permissions further restrict which sections they can see within the article

**Example:**
- Article permission: "Company members" (Companies A, B, C can access)
- Section 1: No restrictions (visible to all who can access the article)
- Section 2: Companies A and B only
- Section 3: Company C only

Result:
- Company A users see Sections 1 and 2
- Company B users see Sections 1 and 2
- Company C users see Sections 1 and 3

### Visual Indicators

In the editor, section company permissions are shown:
- A light blue background bar below the section heading
- Company names displayed as badges (e.g., "ACME Corp", "XYZ Inc")
- If no restrictions: Shows "All companies (no restrictions)" in gray italics

### Best Practices for Section Permissions

1. **Use descriptive section headings**: Help users understand what content they're viewing
2. **Group related content**: Put company-specific content in dedicated sections
3. **Test with different companies**: Preview as different users to verify visibility
4. **Document your approach**: Keep notes on which sections are restricted and why
5. **Prefer section permissions over conditional tags**: Easier to manage and maintain

## Legacy Conditional Logic (Backward Compatible)

The `<kb-if>` tag system continues to work for backward compatibility, but **section-level permissions are now the recommended approach** for new content.

## Use Cases

### Section-Level Permissions Use Cases

- **Company-Specific Procedures**: Entire workflows that differ by company
- **Restricted Information**: Sensitive content for specific companies only
- **Feature Documentation**: Document features available only to certain companies
- **Custom Configurations**: Company-specific setup instructions as separate sections

### Conditional Logic Use Cases (Legacy)

- **Inline Variations**: Small text differences within a paragraph
- **Dynamic Content**: Company names or logos embedded in flowing text
- **Backward Compatibility**: Existing articles using `<kb-if>` tags

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

## Migration Guide

### Moving from Conditional Tags to Section Permissions

If you have articles using `<kb-if>` tags, consider migrating to section-level permissions:

1. **Identify conditional blocks**: Review your articles for `<kb-if>` tags
2. **Reorganize content**: Move each company-specific block into its own section
3. **Set section permissions**: Use the Company Access button to assign companies
4. **Test thoroughly**: Verify content appears correctly for each company
5. **Remove old tags**: Once verified, remove the `<kb-if>` tags

**Note**: Both systems can coexist in the same article during migration.

## Future Enhancements

Potential future improvements:

- User-based section permissions in addition to company-based
- Role-based section permissions
- Date/time-based section visibility
- Section permission templates for quick setup
- Bulk permission management across multiple sections

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

### Example 4: Using Section Permissions (Recommended)

Instead of using conditional tags, organize content into sections with permissions:

**Section 1: "Overview"** (No restrictions)
```html
<h2>Installation Instructions</h2>
<p>This guide will help you install and configure the software.</p>
```

**Section 2: "ACME Corp Setup"** (Restricted to ACME Corp)
```html
<h3>ACME Corp Portal</h3>
<p>Visit <a href="https://portal.acme.com">https://portal.acme.com</a></p>
<p>Login credentials: Use your ACME email and password</p>
<img src="/uploads/acme-portal-screenshot.png" alt="ACME Portal" />
```

**Section 3: "Globex Setup"** (Restricted to Globex Corporation)
```html
<h3>Globex Portal</h3>
<p>Visit <a href="https://downloads.globex.com">https://downloads.globex.com</a></p>
<p>Login credentials: Use your SSO credentials</p>
```

**Section 4: "Post-Installation"** (No restrictions)
```html
<h3>After Download</h3>
<p>Run the installer and follow the on-screen instructions.</p>
```

This approach provides cleaner organization and easier maintenance than conditional tags.

## Support

For questions or issues with conditional logic:
1. Review this documentation
2. Check the troubleshooting section
3. Contact your administrator
4. Submit a support ticket if the issue persists
