# Cron expressions

MyPortal schedules use UTC and accept either five fields or an optional sixth,
trailing **year** field:

```text
minute hour day-of-month month day-of-week [year]
```

The year range is **1970 through 2099**, inclusive. The year supports `*`, a
four-digit year, inclusive ranges, comma-separated lists, and combinations of
lists and ranges. Year step expressions are not supported. Omitting the year is
equivalent to `*`, so existing schedules require no migration. The sixth field
is always a year; MyPortal does not support a seconds field.

Existing syntax in the first five fields remains available, including `L` for
the last day of a month and the other supported cron modifiers.

| Expression | Meaning |
| --- | --- |
| `0 9 1 1 * 2027` | 09:00 on 1 January 2027 only |
| `0 9 1 1 * 2027-2029` | 09:00 on 1 January in 2027, 2028, and 2029 |
| `0 9 1 1 * 2027,2029` | 09:00 on 1 January in 2027 and 2029 |
| `0 9 1 1 * 2027,2029-2031` | A list combining a year and a range |
| `0 9 L * * 2028` | 09:00 on every month's last day in 2028 |
