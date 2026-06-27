# Domain Profiles

A **domain profile** is a TOML file under `profiles/` that externalises everything corpus-specific from the engine:

- The controlled **document-type vocabulary** (`record_document` validates against it)
- The **entity/role taxonomy** and how many key actors to surface in the synthesis
- **Extraction guardrails** — which metadata fields are required vs best-effort
- The **naming convention** instructions injected into the agent's system prompt
- **Output sinks** (currently `local_markdown`; plugin-based extensions planned)

Swapping profiles changes telcontar's entire domain model — no code changes, no restart needed beyond pointing `PROFILE=` at the new file.

---

## Bundled profile: `is_it_project`

The `profiles/is_it_project.toml` profile is designed for **corporate IS/IT project document piles** (PMO-flavoured, French-language vocabulary).

### Document types

| ID | Label | When to use |
|---|---|---|
| `communication_formelle` | Communication formelle | Official notes, letters, announcements to stakeholders |
| `releve_de_decision` | Relevé de décision | Decision logs from steering committees or arbitrage |
| `document_de_travail` | Document de travail | In-progress deliverables and analyses |
| `support_copil` | Support de comité de pilotage | COPIL slides or dossiers |
| `support_reunion` | Support de réunion de travail | Operational meeting materials |
| `draft_officiel` | Draft de document officiel | Preparatory versions of documents to be made official |
| `notes` | Notes | Unstructured personal or meeting notes |
| `echanges` | Échanges | Email threads or message exchanges |
| `autre` | Autres (préciser) | Anything not covered above |

### Entity model

| Kind | Roles |
|---|---|
| `person` | `author`, `mentioned`, `sponsor`, `chef_de_projet`, `metier`, `prestataire`, `autre` |
| `org` | same roles |

- **`salient_cap = 5`** — at most 5 key actors are surfaced in the synthesis
- **Author guardrail** — the agent only assigns the `author` role when the author is explicitly named in the document; it never infers one

### Extraction

Required fields: `title`, `summary`, `provenance`

Optional (best-effort): `date`, `author`

### Naming convention

Files are renamed to `snake_case` with an ISO date prefix when known (`YYYY-MM-DD_`). Accents are transliterated, extensions kept, redundant version suffixes dropped.

---

## Selecting a profile

Set the `PROFILE` variable in `.env`:

```ini
PROFILE=is_it_project   # default
```

The engine loads `{PROFILES_DIR}/{PROFILE}.toml` at startup.

---

## Creating a new profile

See [Adding a Profile](../developer/adding-profiles.md) for a step-by-step guide to authoring a new `.toml` and what each field controls.
