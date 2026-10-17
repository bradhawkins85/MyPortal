# Account erasure and backups

This page explains how MyPortal handles a person's request to be forgotten
(GDPR Article 17), why ticket history is kept, and how an anonymisation stays
in force when an older backup is restored.

For the request form and the approval workflow, see
[Legal Policies → Account anonymisation requests](../wiki/administration/Legal%20Policies.md#account-anonymisation-requests).

## What is kept, and why that is compliant

Approving a request anonymises the account rather than deleting it. Tickets,
replies, invoices and orders stay, because they are the business's records of
work done for a customer company. The person's details are removed from them:

- the account's name becomes "Anonymised user" and its email address becomes
  `anonymised+<id>@invalid`, which can't receive email;
- replies they wrote show "Anonymised user" as the author;
- their email address, phone numbers and full name are redacted (replaced with
  `[redacted]`) in their own replies, and in the text and replies of the
  tickets they raised;
- sign-in details, sessions, call recordings, voicemail, SMS links, chat
  identity and email tracking are deleted.

Replies stay linked to the account's id, but the account no longer holds
anything that identifies the person. GDPR does not apply to information that
can't be linked to a person by means reasonably likely to be used (Recital 26),
so the remaining history is anonymous **only if no other copy can link the id
back to the person**. Backups are such a copy. The erasure ledger below is what
stops them from doing so.

Where you still need some records about the person, Article 17(3) allows it:
for example invoices you must keep for tax law, or records you need for a legal
claim. MyPortal keeps those records with the personal details removed.

Some limits to be aware of:

- Free text can still mention the person in ways a search can't find, such as
  a nickname or a first name on its own. A first name alone isn't redacted,
  because replacing every "Sam" would garble other people's text. Review the
  person's tickets if they were especially sensitive.
- Data held outside MyPortal is not touched: mailboxes, your Microsoft 365
  tenant, an external Matrix server, accounting systems, and email or web
  server logs.

## The backup problem

Database backups (`db-*.sql.gz`) taken before an anonymisation still hold the
person's details. Restoring one, after a failed upgrade for example, would
bring the person back as they were. MyPortal can't rewrite old backup files,
so it makes sure a restore never brings the person back into use.

## The erasure ledger

Every completed anonymisation is appended to the erasure ledger, a small file
that is **not** part of the database:

| Install | Default location |
|---------|------------------|
| systemd | `/opt/myportal/shared/data/privacy/erasure-ledger.jsonl` |
| Docker  | `/app/var/data/privacy/erasure-ledger.jsonl` in the `state` volume |
| Any     | `PRIVACY_ERASURE_LEDGER_PATH`, when set |

Because the file lives outside the database, restoring a database backup
leaves it untouched. MyPortal compares the ledger with the database:

- **when it starts**, before it serves any request. Both
  `myportal-backup --restore` and `myportal-docker restore-db` restart
  MyPortal, so this runs straight after every scripted restore;
- **every 5 minutes**, for a database restored by hand while MyPortal runs;
- **when a ledger account's session is used**, which is refused until the
  anonymisation has been applied again;
- **on demand**, from **Admin → Account anonymisation → Check now**, or with
  `python manage.py erasure-ledger check`.

For each account in the ledger that a restore brought back, it runs the
anonymisation again. Nothing is emailed to the person, the request is marked
completed (or recreated if the backup predates it), and an
`account_anonymisation.reapply` audit entry is written. It also copies into
the ledger any completed request that is missing from it, so anonymisations
made before the ledger existed are protected from the first check.

### What an entry holds

Each line records the user id, the request id, the time, and the SHA-256 hash
of the email address the request already keeps for marketing opt-outs. It
holds no names or addresses. The hash can't be turned back into the address,
but anyone who already knows an address can check it against the ledger, so
treat the file as confidential. Lines are hash-chained: if a line is edited or
removed, the admin page and `manage.py erasure-ledger verify` say so. A damaged
ledger still applies every entry it holds.

### Reused ids

Restoring a database also resets the counter that numbers new accounts, so a
new sign-up could be given an anonymised account's id. MyPortal therefore only
re-anonymises an account when its email address matches the one that was
anonymised, and it moves the counter past every anonymised id so ids aren't
handed out again.

If the address doesn't match, the account is listed under **Needs a decision**
on **Admin → Account anonymisation**. It is either the same person under an
address they used before the backup was taken, or a different person. A super
admin checks who it is and chooses:

- **Same person: anonymise again** anonymises the account and records the older
  address's hash, so restoring the same backup again needs no decision; or
- **Different person** records that the account is someone else and leaves it
  unchanged.

## What you need to do as an operator

1. **Keep the ledger out of database restores.** Don't put it inside the
   database or roll it back with database snapshots. The default locations
   above already do this.
2. **Keep a copy off the server.** Each backup run saves the current ledger as
   `erasure-ledger.jsonl` in the backup directory. It never replaces a longer
   copy with a shorter one. Copy that file off the server along with the
   environment file, or run `python manage.py erasure-ledger export`. It holds
   no personal details beyond the email hashes described above.
3. **When rebuilding a server, restore the ledger before MyPortal starts.**
   `myportal-backup --restore` puts the saved copy back if the live ledger is
   missing. `myportal-docker restore-db` merges the saved copy in and runs a
   check. If you restore another way, run
   `python manage.py erasure-ledger import /path/to/copy.jsonl` and then
   `python manage.py erasure-ledger check`. Importing only adds entries, so it
   is always safe.
4. **Limit backup retention.** Old backups hold anonymised people's details
   until they are deleted. Keep `MYPORTAL_BACKUPS_TO_KEEP` and your off-site
   retention as short as your recovery needs allow, and don't use backups for
   anything except restoring MyPortal.
5. **Don't load backups into other systems** (test, reporting or analytics
   copies). The ledger only protects databases a MyPortal install with the
   ledger runs against.
6. **Files backups.** Restoring a `files-*.tar.gz` archive on its own can bring
   back deleted recording and voicemail files without database rows pointing
   at them. Restore files and database backups from the same point, and let the
   check run.

## Commands

```bash
python manage.py erasure-ledger check            # re-apply now; exit 1 if anything needs review
python manage.py erasure-ledger verify           # report unreadable or edited lines
python manage.py erasure-ledger export > copy.jsonl
python manage.py erasure-ledger import copy.jsonl
```

On systemd installs, run them from the current release as the service user,
so the ledger stays writable by MyPortal:
`cd /opt/myportal/current && sudo -u myportal .venv/bin/python manage.py erasure-ledger check`.
In Docker installs, run them in the serving container, for example
`docker exec -i <container> python manage.py erasure-ledger check`.

## Why not cryptographic erasure?

Cryptographic erasure ("crypto-shredding") encrypts each person's data with
their own key and deletes the key to erase them. Every copy of the data,
backups included, then becomes unreadable at once. It was considered and not
used for MyPortal, for these reasons:

- **The identifying fields are used everywhere.** Names and email addresses are
  read by hundreds of queries: sign-in looks accounts up by email address, and
  searching, sorting, joins, reports and the report builder use names directly.
  Encrypting them would need lookup hashes for sign-in, and rewriting every
  search, sort and report. That is a large, risky change for a working
  service desk.
- **The keys have the same backup problem.** The per-person keys can't live in
  the database, or a restored backup would bring the key back with the data.
  They would need their own store, and that store needs backups too. Restoring
  an older copy of the key store restores a deleted key. Losing it makes every
  backup unreadable. Without an external key management service (such as AWS
  KMS, Azure Key Vault or HashiCorp Vault) that can delete a key permanently,
  crypto-shredding still depends on a record kept outside the backups, which
  is what the ledger already is.

The ledger gives the same result for restores (an anonymised person never
comes back into use), works with the existing `mysqldump` backups, and needs
no change to how data is stored or queried. If you later need old backup files
themselves to be unreadable before they expire, the next step would be
field-level encryption of the identity columns with keys held in an external
key management service.
