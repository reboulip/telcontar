# Adding a Profile

A domain profile is a single TOML file in `profiles/`. Adding a new one requires no code changes — only authoring the TOML and pointing `PROFILE=` at it.

---

## When to create a new profile

Create a profile when you have a **different kind of document corpus** with its own:

- Vocabulary of document types (e.g. legal contracts vs. scientific papers vs. HR files)
- Roles and entity model (e.g. lawyer/judge vs. author/reviewer vs. manager/employee)
- Naming convention
- Language (the profile description and labels can be in any language)

The bundled `is_it_project` profile is designed for French-language IS/IT project piles. For a different corpus, a sibling profile will give the agent much more relevant context.

---

## Step-by-step guide

### 1. Copy the existing profile as a starting point

```bash
cp profiles/is_it_project.toml profiles/my_corpus.toml
```

### 2. Edit the top-level fields

```toml
name = "my_corpus"        # must match the filename stem
description = "What this profile covers."
```

!!! warning
    `name` must match the filename stem exactly. `PROFILE=my_corpus` loads `profiles/my_corpus.toml`.

### 3. Define your document types

Replace the `[[document_types]]` blocks with the vocabulary that fits your corpus. Each type needs at minimum an `id`:

```toml
[[document_types]]
id = "contract"
label = "Contract"
description = "A legally binding agreement. Used when the document is signed by both parties."

[[document_types]]
id = "draft"
label = "Draft"
description = "A version of a document not yet finalized or signed."

[[document_types]]
id = "other"
label = "Other"
description = "Anything not covered above."
```

**Tips:**

- Keep `id` values lowercase and underscore-separated
- Always include an `"other"` catch-all
- Descriptions are injected into the system prompt — be specific enough for GPT-5 to classify correctly
- `id` values must be unique within the profile (duplicate IDs cause a startup error)

### 4. Define the entity model

```toml
[entities]
kinds = ["person", "org"]
role_taxonomy = [
    "author",
    "signatory",
    "counsel",
    "mentioned",
]
salient_cap = 4    # max key actors in the synthesis
```

Choose roles that are meaningful for your corpus. The agent will use these to classify people and organisations it finds in documents. `role_taxonomy` values are **validated server-side** — any entity with an unlisted role will cause `record_document` to raise.

### 5. Set extraction guardrails

```toml
[extraction]
required = ["title", "summary", "provenance"]
optional = ["date", "author"]
```

`required` fields are injected as hard requirements in the agent's system prompt. `optional` fields are "best-effort" — the agent leaves them `null` rather than inventing a value. `title`, `summary`, and `provenance` are always required regardless of this setting; include them for clarity.

### 6. Configure naming

```toml
[naming]
convention = "snake_case_iso_dates"
instructions = """\
Use snake_case. Prefix with the document date as YYYY-MM-DD when known. \
Transliterate accented characters to ASCII. Keep the original extension. \
Example: 2024-03-01_services_contract_acme.pdf"""
```

The `instructions` string is injected verbatim into the system prompt under "File-naming conventions". Be concrete — include an example.

### 7. Activate the profile

```ini
# .env
PROFILE=my_corpus
```

### 8. Test it

Run the agent on a small sample directory and verify that:

- Documents are classified into the correct types
- Entity roles match your taxonomy
- Files are renamed according to your naming convention

If the agent misclassifies documents, improve the `description` fields of the relevant types and re-run.

---

## Profile validation

The server validates the profile at startup:

- `name` is present and a string
- At least one `[[document_types]]` entry exists
- No duplicate `id` values in `[[document_types]]`
- Entity `role` values in `record_document` calls must be in `role_taxonomy`

Validation errors surface as a `ValueError` with a descriptive message and prevent the server from starting.

---

## Advanced: runtime naming override

You can override the naming instructions at runtime without editing the profile. Create `.organizer/NAMING.md` in the project root with your naming rules. The host reads this file at startup and injects its content instead of the profile's `naming.instructions`. This is useful for corpus-specific overrides that change frequently.

---

## Full annotated example

See [Profile Schema Reference](../reference/profile-schema.md) for a fully annotated example TOML covering every field.
