# Wiki Publishing Guide

This guide explains how to publish the MyPortal documentation to the GitHub wiki.

## Overview

All documentation has been converted to wiki format and is located in the `wiki/` directory. The wiki consists of 78 pages covering:

- Core features and APIs
- Integration modules
- Setup and configuration
- Development guides
- Implementation summaries

## Prerequisites

You need:
- Git installed
- Write access to the MyPortal repository
- GitHub authentication configured

## Publishing Steps

### Option 1: Direct Wiki Repository (Recommended)

GitHub wikis are separate git repositories. To publish:

1. **Clone the wiki repository**
   ```bash
   cd /tmp
   git clone https://github.com/bradhawkins85/MyPortal.wiki.git
   ```

2. **Copy wiki files**
   ```bash
   cd MyPortal.wiki
   cp /path/to/MyPortal/wiki/*.md .
   ```

3. **Commit and push**
   ```bash
   git add .
   git commit -m "Update wiki with all documentation"
   git push
   ```

4. **Verify**
   - Visit https://github.com/bradhawkins85/MyPortal/wiki
   - Check that Home page displays properly
   - Verify navigation links work

### Option 2: Manual Upload via GitHub UI

If wiki is not yet enabled:

1. **Enable wiki**
   - Go to repository Settings
   - Enable Wiki in Features section

2. **Create Home page**
   - Click "Create the first page"
   - Copy content from `wiki/Home.md`
   - Save

3. **Add remaining pages**
   - Click "New Page"
   - Copy content from each wiki file
   - Use the filename (without .md) as page title
   - Save

4. **Configure sidebar**
   - Edit `_Sidebar` page
   - Copy content from `wiki/_Sidebar.md`
   - Save

## Wiki Structure

### Main Pages

- **Home.md** - Landing page with complete navigation
- **_Sidebar.md** - Quick navigation sidebar
- **Setup-and-Installation.md** - Getting started guide
- **Configuration.md** - Environment configuration
- **Authentication-API.md** - Authentication endpoints

### Content Organization

```
wiki/
├── Home.md (Main navigation)
├── _Sidebar.md (Quick navigation)
├── README.md (This file)
├── Setup-and-Installation.md
├── Configuration.md
├── Authentication-API.md
├── Company-Context-Switching.md
│
├── Core Features/
│   ├── API-Keys.md
│   ├── Tickets-API.md
│   ├── Orders-API.md
│   ├── Companies-API.md
│   └── Issues-API.md
│
├── Integrations/
│   ├── Xero-Integration.md
│   ├── ChatGPT-MCP.md
│   ├── Uptime-Kuma.md
│   ├── IMAP.md
│   └── SMTP-Relay.md
│
├── Advanced Topics/
│   ├── BCP-Permissions.md
│   ├── Fail2ban.md
│   └── Systemd-Service.md
│
└── Implementation Guides/
    ├── BC3-BC18 summaries
    └── UI documentation
```

## Link Format

Wiki links follow GitHub wiki conventions:

- **Internal links**: `[Text](Page-Name)` - No .md extension
- **External links**: `[Text](https://example.com)`
- **Anchors**: `[Text](Page-Name#section)`

All internal documentation links have been converted to this format.

## Verification Checklist

After publishing, verify:

- [ ] Home page displays with navigation
- [ ] Sidebar appears on all pages
- [ ] All links in Home page work
- [ ] Sidebar links work
- [ ] Internal cross-references work
- [ ] Images display (if any)
- [ ] Code blocks render properly
- [ ] Tables format correctly

## Maintenance

### Updating Wiki Content

When source documentation is updated:

1. Update the file in `docs/` or root directory
2. Re-run the conversion script if needed
3. Copy updated file to wiki repository
4. Commit and push

### Adding New Documentation

1. Create file in appropriate location (`docs/` or root)
2. Run conversion script to add to wiki/
3. Add link to Home.md
4. Update _Sidebar.md if appropriate
5. Publish to wiki

## Troubleshooting

### Wiki Not Enabled

If wiki repository doesn't exist:
- Enable wiki in repository Settings → Features
- Create first page via GitHub UI
- Wiki repository will be created automatically

### Links Not Working

- Verify page names match exactly (case-sensitive)
- Check for spaces vs hyphens in page names
- Ensure no .md extension in links
- Verify target page exists

### Formatting Issues

- Check markdown syntax in source file
- Verify code blocks use proper fencing
- Ensure tables have proper formatting
- Check for special characters that need escaping

## Statistics

- **Total pages**: 78
- **Total lines**: 15,721
- **Categories**: 5 main categories
- **Implementation guides**: 16 BC guides
- **API documentation**: 5 major APIs
- **Integration guides**: 7 integrations

## Next Steps

1. Review this guide
2. Choose publishing method
3. Follow steps to publish
4. Verify all pages load correctly
5. Test navigation and links
6. Share wiki URL with team

## Resources

- [GitHub Wiki Documentation](https://docs.github.com/en/communities/documenting-your-project-with-wikis)
- [Markdown Guide](https://guides.github.com/features/mastering-markdown/)
- [MyPortal README](https://github.com/bradhawkins85/MyPortal/blob/main/README.md)
