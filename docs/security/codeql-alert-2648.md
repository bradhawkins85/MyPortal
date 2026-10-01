# CodeQL alert 2648 disposition

## Disposition

Alert [2648](https://github.com/bradhawkins85/MyPortal/security/code-scanning/2648),
`py/stack-trace-exposure`, is **fixed**. The reporting query-assistant response no
longer serializes an exception with `str(exc)`. It selects response text from
static, code-keyed allow lists instead.

## Source-to-sink review

The two exception types formerly sharing the response sink have these paths:

1. `ReportingQueryError` is raised by `validate_select_query`. Each validation
   branch now assigns a stable client code. The handler maps only recognized
   codes to static validation text; an absent or unrecognized code produces a
   generic 400 response.
2. `_ClientSafeAIQueryError` is created only for a missing module response, a
   missing SQL response, or an exact match in the approved module-status reason
   allow list. These paths now carry identifiers rather than response text, and
   the handler resolves each identifier through a second static map.
3. Provider-reported status text is used only for exact allow-list matching.
   Unapproved text, provider exceptions, schema/database failures, and all other
   unexpected exceptions continue to receive the generic 503 response.
4. Unknown validation identifiers are logged using only the exception class;
   exception messages, SQL/payload content, credentials, and provider details
   are excluded from both the response and the diagnostic fields.

Regression coverage injects a `ReportingQueryError` containing a credential,
stack-trace marker, and database payload. It verifies the generic response and
checks that neither the response nor captured server diagnostic contains the
injected value. Existing coverage verifies approved module and validation
messages retain their 400 status, while raw provider failures retain the generic
503 response and metadata-only diagnostics.

## Analysis evidence

CodeQL CLI 2.27.0 was run on the repaired tree with the bundled
`python-security-extended.qls` suite. It scanned 1,250 Python files and completed
without an analysis error. The SARIF contained **zero**
`py/stack-trace-exposure` results, so alert 2648 is resolved and no new finding
for the same rule was introduced. The linked alert should be closed as `fixed`
after this change reaches the default branch.
