# Marketing Assistant

The Marketing Assistant writes marketing pages for you from websites you choose, so you don't have
to copy vendor information into pages by hand. It only uses what those websites say: every
statement is checked against them before it is used, and anything it cannot verify is left out and
listed for you to review. Pages get a mix of text and images taken from the same websites.

Anyone who can manage marketing pages (`marketing.access`) can use it.

## Writing a page

1. Go to **Marketing** and create a page. Paste the websites the page should be built from into
   **Source websites for the Marketing Assistant**, one address per line (for example a vendor's
   product page, its datasheet and a review). The Marketing Assistant opens next.
   For an existing page, open **Marketing Assistant** from the page editor or the pages list and add
   websites under **Source websites**.
2. Optionally write a **brief**: the audience, tone and what to focus on (for example "small
   accounting firms; focus on backup and recovery"). The brief guides the writing only; it cannot
   add facts.
3. Choose **Write the article** and **Run assistant**. The page refreshes when the run finishes.
4. Review the page with **View page**, then publish it from the page editor when you're happy.

## Refreshing a page

Run the assistant again with **Refresh with new information**. It keeps the current structure,
updates facts that changed on the websites, adds new ones and drops anything the websites no
longer say. Add or remove websites first if the page should use different sources.

Only sections the assistant wrote (marked **Assistant** in the page editor) are replaced. Sections
you added yourself are always kept.

## How information is checked

Each run:

1. Reads every listed website (up to `MARKETING_ASSISTANT_MAX_SOURCES`, 10 by default). Only public
   addresses can be read; private, local and cloud metadata addresses are refused on every redirect.
2. Drafts the page as short statements, each naming the websites it came from.
3. Cross-checks every statement in a separate fact-checking pass. A statement is used only when:
   - at least `MARKETING_ASSISTANT_MIN_SOURCES_PER_CLAIM` of the websites state it (1 by default;
     raise it to require corroboration from several websites — when fewer websites could be read,
     all of them must confirm it),
   - no website contradicts it, and
   - every number in it (prices, percentages, dates, counts) appears in a confirming website.
4. Builds the sections only from verified statements and lists the websites each section relied on
   under **Sources** at the bottom of the public page.

The run report under **Runs** shows which websites were read (and why any failed) and every
statement that was **left out**, with the reason, so you can see what the assistant could not
verify.

## Images

Images are only taken from the listed websites: up to `MARKETING_ASSISTANT_MAX_IMAGES` (6 by
default, `0` turns images off) JPEG, PNG, GIF or WebP images, at least 240×140 pixels. Logos in
navigation, icons, tracking pixels and SVG files are skipped. Each image is copied into MyPortal so
the public page serves it itself, and is credited with the website it came from.

Make sure you're allowed to reuse a website's images (vendor partner and media kits usually allow
it) before publishing a page that shows them.

## Undoing a run

Every successful run saves a copy of the page first. **Undo this run** puts the assistant's
sections, subtitle and intro back as they were before that run. Sections you wrote yourself are not
touched.

## Settings

| Setting | Default | Purpose |
|---------|---------|---------|
| `MARKETING_ASSISTANT_LLM_BASE_URL` | *(empty)* | OpenAI-compatible endpoint (`{base_url}/v1/chat/completions`; Ollama serves it too). |
| `MARKETING_ASSISTANT_LLM_MODEL` | *(empty)* | Model name. |
| `MARKETING_ASSISTANT_LLM_API_KEY` | *(empty)* | API key, when the endpoint needs one. |
| `MARKETING_ASSISTANT_MAX_SOURCES` | `10` | Most websites read per run (1-25). |
| `MARKETING_ASSISTANT_MAX_IMAGES` | `6` | Most images placed on a page (0-12). |
| `MARKETING_ASSISTANT_MIN_SOURCES_PER_CLAIM` | `1` | Websites that must confirm each statement (1-5). |

Leave the three LLM settings empty to use the AI troubleshooter's model (`TROUBLESHOOT_LLM_*`, which
itself falls back to `MATRIXBOT_AI_OLLAMA_*`). A model with a large context window gives the best
results when several long websites are listed. Requests appear as **Marketing Assistant** in
[LLM Usage](LLM%20Usage.md).

The assistant is the `marketing_assistant` feature pack: add it to `DISABLED_FEATURE_PACKS` to turn
it off. It is also unavailable while the `marketing` pack is disabled.
