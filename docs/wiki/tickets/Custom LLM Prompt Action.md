# Custom LLM Prompt Action

The **Custom LLM Prompt** automation action (`ai-custom-prompt`) lets an
administrator tell the configured LLM (the Ollama module: Ollama, OpenAI or
llama.cpp) what to do with a ticket in their own words, for example:

- summarise the problem and what has been tried so far,
- extract product names and model numbers,
- list identified safety, security or compliance issues,
- pull out outstanding actions, affected users or devices,
- assess requester sentiment,
- draft a resolution summary for a technician to review.

The answer is posted to the ticket as an **internal note**. Model output never
reaches the requester directly, and requesters who opted out of AI processing
are skipped like every other AI ticket action.

## Payload

| Field | Required | Description |
| --- | --- | --- |
| `ticket_id` | No | Ticket to process. Defaults to the triggering ticket (`{{ticket.id}}`). |
| `instructions` | Yes | What the AI should do with the ticket (up to 4000 characters). |
| `desired_outcome` | No | What a good answer looks like: length, tone, one item per product, "empty list when none", and so on. |
| `response_format` | No | `text` (default) for a written answer, or `list` for one item per finding, posted as a bullet list. |
| `conversation` | No | `public` (default) includes public replies, `all` also includes internal notes, `none` sends only the ticket subject and description. |
| `history_limit` | No | Number of most recent replies to include, 1–50 (default 10). |
| `note_title` | No | Heading for the internal note (default "AI response"). |
| `model` | No | Overrides the Ollama module's model for this action. |

The automation editor's payload samples include ready-made prompts for each of
the examples above.

```json
{
  "ticket_id": "{{ ticket.id }}",
  "instructions": "Review the ticket for any physical, electrical or workplace safety hazards.",
  "desired_outcome": "One item per hazard. Return an empty list when no safety issue is described.",
  "response_format": "list",
  "conversation": "public",
  "note_title": "Safety issues identified"
}
```

## Behaviour

- The ticket subject, description, status, priority, category and the selected
  conversation history are sent as untrusted records, separate from your
  instructions. Instructions found inside ticket content are not followed.
- `instructions` and `desired_outcome` are **not** template-rendered: a
  placeholder such as `{{ticket.description}}` is sent literally. Ticket data
  reaches the model only through the records above, so customer text cannot
  rewrite your instructions. Use `conversation` and `history_limit` to choose
  what the model sees.
- When the AI finds nothing (an empty answer or empty list), no note is posted
  and the action is recorded as skipped.
- Answers that are not valid JSON in the requested shape, longer than 8000
  characters, or lists of more than 100 items (or items longer than 500
  characters) are rejected and recorded as errors without posting a note.
- Usage is reported under **Custom LLM prompt** on the
  [LLM Usage](../administration/LLM%20Usage.md) page.
