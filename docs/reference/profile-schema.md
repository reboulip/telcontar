# Profile Schema Reference

A domain profile is a TOML file under `profiles/` that adapts the engine to a specific kind of document corpus. This page documents every field the engine reads, with types, defaults, and validation rules.

See [Domain Profiles](../user-guide/profiles.md) for a conceptual overview. See [Adding a Profile](../developer/adding-profiles.md) for a step-by-step authoring guide.

---

## Top-level fields

```toml
name = "my_profile"                    # required, unique identifier (no spaces)
description = "Human-readable blurb."  # optional
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **yes** | Profile identifier. Must match the filename stem. Used in logs and the agent's system prompt. |
| `description` | string | no | Short description of the corpus this profile targets. |

---

## `[[document_types]]`

Repeated table — define one `[[document_types]]` block per document type. At least one is required.

```toml
[[document_types]]
id = "report"
label = "Report"
description = "A formal written account of findings or recommendations."
```

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | **yes** | Machine-readable identifier. Passed to `record_document(type=...)` and validated server-side. Must be unique within the profile. |
| `label` | string | no (defaults to `id`) | Human-readable label. Shown in the agent's system prompt. |
| `description` | string | no | Description injected into the system prompt to help the agent classify documents. |

!!! warning
    Duplicate `id` values within a profile cause a `ValueError` at load time and prevent the server from starting.

---

## `[entities]`

```toml
[entities]
kinds = ["person", "org"]
role_taxonomy = [
    "author",
    "mentioned",
    "sponsor",
]
salient_cap = 5
```

| Field | Type | Default | Description |
|---|---|---|---|
| `kinds` | list of strings | `["person", "org"]` | Allowed values for the `kind` field in entity records. Not validated server-side but injected into the system prompt. |
| `role_taxonomy` | list of strings | `[]` | Allowed `role` values in entity records. **Validated server-side** in `record_document` — an entity with a role not in this list raises `ValueError`. |
| `salient_cap` | int | `5` | The maximum number of key actors returned by `get_actors` (hard cap in the tool) and surfaced in the synthesis (injected as a prompt instruction). A value of `0` or negative disables the cap and returns all actors. |

---

## `[extraction]`

```toml
[extraction]
required = ["title", "summary", "provenance"]
optional = ["date", "author"]
```

| Field | Type | Default | Description |
|---|---|---|---|
| `required` | list of strings | `[]` | Metadata fields the agent **must** produce in every `record_document` call. Injected into the system prompt as hard requirements. Not validated server-side (the agent is responsible). |
| `optional` | list of strings | `[]` | Fields the agent should produce **if possible** but may leave `null`. Injected as "SI POSSIBLE" guardrails. |

The three universally required fields for `record_document` are `title`, `summary`, and `provenance` — these are validated positionally in the tool call regardless of profile settings.

---

## `[synthesis]`

```toml
[synthesis]
template = "project_synthesis"
title = "Synthèse du projet"
sections = [
    "Vue d'ensemble — objet du corpus et état d'avancement général",
    "Acteurs principaux — qui intervient et à quel titre",
    "Chronologie — les évènements clés datés, par ordre chronologique",
    "Documents clés — les livrables majeurs et leur apport",
    "Doublons et versions — duplicats et versions modifiées repérés",
    "Points d'attention — risques, manques ou décisions en suspens",
]
instructions = """\
Rédige SUMMARY.md en français comme une synthèse de projet structurée par les \
sections ci-dessus (une rubrique Markdown ## par section)..."""
```

| Field | Type | Default | Description |
|---|---|---|---|
| `template` | string | `""` | Name of the synthesis template. Currently `"project_synthesis"` is the only built-in. Reserved for future plugin expansion. |
| `title` | string | `""` | Title of the synthesis document. Injected into the agent's "Project synthesis" prompt section as the SUMMARY.md heading. |
| `sections` | list of strings | `[]` | Ordered list of section names. Each entry becomes one `##` heading in SUMMARY.md. The agent composes the sections in this order, drawing on `list_documents`, `get_registry`, `list_events`, `get_graph`, and `get_actors`. |
| `instructions` | string | `""` | Prose writing rules injected verbatim at the end of the "Project synthesis" prompt section. Use this to set language, tone, and grounding constraints (e.g. "never invent a fact not present in the data"). |

`profile.synthesis()` returns all four fields as a dict `{template, title, sections, instructions}`. The host's `_build_synthesis_section` renders them into the agent's system prompt. `write_summary` is unchanged — it remains a plain sink that persists whatever Markdown the agent composes.

---

## `[naming]`

```toml
[naming]
convention = "snake_case_iso_dates"
instructions = """\
Use snake_case file names. Prefix with YYYY-MM-DD when the date is known. \
Transliterate accented characters. Keep the original extension."""
```

| Field | Type | Default | Description |
|---|---|---|---|
| `convention` | string | `""` | Convention identifier. Currently informational; the agent uses `instructions` to decide. |
| `instructions` | string | `""` | Free-text naming rules injected verbatim into the agent's system prompt under a "File-naming conventions" section. Overrides the built-in default convention. |

!!! tip
    You can also override naming conventions at run-time without editing the profile: place a `.organizer/NAMING.md` file in the project root. The host reads it at startup and injects it instead of the profile's `instructions`. This takes precedence over both the profile and the built-in default.

---

## `[sinks]`

```toml
[sinks]
default = ["local_markdown"]
```

| Field | Type | Default | Description |
|---|---|---|---|
| `default` | list of strings | `["local_markdown"]` | Active output sinks. `"local_markdown"` writes `INDEX.md`, `manifest.json`, and `SUMMARY.md` to the target directory. Additional sinks are planned as plugins (v0.8.0+). |

---

## Full annotated example

```toml
# profiles/my_corpus.toml

name = "my_corpus"
description = "Legal archive for company contracts."

[[document_types]]
id = "contract"
label = "Contract"
description = "Legally binding agreement between two or more parties."

[[document_types]]
id = "amendment"
label = "Amendment"
description = "Modification or addendum to an existing contract."

[[document_types]]
id = "correspondence"
label = "Correspondence"
description = "Letters or emails related to contract negotiation."

[[document_types]]
id = "other"
label = "Other"
description = "Anything not covered above."

[entities]
kinds = ["person", "org"]
role_taxonomy = ["signatory", "counsel", "mentioned", "author"]
salient_cap = 4

[extraction]
required = ["title", "summary", "provenance"]
optional = ["date", "author"]

[synthesis]
template = "project_synthesis"
title = "Contract corpus summary"
sections = [
    "Overview — what this corpus covers and its scope",
    "Key parties — who appears and in what capacity",
    "Timeline — dated milestones in chronological order",
    "Key documents — major agreements and their significance",
    "Duplicates and versions — redundant or superseded files",
    "Points of attention — open items or risks, only if evidenced",
]
instructions = """\
Write SUMMARY.md in English. Structure it with one ## section per item above. \
Draw only on the registry (list_documents / get_registry), the event journal \
(list_events), the knowledge graph (get_graph), and ranked actors (get_actors). \
Never invent a fact not present in the data."""

[naming]
convention = "snake_case_iso_dates"
instructions = """\
Use snake_case. Prefix with the contract signing date as YYYY-MM-DD when known. \
Include the counterparty name. Keep the original extension. \
Example: 2024-03-01_acme_corp_services_contract.pdf"""

[sinks]
default = ["local_markdown"]
```

Activate it with:

```ini
PROFILE=my_corpus
```
