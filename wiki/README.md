# MyPortal Wiki

This directory contains wiki documentation for the MyPortal project. These files are intended to be published to the GitHub wiki.

## Structure

- **Home.md** - Main wiki landing page with navigation to all documentation
- **_Sidebar.md** - Sidebar navigation for quick access to key pages
- **Setup-and-Installation.md** - Getting started guide
- **Configuration.md** - Environment configuration reference
- Individual topic pages organized by category

## Categories

### Core Features
- Authentication, API Keys, Company Management
- Ticketing System, Orders, Issues

### Integration Modules
- Xero, Email (IMAP/SMTP), Uptime Kuma, ChatGPT MCP, OpnForm

### Advanced Topics
- Business Continuity Planning (BCP)
- Security and Compliance
- Deployment
- Development documentation

### Implementation Guides
- BC3-BC18 implementation summaries and guides
- UI documentation
- Test summaries

## Publishing to GitHub Wiki

To publish these files to the GitHub wiki:

1. Clone the wiki repository:
   ```bash
   git clone https://github.com/bradhawkins85/MyPortal.wiki.git
   ```

2. Copy all files from this directory to the wiki repository:
   ```bash
   cp wiki/*.md MyPortal.wiki/
   ```

3. Commit and push:
   ```bash
   cd MyPortal.wiki
   git add .
   git commit -m "Update wiki documentation"
   git push
   ```

## Maintenance

When updating documentation:

1. Update the source file in either `docs/` or root directory
2. Re-run the conversion script to update wiki files
3. Verify internal links are correct
4. Publish to GitHub wiki

## Link Format

Wiki pages use GitHub wiki link format:
- `[Link Text](Page-Name)` - Links to another wiki page
- Page names use Title-Case-With-Hyphens
- No `.md` extension in links

## Notes

- All internal documentation links have been converted to wiki format
- Files are named using Title-Case for better wiki navigation
- The Home page provides comprehensive navigation to all topics
- The Sidebar provides quick access to commonly used pages
