# Office 365 group cleanup

**Office 365 → Group Cleanup** (`/m365/group-cleanup`) finds groups and
distribution lists in the selected company's tenant that still contain people
who have left, and removes them on request.

## Who counts as removable

A member is listed when any of these is true:

| Reason shown | Meaning |
| --- | --- |
| Sign-in blocked in Microsoft 365 | The account's `accountEnabled` is false. |
| Ex-staff in MyPortal | A staff record with this address is marked ex-staff. |
| Disabled in MyPortal | A staff record with this address is disabled. |

An address that also belongs to an active staff record is never flagged, so a
re-hired person or a duplicate record cannot be removed by mistake. Only
direct members are considered.

## Using the page

1. **Run scan** queues a background scan. It reads the tenant's users once,
   picks those who have left, then reads each one's direct group memberships.
   The page refreshes until it finishes; the latest result is kept per company.
2. The table lists each group with its type and number of removable entries.
   **View** opens the member list with the reason for each person.
3. **Clean…** opens the same list with everyone selected. Untick anyone to
   keep, then **Remove N from group**.

Before anything is removed, MyPortal re-reads the group's members from
Microsoft 365 and re-checks each one, so people who were re-enabled since the
scan are skipped. Every clean is audited as `m365.group_cleanup.clean` with
the group and the members removed or not removed.

## How members are removed

| Group type | Removed with |
| --- | --- |
| Microsoft 365 group, security group | Microsoft Graph `DELETE /groups/{id}/members/{id}/$ref` (`GroupMember.ReadWrite.All`) |
| Distribution list, mail-enabled security group | Exchange Online `Remove-DistributionGroupMember` (`Exchange.ManageAsApp` + Exchange Administrator role) |

Groups with dynamic membership and groups synced from on-premises Active
Directory are listed but cannot be cleaned here; the page says where to change
them instead.

## Access

The **Group Cleanup** menu permission (`menu.m365.group_cleanup`, Office 365
group) controls the page. Read access shows the results; write access allows
scans and removals. Super administrators always have access. The page is part
of the `m365_admin` feature pack.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/m365/group-cleanup` | Latest scan results |
| POST | `/m365/group-cleanup/scan` | Queue a scan (write) |
| POST | `/m365/group-cleanup/{group_id}/clean` | Remove members; `scope=selected` with `member_ids` removes only those (write) |
