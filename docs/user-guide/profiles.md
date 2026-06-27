# Domain Profiles

A **domain profile** is a TOML file under `profiles/` that externalises everything corpus-specific from the engine:

- The controlled **document-type vocabulary** (`record_document` validates against it)
- The **entity/role taxonomy** and how many key actors to surface in the synthesis
- **Extraction guardrails** — which metadata fields are required vs best-effort
- The **naming convention** instructions injected into the agent's system prompt
- **Output sinks** (currently `local_markdown`; plugin-based extensions planned)

Swapping profiles changes telcontar's entire domain model — no code changes, no restart needed beyond pointing `PROFILE=` at the new file.

---

## Bundled profiles

Three profiles ship with telcontar. Each proves that the engine is domain-agnostic: swap `PROFILE=` and the agent's entire vocabulary, naming convention, entity model, and synthesis style change with no code modification.

| Profile | File | Language | Corpus |
|---|---|---|---|
| `is_it_project` | `profiles/is_it_project.toml` | French | Corporate IS/IT project document piles |
| `research_papers` | `profiles/research_papers.toml` | English | Academic research-paper corpora |
| `personal_files` | `profiles/personal_files.toml` | French | Personal and administrative household documents |

---

## Profile: `is_it_project`

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

### Synthesis

The profile defines a French-language project narrative for SUMMARY.md with the title **"Synthèse du projet"** and six ordered sections:

| Order | Section |
|---|---|
| 1 | Vue d'ensemble — objet du corpus et état d'avancement général |
| 2 | Acteurs principaux — qui intervient et à quel titre (rôles), d'après les acteurs classés (`get_actors`) |
| 3 | Chronologie — les évènements clés datés (journal d'évènements), par ordre chronologique |
| 4 | Documents clés — les livrables majeurs et leur apport (provenance) |
| 5 | Doublons et versions — duplicats et versions modifiées repérés |
| 6 | Points d'attention — risques, manques ou décisions en suspens, uniquement si étayés |

The agent composes the prose for each section from the registry (`list_documents` / `get_registry`), the event journal (`list_events`), the knowledge graph (`get_graph`), and the ranked actors (`get_actors`). It never invents facts not present in the data. `write_summary` persists the resulting Markdown to `SUMMARY.md` in the target directory.

### Naming convention

Files are renamed to `snake_case` with an ISO date prefix when known (`YYYY-MM-DD_`). Accents are transliterated, extensions kept, redundant version suffixes dropped.

---

---

## Profile: `research_papers`

The `profiles/research_papers.toml` profile is designed for **academic research-paper corpora** (English, scholarly vocabulary).

### Document types

| ID | Label | When to use |
|---|---|---|
| `journal_article` | Journal article | Peer-reviewed article published in an academic journal |
| `preprint` | Preprint | Manuscript shared before peer review (arXiv, bioRxiv, SSRN, …) |
| `conference_paper` | Conference paper | Paper published in conference or workshop proceedings |
| `review` | Review / survey | Literature review, survey, or systematic review of a field |
| `thesis` | Thesis / dissertation | Master's or doctoral thesis |
| `book_chapter` | Book chapter | Chapter in an edited volume or monograph |
| `technical_report` | Technical report | Working paper, white paper, or institutional technical report |
| `dataset_doc` | Dataset / supplementary | Data paper, dataset description, or supplementary material |
| `notes` | Reading notes | Personal reading notes or annotations, not a published work |
| `autre` | Other (specify) | Type not covered above; specify in attributes |

### Entity model

| Kind | Roles |
|---|---|
| `person` | `author`, `coauthor`, `advisor`, `editor`, `reviewer`, `cited`, `autre` |
| `org` | `institution` |

- **`salient_cap = 5`** — at most 5 key researchers are surfaced in the synthesis
- **Author guardrail** — the agent only assigns `author` or `coauthor` when the attribution is explicit in the document; it never infers one

### Extraction

Required fields: `title`, `summary`, `provenance`

Optional (best-effort): `date`, `author`

### Synthesis

The profile defines an English-language literature synthesis for SUMMARY.md with the title **"Research corpus synthesis"** and seven ordered sections: Overview, Key researchers, Timeline, Key works, Themes & threads, Duplicates & versions, Gaps & open questions.

The agent draws on the registry, the event journal (`list_events`), the knowledge graph (`get_graph`), and the ranked researchers (`get_actors`). It never invents a citation, author, venue, or date absent from the data.

### Naming convention

Files are renamed to `firstauthor_year_keyword` in snake_case (e.g. `smith_2021_transformers.pdf`). Accents are transliterated, extensions kept, redundant suffixes such as "final" or "v2" dropped when the year already disambiguates.

---

## Profile: `personal_files`

The `profiles/personal_files.toml` profile is designed for **personal and administrative household documents** (French, issuer/recipient role model).

**Note:** this profile deliberately omits the `author` concept — a bank statement or invoice does not have an author in the usual sense. The `author` role is absent from the role taxonomy and `author` is not an extraction field. This intentional asymmetry demonstrates that extraction fields and roles are fully profile-driven.

### Document types

| ID | Label | When to use |
|---|---|---|
| `facture` | Facture | Facture ou reçu d'achat (énergie, télécom, commerce, santé…) |
| `contrat` | Contrat | Contrat ou convention (bail, assurance, abonnement, prêt) |
| `releve_bancaire` | Relevé bancaire | Relevé de compte, relevé de carte ou document bancaire |
| `courrier_administratif` | Courrier administratif | Courrier d'une administration ou d'un organisme (CAF, impôts, sécu) |
| `attestation` | Attestation / justificatif | Attestation, certificat ou justificatif (domicile, assurance, scolarité) |
| `fiche_de_paie` | Fiche de paie | Bulletin de salaire ou document lié à la rémunération |
| `impot` | Document fiscal | Avis d'imposition, déclaration de revenus ou document fiscal |
| `sante` | Document de santé | Document médical, remboursement, ordonnance ou mutuelle |
| `notes` | Notes | Notes personnelles non structurées |
| `autre` | Autre (préciser) | Type non couvert ci-dessus ; préciser dans les attributs |

### Entity model

| Kind | Roles |
|---|---|
| `person` | `titulaire`, `emetteur`, `destinataire`, `mentionne`, `autre` |
| `org` | `organisme` |

- **`salient_cap = 5`** — at most 5 key actors (household members, recurring organisations) are surfaced in the synthesis
- **No author concept** — the `author` role does not exist in this profile; use `emetteur` for the issuing organisation and `titulaire` for the household member the document concerns

### Extraction

Required fields: `title`, `summary`, `provenance`

Optional (best-effort): `date`

Note that `author` is intentionally absent from `optional` — the engine will not attempt to extract it for this corpus.

### Synthesis

The profile defines a French-language personal synthesis for SUMMARY.md with the title **"Synthèse des documents personnels"** and six ordered sections: Vue d'ensemble, Acteurs et organismes, Chronologie, Pièces importantes, Doublons et versions, Points d'attention.

The agent draws on the registry, the event journal, the knowledge graph, and the ranked actors. It never invents a date, amount, or organisation absent from the data.

### Naming convention

Files are renamed to `snake_case` with an ISO date prefix when known: `YYYY-MM-DD_type_organisme` (e.g. `2024-03-12_facture_edf.pdf`). Accents are transliterated, extensions kept, redundant suffixes (« copie », « v2 ») dropped when the date already distinguishes the document.

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
