# Client Onboarding

**Administration > Client Onboarding** (`/admin/client-onboarding`, super admins) sends a new
client a public form that sets them up in MyPortal. Each client gets their own single-use
magic link, so they do not need a portal account.

## Sending an onboarding link

1. Open **Client Onboarding** and select **+ New onboarding link**:
   - **Client name** (optional) pre-fills the business name on the form.
   - **Client email** (optional) is where the link is sent when **Email the link to the client** is ticked.
   - **Link works for** sets how long the link stays valid (7–90 days, default 30).
2. Select **Create link** (or **Create and email link**). The link is shown once with a
   **Copy link** button, so copy it now if you are not emailing it.

Only a hash of the link token is stored. If a link is lost, use **Regenerate link** from the
row's **Actions** menu (the old link stops working). **Revoke link** cancels a link that has not
been submitted.

## What the client fills in

The form is a branded, four-step page that works on phones and desktops:

1. **Your business:** business name, plus optional main phone, general email and website.
2. **Sites:** one card per location (up to 20). **+ Add a site** opens an editor with three
   tabs: **Address**, **Primary contact** (name, email, phone) and **Opening hours** (time zone,
   an on/off switch per day, an optional break for split shifts, and quick-fill buttons such as
   *Mon–Fri, 8:30am–5pm* or *Open 24/7*). A live summary shows the site as it will be saved.
3. **Billing:** *Same as the main site contact*, or someone else's name, email and phone.
4. **Review:** everything on one page with **Edit** links, optional notes, and a confirmation
   tick box before **Send my details**.

Each step is checked before the client moves on, with messages next to the fields that need
attention. If the server finds a problem, the client sees a summary at the top and lands on the
step to fix; everything they typed is kept. The form still works without JavaScript (all steps
show on one page), and the client is warned before leaving with unsaved changes.

## What happens on submission

MyPortal automatically:

- Creates the **company** in a **Pending approval** state with **Invoice Due Days = 7** and
  **Payment Methods = Invoice prepay**. The company address and phone come from the first site
  when no main phone is given.
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

## Reviewing and approving the company

Pending companies are listed under **Waiting for approval** at the top of **Companies**, and their
status shows **Pending approval**. Open one to review it: the company page shows a **Pending
approval** panel summarising each site's address, primary contact, phone and business hours
as the client entered them. Edit anything that needs correcting with the normal company page
sections (details, addresses, business hours, staff), then select **Approve company**.

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
