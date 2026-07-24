# pylint: disable=import-error,protected-access

from types import SimpleNamespace

from gui import app as gui_app
from gui.app import SongOrganizerApp
from renamer.review_models import (
    FileSnapshot,
    DuplicateFinding,
    RenameProposal,
    ReviewPlan,
    TagProposal,
)


def test_version_qualifier_conflict_requires_manual_review():
    proposal = SimpleNamespace(
        warnings=(
            "Version qualifier conflicts with AcoustID metadata; "
            "review the proposed filename.",
        )
    )

    assert gui_app._requires_review(proposal)


class _FakeTree:
    def __init__(self, rows, selected=()):
        self.rows = rows
        self.selected = tuple(selected)

    def get_children(self, _parent=""):
        return tuple(self.rows)

    def delete(self, *rows):
        for row in rows:
            self.rows.pop(row, None)

    def insert(self, _parent, _index, values):
        row = f"row-{len(self.rows)}"
        self.rows[row] = tuple(values)
        return row

    def selection(self):
        return self.selected

    def selection_set(self, row):
        self.selected = (row,)

    def identify_column(self, _x):
        return "#1"

    def identify_region(self, _x, _y):
        return "cell"

    def identify_row(self, y):
        return y

    def item(self, row, option=None, values=None):
        if values is not None:
            self.rows[row] = tuple(values)
        if option == "values":
            return self.rows[row]
        return {"values": self.rows[row]}


def test_select_all_selects_only_actionable_proposals(tmp_path):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old"},
        after={"artist": "New"},
        confidence="high",
        reason="test",
    )
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    app.plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[rename],
        tag_proposals=[tag],
    )
    app.selected_ids = set()
    app._row_ids = {
        ("renames", "shared-row"): rename.id,
        ("tags", "shared-row"): tag.id,
        ("errors", "shared-row"): "issue-1",
    }
    app.trees = {
        "renames": _FakeTree({"shared-row": ("☐",)}),
        "tags": _FakeTree({"shared-row": ("☐",)}),
    }
    app.status_var = _FakeStatus()

    app._select_all()

    assert app.selected_ids == {rename.id, tag.id}
    assert app.trees["renames"].rows["shared-row"][0] == "☑"
    assert app.trees["tags"].rows["shared-row"][0] == "☑"


def test_checkbox_selects_the_entire_decision_group(tmp_path):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "new.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "new.mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"artist": "Old"},
        after={"artist": "New"},
        confidence="high",
        reason="test",
    )
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    app.plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[rename],
        tag_proposals=[tag],
    )
    app.selected_ids = set()
    app._row_ids = {
        ("renames", "rename-row"): rename.id,
        ("tags", "tag-row"): tag.id,
    }
    app.trees = {
        "renames": _FakeTree({"rename-row": ("☐",)}),
        "tags": _FakeTree({"tag-row": ("☐",)}),
    }
    app.status_var = _FakeStatus()

    app._handle_tree_click("renames", SimpleNamespace(x=5, y="rename-row"))

    assert app.selected_ids == {rename.id, tag.id}
    assert app.trees["renames"].rows["rename-row"][0] == "☑"
    assert app.trees["tags"].rows["tag-row"][0] == "☑"


def test_select_all_ready_skips_destination_collisions(tmp_path):
    source = tmp_path / "old.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    safe = RenameProposal(
        id="rename-safe",
        decision_group_id="safe",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "safe.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "safe.mp3"},
        confidence="high",
        reason="test",
    )
    review = RenameProposal(
        id="rename-review",
        decision_group_id="review",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "collision.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "collision.mp3"},
        confidence="high",
        reason="test",
        warnings=("Destination already exists: collision.mp3",),
    )
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    app.plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[safe, review],
    )
    app.selected_ids = set()
    app._row_ids = {
        ("renames", "safe-row"): safe.id,
        ("renames", "review-row"): review.id,
    }
    app.trees = {
        "renames": _FakeTree(
            {"safe-row": ("☐",), "review-row": ("☐",)}
        ),
        "tags": _FakeTree({}),
    }
    app.status_var = _FakeStatus()

    app._select_all()

    assert app.selected_ids == {safe.id}
    assert app.trees["renames"].rows["safe-row"][0] == "☑"
    assert app.trees["renames"].rows["review-row"][0] == "☐"


def test_checkbox_toggles_all_shift_selected_rows():
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    app.selected_ids = set()
    app._row_ids = {
        ("renames", "row-1"): "rename-1",
        ("renames", "row-2"): "rename-2",
    }
    app.trees = {
        "renames": _FakeTree(
            {
                "row-1": ("☐",),
                "row-2": ("☐",),
            },
            selected=("row-1", "row-2"),
        ),
        "tags": _FakeTree({}),
    }

    result = app._handle_tree_click(
        "renames",
        SimpleNamespace(x=5, y="row-2"),
    )

    assert result == "break"
    assert app.selected_ids == {"rename-1", "rename-2"}
    assert app.trees["renames"].rows["row-1"][0] == "☑"
    assert app.trees["renames"].rows["row-2"][0] == "☑"


def test_right_click_file_opens_context_menu_for_exact_path(monkeypatch):
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    app.root = object()
    app._row_paths = {
        ("renames", "row-1"): r"F:\Music\Hip-Hop\Artist - Song.mp3",
    }
    app.trees = {
        "renames": _FakeTree({"row-1": ("☐",)}, selected=()),
    }
    opened = []
    app._open_in_file_explorer = opened.append
    menus = []

    class _FakeMenu:
        def __init__(self, *_args, **_kwargs):
            self.command = None
            menus.append(self)

        def add_command(self, *, command, **_kwargs):
            self.command = command

        def tk_popup(self, x, y):
            assert (x, y) == (40, 50)
            self.command()

    monkeypatch.setattr(gui_app.tk, "Menu", _FakeMenu)

    result = app._handle_tree_context_menu(
        "renames",
        SimpleNamespace(x_root=40, y_root=50, y="row-1"),
    )

    assert result == "break"
    assert app.trees["renames"].selection() == ("row-1",)
    assert opened == [r"F:\Music\Hip-Hop\Artist - Song.mp3"]
    assert len(menus) == 1


def test_open_file_explorer_passes_target_as_separate_argument(tmp_path, monkeypatch):
    path = tmp_path / "Artist - Song.mp3"
    path.write_bytes(b"audio")
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    calls = []

    def fake_popen(command, **options):
        calls.append((command, options))

    monkeypatch.setattr(gui_app.subprocess, "Popen", fake_popen)

    app._open_in_file_explorer(str(path))

    assert calls
    assert calls[0][0] == ["explorer.exe", "/select,", str(path)]


def test_tag_display_uses_compact_artist_title_values():
    assert gui_app._tag_display({"artist": "Artist", "title": "Song"}) == "Artist / Song"
    assert gui_app._tag_display({"title": "Song"}) == "Song"


def test_duplicate_finding_renders_each_path():
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    rendered = []
    app._insert_row = lambda *values: rendered.append(values)
    finding = DuplicateFinding(
        id="duplicate-1",
        paths=("first.mp3", "second.mp3"),
        classification="unsafe",
        recommendation="Keep both unless you confirm they are equivalent.",
        evidence={},
        confidence="low",
    )

    app._insert_duplicate_finding(finding)

    assert rendered == [
        (
            "duplicates",
            "duplicate-1:1",
            "unsafe (1/2)",
            "first.mp3",
            "Keep both unless you confirm they are equivalent.",
            "low",
        ),
        (
            "duplicates",
            "duplicate-1:2",
            "unsafe (2/2)",
            "second.mp3",
            "Keep both unless you confirm they are equivalent.",
            "low",
        ),
    ]


def test_select_recommended_allows_expected_paired_repairs(tmp_path):
    source = tmp_path / "Artist - Song (feat. Guest).mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    rename = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "Artist - Song (feat. Guest).mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "Artist - Song (feat. Guest).mp3"},
        confidence="high",
        reason="test",
    )
    tag = TagProposal(
        id="tag-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        path=str(source),
        before={"title": "Song"},
        after={"title": "Song (feat. Guest)"},
        confidence="high",
        reason="test",
    )
    low_confidence_rename = RenameProposal(
        id="rename-2",
        decision_group_id="group-2",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "other.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "other.mp3"},
        confidence="medium",
        reason="test",
    )
    unsafe_rename = RenameProposal(
        id="rename-3",
        decision_group_id="group-3",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "unsafe.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "unsafe.mp3"},
        confidence="high",
        reason="test",
        warnings=("Destination collides with another proposal.",),
    )
    plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[rename, low_confidence_rename, unsafe_rename],
        tag_proposals=[tag],
    )

    assert gui_app._recommended_ids(plan) == {rename.id, tag.id}


def test_edit_selected_filename_updates_plan_and_selection(tmp_path, monkeypatch):
    source = tmp_path / "Artist - Wrong Spelling.mp3"
    source.write_bytes(b"audio")
    snapshot = FileSnapshot.capture(str(source))
    proposal = RenameProposal(
        id="rename-1",
        decision_group_id="group-1",
        snapshot=snapshot,
        old_path=str(source),
        new_path=str(tmp_path / "Artist - Wrong Spelling.mp3"),
        current_values={"filename": source.name},
        proposed_values={"filename": "Artist - Wrong Spelling.mp3"},
        confidence="high",
        reason="test",
    )
    app = SongOrganizerApp.__new__(SongOrganizerApp)
    app.plan = ReviewPlan.create(
        str(tmp_path),
        False,
        rename_proposals=[proposal],
    )
    app.selected_ids = {proposal.id}
    app._row_ids = {("renames", "rename-row"): proposal.id}
    app._row_paths = {}
    app.trees = {
        "renames": _FakeTree(
            {"rename-row": ("☑", "Rename", str(source), "old summary", "high")},
            selected=("rename-row",),
        ),
        "tags": _FakeTree({}),
        "duplicates": _FakeTree({}),
        "errors": _FakeTree({}),
    }
    app.root = None
    app.status_var = _FakeStatus()
    monkeypatch.setattr(
        gui_app,
        "_ask_filename",
        lambda *_args, **_kwargs: "Artist - Correct Spelling.mp3",
    )

    app._edit_selected_filename()

    updated = app.plan.rename_proposals[0]
    assert updated.proposed_values["filename"] == "Artist - Correct Spelling.mp3"
    assert updated.new_path.endswith("Artist - Correct Spelling.mp3")
    assert updated.id in app.selected_ids
    assert proposal.id not in app.selected_ids
    assert app.plan.validate_digest()
    assert any(
        values[3] == "Artist - Correct Spelling.mp3"
        for values in app.trees["renames"].rows.values()
    )


class _FakeStatus:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value
