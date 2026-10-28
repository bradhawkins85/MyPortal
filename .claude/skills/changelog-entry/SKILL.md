---
name: changelog-entry
description: Add a change log entry (changes/<guid>.json) for a feature or fix. Use for every new feature or fix before committing.
argument-hint: <Feature|Fix|Improvement|Security> "<summary>"
---

# Change log entry

Every feature or fix gets one entry. Create it with the helper; it generates
the GUID filename, the UTC `occurred_at`, and the `content_hash` exactly as
`app/services/change_log.py` computes it:

```bash
python3 .claude/skills/changelog-entry/new_entry.py Fix "Summary of the change"
```

- `change_type`: `Feature`, `Fix`, `Improvement` or `Security`.
- Write the summary for the people using MyPortal, not for developers: one
  sentence saying what they can now do or what no longer goes wrong. Match the
  tone of recent entries in `changes/`.
- Don't edit `summary` or `change_type` afterwards by hand; the hash would no
  longer match. Delete the file and run the helper again instead.
- Commit the new file with the change it describes.
