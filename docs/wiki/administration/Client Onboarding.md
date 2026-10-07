# Client Onboarding

**Administration > Client Onboarding** (`/admin/client-onboarding`, super admins) sends a new
client a public form that sets them up in MyPortal. Each client gets their own single-use
magic link, so they do not need a portal account.

## Sending an onboarding link

1. Open **Client Onboarding** and select **+ New onboarding link**:
   - **Company name** and **Contact name**: enter at least one. The company name pre-fills the
     business name on the form; the contact name pre-fills the main site's primary contact. The
     form and email greet the contact by first name, or the company when there's no contact.
   - **Contact email** (optional) is where the link is sent when **Email the link to the contact**
     is ticked.
   - **Personal message** (optional) is added to the invitation email, for example a note about
     your call or next steps.
   - **Link works for** sets how long the link stays valid (7–90 days, default 30).
2. Select **Create link** (or **Create and email link**). The link is shown once with a
   **Copy link** button, so copy it now if you are not emailing it.

Only a hash of the link token is stored. If a link is lost, use **Regenerate link** from the
row's **Actions** menu (the old link stops working). **Revoke link** cancels a link that has not
been submitted.

## Customising the invitation email

The invitation email comes from two message templates under **Automation & AI > Message
Templates**, shared by every onboarding link. They're labelled *Used for client onboarding
invitation*. The modal links straight to the body template.

- **Client onboarding invitation subject** (`client-onboarding-invitation-subject`, plain text)
- **Client onboarding invitation** (`client-onboarding-invitation`, HTML)

Both can use these variables:

| Variable | Value |
| --- | --- |
| `{{ recipient.greeting_name }}` | Contact's first name, else the company name |
| `{{ contact.name }}`, `{{ contact.first_name }}`, `{{ contact.email }}` | The contact entered in the modal |
| `{{ company.name }}` | The company name (or "your business" when only a contact was entered) |
| `{{ onboarding.link }}` | The client's magic link |
| `{{ onboarding.expires }}`, `{{ onboarding.expires_days }}` | When the link stops working |
| `{{ onboarding.message }}` | The personal message, as paragraphs (empty when none was entered) |
| `{{ sender.name }}`, `{{ sender.email }}` | The technician who created or regenerated the link |
| `{{ app.name }}`, `{{ portal.url }}` | Portal name and address |

Replies go to the technician who sent the link. If a template is deleted, MyPortal falls back to
its built-in wording.

## Custom questions

Select **Custom questions** from **Client Onboarding** to open
`/admin/client-onboarding/questions`. Super admins can add, edit, delete and order questions
for every client onboarding form.

For each question, enter its label, choose a field type and decide where it appears:
**Your business**, **Sites**, **Billing**, **Review**, or **Each site**. Questions assigned to
**Each site** repeat for every location the client adds. You can make a question required and
add help text to explain what the client should enter.

Available field types are **Short answer**, **Long answer**, **Email**, **Phone number**,
**Date**, **Dropdown**, **Multi-select**, **Tick box**, **Single choice (radio buttons)**,
**Number**, **Website URL**, **Time** and **Date and time**. Dropdown, multi-select and
single-choice questions need their choices entered **one per line**. Use a tick box for a
single yes/no answer, and multi-select when the client can choose several options.

Saved questions apply to every open onboarding form, including links created before the
question was added. Required questions and field formats are checked before submission.
Changing or deleting a question does not change earlier submissions: each submitted answer
keeps its original question label, field type and displayed value.

## What the client fills in

The form is a branded, four-step page that works on phones and desktops:

1. **Your business:** business name, main phone and general email (all required), plus an
   optional website.
2. **Sites:** one card per location (up to 20). **+ Add a site** opens an editor with three
   tabs: **Address**, **Primary contact** (name, email, phone) and **Opening hours** (time zone,
   an on/off switch per day, an optional break for split shifts, and quick-fill buttons such as
   *Mon–Fri, 8:30am–5pm* or *Open 24/7*). A live summary shows the site as it will be saved.
3. **Billing:** *Same as the main site contact*, or someone else's name, email and phone.
4. **Review:** everything on one page with **Edit** links, optional notes, and a confirmation
   tick box before **Send my details**.

Custom questions appear in their selected step or in each site's editor. The **Review** step
also shows the client's custom answers before they submit.

Each step is checked before the client moves on, with messages next to the fields that need
attention. If the server finds a problem, the client sees a summary at the top and lands on the
step to fix; everything they typed is kept. The form still works without JavaScript (all steps
show on one page), and the client is warned before leaving with unsaved changes.

## What happens on submission

MyPortal automatically:

- Creates the **company** in a **Pending approval** state with **Invoice Due Days = 7** and
  **Payment Methods = Invoice prepay**. The company phone is the main phone, and the company
  address comes from the first site.
- Adds each site as a **company address** (labelled with the site name) with its phone,
  primary contact and business hours.
- Creates a **staff contact** for each primary contact and the billing contact (a person used
  for several roles is created once) and marks the billing contact as a **billing contact**.
  These contacts stay **disabled** until the company is approved.
- Saves the first site's hours as the company's **business hours**, which drive SLAs and
  business-hours automations.
- Raises a **support ticket** for the company in the **New Client** status, with the primary
  contact of the first site as requester and a summary of everything submitted. Use this ticket
  to send the client their onboarding information. The `New Client` status is added by the
  upgrade and recreated automatically if it has been removed.
- Includes the custom answers, grouped by form section and site, in the **New Client** ticket.

## Reviewing and approving the company

Pending companies are listed under **Waiting for approval** at the top of **Companies**, and their
status shows **Pending approval**. Open one to review it: the company page shows a **Pending
approval** panel summarising each site's address, primary contact, phone and business hours
as the client entered them. Edit anything that needs correcting with the normal company page
sections (details, addresses, business hours, staff), then select **Approve company**.

Select **Company > Onboarding answers** on the company page to see its **Onboarding custom
questions** panel. Answers also appear on the submitted form's page under **Client Onboarding**,
grouped by section and site. These displays use the saved answers so they remain readable after
questions are edited or deleted. Super admins can still review them on the company page after
approval.

Approving activates the company, enables the contacts created by the form and marks the
onboarding link **Approved**. The approval is recorded in the audit trail. You can also approve
from the submission's page under **Client Onboarding**.

If a company with the same name already exists the client is asked to check the name. If setup
fails part-way, the link shows **Needs attention**; open it to see the submitted details and the
error, and finish setting up the client manually.

## Security notes

- `/onboarding/{token}` is public and exempt from CSRF checks because the unguessable,
  single-use token in the URL authenticates the request.
- The page is served with `noindex` and `Referrer-Policy: no-referrer` so the token is not leaked
  to search engines or other sites.
- Creating, regenerating and revoking links is recorded in the audit trail.
