"""End-to-end integration tests: the full server tool-chain against a fixture directory.

Design (F1): **no LLM.** A temporary fixture directory is seeded, then the real
server tool implementations are driven in-process through the complete lifecycle —

    list_dir → read_file / extract_text → compute_checksum → record_document
      → propose_* → review_plan → approve_plan → execute_plan (+ registry reconcile)
      → undo_last → write_index → write_summary

— asserting real file-I/O effects (files actually move / are quarantined),
registry + journal persistence, checksum-stable identity across moves, undo
reversal, and output-artifact generation. These complement the per-tool unit
tests by exercising the tools *together*, sharing on-disk state through tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.profile import Profile, load_profile
from server.tools import (
    approve_plan,
    compute_checksum,
    create_plan,
    execute_plan,
    extract_text,
    find_modified_documents,
    get_document,
    list_documents,
    list_dir,
    propose_move,
    propose_quarantine,
    propose_rename,
    read_file,
    record_document,
    review_plan,
    undo_last,
    write_index,
    write_summary,
)

_BUNDLED_PROFILES_DIR = Path(__file__).resolve().parents[1] / "profiles"

# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture()
def profile() -> Profile:
    return load_profile("is_it_project", _BUNDLED_PROFILES_DIR)


@pytest.fixture()
def plans_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".organizer" / "plans"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def journal_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "journal.jsonl"


@pytest.fixture()
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / ".organizer" / "registry.json"


@pytest.fixture()
def quarantine_dir(tmp_path: Path) -> Path:
    return tmp_path / "_quarantine"


@pytest.fixture()
def fixture_dir(tmp_path: Path) -> Path:
    """Seed a small, realistic corpus and an empty destination folder.

    ``kickoff.txt`` and ``kickoff_dup.txt`` are byte-identical (same checksum);
    ``sorted/`` is an empty move target.
    """
    root = tmp_path / "corpus"
    incoming = root / "incoming"
    incoming.mkdir(parents=True)
    (root / "sorted").mkdir()

    kickoff = "Compte-rendu de la réunion de lancement du projet Telcontar.\n"
    (incoming / "kickoff.txt").write_text(kickoff, encoding="utf-8")
    (incoming / "kickoff_dup.txt").write_text(kickoff, encoding="utf-8")
    (incoming / "budget.md").write_text("# Budget\n\nNotes sur le budget prévisionnel.\n", encoding="utf-8")
    (incoming / "junk.txt").write_text("brouillon sans intérêt à mettre en quarantaine\n", encoding="utf-8")
    return root


def _record(
    reg_path: Path, prof: Profile, *, checksum: str, path: str, title: str, **kw: object
) -> dict:
    """Record a document with sensible IS/IT-profile defaults."""
    defaults: dict = dict(
        type="notes",
        summary="résumé",
        provenance="corpus de test",
        date=None,
        entities=None,
        attributes=None,
        status="active",
    )
    defaults.update(kw)
    return record_document(
        checksum=checksum,
        path=path,
        title=title,
        registry_path=reg_path,
        profile=prof,
        **defaults,  # type: ignore[arg-type]
    )


# ── Phase 1: read-only inspection ─────────────────────────────────────────────


class TestReadOnlyInspection:
    def test_list_dir_enumerates_tree(self, fixture_dir: Path) -> None:
        out = list_dir(str(fixture_dir / "incoming"))
        names = {e["name"] for e in out["entries"]}
        assert {"kickoff.txt", "kickoff_dup.txt", "budget.md", "junk.txt"} <= names
        assert all(e["type"] == "file" for e in out["entries"])

    def test_read_file_returns_content(self, fixture_dir: Path) -> None:
        text = read_file(str(fixture_dir / "incoming" / "budget.md"), 4000)
        assert "budget prévisionnel" in text

    def test_read_file_truncates_at_max_chars(self, tmp_path: Path) -> None:
        big = tmp_path / "big.txt"
        big.write_text("x" * 500, encoding="utf-8")
        out = read_file(str(big), 100)
        assert out.startswith("x" * 100)
        assert "content truncated" in out

    def test_extract_text_plain(self, fixture_dir: Path) -> None:
        text = extract_text(str(fixture_dir / "incoming" / "budget.md"), 4000)
        assert "Budget" in text

    def test_compute_checksum_is_deterministic(self, fixture_dir: Path) -> None:
        f = str(fixture_dir / "incoming" / "kickoff.txt")
        first = compute_checksum(f)["checksum"]
        second = compute_checksum(f)["checksum"]
        assert first == second
        assert len(first) == 64  # sha256 hex

    def test_identical_files_share_checksum(self, fixture_dir: Path) -> None:
        a = compute_checksum(str(fixture_dir / "incoming" / "kickoff.txt"))["checksum"]
        b = compute_checksum(str(fixture_dir / "incoming" / "kickoff_dup.txt"))["checksum"]
        assert a == b

    def test_different_files_differ(self, fixture_dir: Path) -> None:
        a = compute_checksum(str(fixture_dir / "incoming" / "kickoff.txt"))["checksum"]
        b = compute_checksum(str(fixture_dir / "incoming" / "budget.md"))["checksum"]
        assert a != b


# ── Phase 2: registry recording ───────────────────────────────────────────────


class TestRegistryRecording:
    def test_record_persists_and_queries(
        self, fixture_dir: Path, registry_path: Path, profile: Profile
    ) -> None:
        f = str(fixture_dir / "incoming" / "budget.md")
        cs = compute_checksum(f)["checksum"]
        _record(registry_path, profile, checksum=cs, path=f, title="Budget prévisionnel")

        got = get_document(cs, registry_path)
        assert got is not None
        assert got["title"] == "Budget prévisionnel"
        assert got["path"] == f
        assert got["status"] == "active"
        assert len(list_documents(registry_path)) == 1

    def test_upsert_by_checksum_preserves_first_seen(
        self, registry_path: Path, profile: Profile
    ) -> None:
        first = _record(registry_path, profile, checksum="c1", path="/p/a.txt", title="Titre 1")
        second = _record(registry_path, profile, checksum="c1", path="/p/a.txt", title="Titre 2")

        docs = list_documents(registry_path)
        assert len(docs) == 1  # upsert, not append
        assert docs[0]["title"] == "Titre 2"
        assert second["first_seen"] == first["first_seen"]

    def test_find_modified_groups_same_title_diff_checksum(
        self, registry_path: Path, profile: Profile
    ) -> None:
        _record(registry_path, profile, checksum="v1", path="/p/plan.md", title="Plan projet")
        _record(registry_path, profile, checksum="v2", path="/p/plan_v2.md", title="Plan projet")

        groups = find_modified_documents(registry_path)
        assert len(groups) == 1
        assert {r["checksum"] for r in groups[0]} == {"v1", "v2"}


# ── Phase 3: plan build, review, approve ──────────────────────────────────────


class TestPlanBuilding:
    def test_propose_review_approve_flow(
        self, fixture_dir: Path, plans_dir: Path
    ) -> None:
        src = str(fixture_dir / "incoming" / "kickoff.txt")
        dest = str(fixture_dir / "sorted")
        plan_id = create_plan(plans_dir)["plan_id"]

        out = propose_move(src, dest, plan_id, plans_dir)
        assert out["ops_count"] == 1

        review = review_plan(plan_id, plans_dir)
        assert review["is_valid"] is True
        assert review["duplicates"] == []

        approved = approve_plan(plan_id, plans_dir)
        assert approved["state"] == "approved"

    def test_review_plan_flags_duplicate_ops(
        self, fixture_dir: Path, plans_dir: Path
    ) -> None:
        src = str(fixture_dir / "incoming" / "kickoff.txt")
        dest = str(fixture_dir / "sorted")
        plan_id = create_plan(plans_dir)["plan_id"]
        propose_move(src, dest, plan_id, plans_dir)
        propose_move(src, dest, plan_id, plans_dir)

        review = review_plan(plan_id, plans_dir)
        assert review["is_valid"] is False
        assert len(review["duplicates"]) == 1
        assert review["duplicates"][0]["src"] == src


# ── Phase 4: execute + registry reconcile ─────────────────────────────────────


class TestExecuteAndReconcile:
    def test_execute_moves_file_and_journals(
        self, fixture_dir: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        from server.journal import all_entries

        src = fixture_dir / "incoming" / "kickoff.txt"
        dest = fixture_dir / "sorted"
        plan_id = create_plan(plans_dir)["plan_id"]
        propose_move(str(src), str(dest), plan_id, plans_dir)
        approve_plan(plan_id, plans_dir)

        result = execute_plan(plan_id, plans_dir, journal_path)

        assert result["state"] == "done"
        assert result["ops_completed"] == 1
        assert not src.exists()
        assert (dest / "kickoff.txt").exists()
        entries = all_entries(journal_path)
        assert len(entries) == 1
        assert entries[0]["op_type"] == "move"

    def test_execute_reconciles_registry_path(
        self,
        fixture_dir: Path,
        plans_dir: Path,
        journal_path: Path,
        registry_path: Path,
        profile: Profile,
    ) -> None:
        src = fixture_dir / "incoming" / "budget.md"
        dest = fixture_dir / "sorted"
        cs = compute_checksum(str(src))["checksum"]
        _record(registry_path, profile, checksum=cs, path=str(src), title="Budget")

        plan_id = create_plan(plans_dir)["plan_id"]
        propose_move(str(src), str(dest), plan_id, plans_dir)
        approve_plan(plan_id, plans_dir)
        execute_plan(plan_id, plans_dir, journal_path, registry_path=registry_path)

        rec = get_document(cs, registry_path)
        assert rec is not None
        # Checksum is the identity; path tracks the move.
        assert rec["checksum"] == cs
        assert rec["path"] == str(dest / "budget.md")

    def test_checksum_stable_across_rename(
        self,
        fixture_dir: Path,
        plans_dir: Path,
        journal_path: Path,
        registry_path: Path,
        profile: Profile,
    ) -> None:
        src = fixture_dir / "incoming" / "budget.md"
        cs = compute_checksum(str(src))["checksum"]
        _record(registry_path, profile, checksum=cs, path=str(src), title="Budget")

        plan_id = create_plan(plans_dir)["plan_id"]
        propose_rename(str(src), "budget_2026.md", plan_id, plans_dir)
        approve_plan(plan_id, plans_dir)
        execute_plan(plan_id, plans_dir, journal_path, registry_path=registry_path)

        rec = get_document(cs, registry_path)
        assert rec is not None
        assert rec["checksum"] == cs
        assert rec["path"] == str(src.parent / "budget_2026.md")


# ── Phase 5: undo ─────────────────────────────────────────────────────────────


class TestUndo:
    def test_undo_reverts_move(
        self, fixture_dir: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        from server.journal import all_entries

        src = fixture_dir / "incoming" / "kickoff.txt"
        dest = fixture_dir / "sorted"
        plan_id = create_plan(plans_dir)["plan_id"]
        propose_move(str(src), str(dest), plan_id, plans_dir)
        approve_plan(plan_id, plans_dir)
        execute_plan(plan_id, plans_dir, journal_path)
        assert (dest / "kickoff.txt").exists()

        undo = undo_last(journal_path, plans_dir)

        assert undo["undone"] is not None
        assert src.exists()
        assert not (dest / "kickoff.txt").exists()
        assert all_entries(journal_path) == []

    def test_undo_reverts_quarantine(
        self, fixture_dir: Path, plans_dir: Path, journal_path: Path, quarantine_dir: Path
    ) -> None:
        src = fixture_dir / "incoming" / "junk.txt"
        plan_id = create_plan(plans_dir)["plan_id"]
        out = propose_quarantine(str(src), plan_id, plans_dir, quarantine_dir)
        approve_plan(plan_id, plans_dir)
        execute_plan(plan_id, plans_dir, journal_path)
        quarantined = Path(out["dst"])
        assert quarantined.exists()
        assert not src.exists()

        undo_last(journal_path, plans_dir)

        assert src.exists()
        assert not quarantined.exists()

    def test_undo_empty_journal_returns_error(
        self, journal_path: Path, plans_dir: Path
    ) -> None:
        out = undo_last(journal_path, plans_dir)
        assert out["undone"] is None
        assert "No operations" in out["error"]


# ── Phase 6: output artifacts ─────────────────────────────────────────────────


class TestOutputs:
    def test_write_index_creates_artifacts(
        self, fixture_dir: Path, plans_dir: Path, journal_path: Path
    ) -> None:
        # Run one op so the journal changelog is non-empty.
        src = fixture_dir / "incoming" / "kickoff.txt"
        dest = fixture_dir / "sorted"
        plan_id = create_plan(plans_dir)["plan_id"]
        propose_move(str(src), str(dest), plan_id, plans_dir)
        approve_plan(plan_id, plans_dir)
        execute_plan(plan_id, plans_dir, journal_path)

        out = write_index(str(fixture_dir), journal_path)

        assert (fixture_dir / "INDEX.md").exists()
        assert (fixture_dir / "manifest.json").exists()
        manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["journal_summary"]["total_ops"] == 1
        file_names = {Path(f["path"]).name for f in manifest["files"]}
        assert "budget.md" in file_names
        assert out["index"] == str(fixture_dir / "INDEX.md")

    def test_write_summary_verbatim(self, fixture_dir: Path) -> None:
        prose = "# Synthèse\n\nLe corpus contient 4 documents.\n"
        write_summary(str(fixture_dir), prose)
        assert (fixture_dir / "SUMMARY.md").read_text(encoding="utf-8") == prose


# ── Phase 7: full lifecycle ───────────────────────────────────────────────────


class TestFullLifecycle:
    def test_end_to_end_organize(
        self,
        fixture_dir: Path,
        plans_dir: Path,
        journal_path: Path,
        registry_path: Path,
        quarantine_dir: Path,
        profile: Profile,
    ) -> None:
        from server.journal import all_entries

        incoming = fixture_dir / "incoming"
        sorted_dir = fixture_dir / "sorted"

        # 1. Inspect the tree.
        listing = list_dir(str(incoming))
        doc_paths = [
            e["path"] for e in listing["entries"] if e["name"] in {"kickoff.txt", "budget.md"}
        ]
        assert len(doc_paths) == 2

        # 2. Analyse + record each document (checksum = identity).
        checksums: dict[str, str] = {}
        for p in doc_paths:
            text = read_file(p, 4000)
            assert text  # content flows into analysis
            cs = compute_checksum(p)["checksum"]
            checksums[Path(p).name] = cs
            _record(registry_path, profile, checksum=cs, path=p, title=Path(p).stem)
        assert len(list_documents(registry_path)) == 2

        # 3. Build a plan: move a real doc, quarantine the junk.
        plan_id = create_plan(plans_dir)["plan_id"]
        propose_move(str(incoming / "budget.md"), str(sorted_dir), plan_id, plans_dir)
        q = propose_quarantine(str(incoming / "junk.txt"), plan_id, plans_dir, quarantine_dir)

        # 4. Review → approve → execute (with registry reconcile).
        assert review_plan(plan_id, plans_dir)["is_valid"] is True
        approve_plan(plan_id, plans_dir)
        result = execute_plan(plan_id, plans_dir, journal_path, registry_path=registry_path)
        assert result["state"] == "done"
        assert result["ops_completed"] == 2

        # 5. Filesystem reflects the plan.
        assert (sorted_dir / "budget.md").exists()
        assert not (incoming / "budget.md").exists()
        assert Path(q["dst"]).exists()
        assert not (incoming / "junk.txt").exists()

        # 6. Registry reconciled: checksums stable, paths + status tracked.
        moved = get_document(checksums["budget.md"], registry_path)
        assert moved is not None
        assert moved["path"] == str(sorted_dir / "budget.md")

        # 7. Emit outputs.
        write_index(str(fixture_dir), journal_path)
        write_summary(str(fixture_dir), "# Synthèse\n\nOrganisation terminée.\n")
        assert (fixture_dir / "INDEX.md").exists()
        assert (fixture_dir / "manifest.json").exists()
        assert (fixture_dir / "SUMMARY.md").exists()

        # 8. Undo the last op (quarantine) — reversible to the byte.
        assert len(all_entries(journal_path)) == 2
        undo_last(journal_path, plans_dir)
        assert (incoming / "junk.txt").exists()
        assert not Path(q["dst"]).exists()
        assert len(all_entries(journal_path)) == 1
