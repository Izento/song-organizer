"""Review-first tkinter application."""

from __future__ import annotations

import queue
import threading
from dataclasses import replace
from datetime import datetime, timezone
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from renamer.apply import (
    apply_review_plan,
    batch_history,
    batches_requiring_recovery,
    latest_undoable_batch,
    undo_batch,
)
from renamer.review_api import analyze_folder
from renamer.review_models import (
    ReviewPlan,
    canonical_path,
    path_key,
    proposal_id,
)
from renamer.runtime import (
    ensure_app_dirs,
    resolve_acoustid_key,
    resolve_fpcalc,
    resource_path,
)


GUI_TITLE = "Ballad"
_WINDOWS_UNSAFE_FILENAME_CHARS = set('<>:"/\\|?*')
_FIXED_TREE_COLUMNS = {"selected", "action", "confidence"}
_TREE_STYLE = "Ballad.Treeview"


def _format_local_timestamp(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        local = timestamp.astimezone()
        return local.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    except (TypeError, ValueError):
        return value


def _tag_display(values: dict[str, str]) -> str:
    return " / ".join(
        value for value in (values.get("artist", ""), values.get("title", "")) if value
    )


class _FilenameDialog(simpledialog.Dialog):
    def __init__(self, parent, initialvalue: str):
        self.initialvalue = initialvalue
        self.result: str | None = None
        super().__init__(parent, title="Correct proposed filename")

    def body(self, master):
        self.minsize(680, 120)
        self.resizable(True, False)
        ttk.Label(master, text="Filename to use:").grid(
            row=0,
            column=0,
            sticky=tk.W,
            padx=(0, 8),
            pady=(0, 8),
        )
        self.entry = ttk.Entry(master, width=80)
        self.entry.insert(0, self.initialvalue)
        self.entry.grid(row=1, column=0, sticky=tk.EW)
        master.columnconfigure(0, weight=1)
        return self.entry

    def apply(self):
        self.result = self.entry.get()


def _ask_filename(parent, initialvalue: str) -> str | None:
    return _FilenameDialog(parent, initialvalue).result


def _is_high_confidence_action(item) -> bool:
    return item.confidence == "high" and not item.warnings


def _recommended_ids(plan: ReviewPlan) -> set[str]:
    """Return high-confidence actions without unresolved safety warnings."""
    return {
        item.id
        for item in (*plan.rename_proposals, *plan.tag_proposals)
        if _is_high_confidence_action(item)
    }


def _filename_validation_error(filename: str, old_path: str) -> str | None:
    if not filename:
        return "Enter a filename."
    if filename in {".", ".."}:
        return "That is not a valid filename."
    if any(character in _WINDOWS_UNSAFE_FILENAME_CHARS for character in filename):
        return "The filename contains characters Windows does not allow."
    if filename.endswith((" ", ".")):
        return "A Windows filename cannot end with a space or period."
    if Path(filename).suffix.casefold() != Path(old_path).suffix.casefold():
        return "Keep the original file extension."
    return None


class SongOrganizerApp:
    def __init__(self, root: tk.Tk | None = None):
        self.root = root or tk.Tk()
        self.root.title(GUI_TITLE)
        self._set_window_icon()
        self.root.geometry("1180x720")
        self.root.minsize(900, 560)
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.plan: ReviewPlan | None = None
        self.selected_ids: set[str] = set()
        self._row_ids: dict[tuple[str, str], str] = {}

        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=True)
        self.fingerprint_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Choose a folder to begin.")
        self.acoustid_key = resolve_acoustid_key()
        fpcalc_state = (
            "available" if resolve_fpcalc() else "not installed (optional)"
        )
        online_state = "enabled" if self.acoustid_key else "skipped"
        self.capability_var = tk.StringVar(
            value=(
                f"Fingerprint helper: {fpcalc_state} | "
                f"Online identification: {online_state}"
            )
        )
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll_events)
        self.root.after(250, self._show_startup_recovery)

    def _set_window_icon(self) -> None:
        icon_path = resource_path("ballad.ico")
        if not icon_path.is_file():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except tk.TclError:
            return

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Music folder:").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.folder_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 5)
        )
        ttk.Button(top, text="Browse…", command=self._browse).pack(side=tk.LEFT)
        ttk.Checkbutton(
            top,
            text="Include subfolders",
            variable=self.recursive_var,
        ).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(
            top,
            text="Use fingerprints for duplicate checks",
            variable=self.fingerprint_var,
        ).pack(side=tk.LEFT, padx=(0, 10))
        self.analyze_button = ttk.Button(
            top, text="Analyze", command=self._analyze
        )
        self.analyze_button.pack(side=tk.LEFT)
        ttk.Label(top, textvariable=self.capability_var).pack(
            side=tk.LEFT, padx=(10, 0)
        )

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
        self.trees: dict[str, ttk.Treeview] = {}
        for key, title in (
            ("renames", "Proposed renames"),
            ("tags", "Tag disagreements"),
            ("duplicates", "Duplicate findings (read-only)"),
            ("errors", "Skipped / errors"),
        ):
            frame = ttk.Frame(self.notebook, padding=6)
            self.notebook.add(frame, text=title)
            tree = self._make_tree(frame, key)
            self.trees[key] = tree

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Button(
            bottom,
            text="Select recommended",
            command=self._select_recommended,
        ).pack(side=tk.LEFT)
        ttk.Button(
            bottom,
            text="Select all",
            command=self._select_all,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self.edit_button = ttk.Button(
            bottom,
            text="Edit filename",
            command=self._edit_selected_filename,
        )
        self.edit_button.pack(side=tk.LEFT, padx=(8, 0))
        self.apply_button = ttk.Button(
            bottom, text="Apply selected", command=self._apply
        )
        self.apply_button.pack(side=tk.LEFT, padx=8)
        self.cancel_button = ttk.Button(
            bottom, text="Cancel", command=self._cancel
        )
        self.cancel_button.pack(side=tk.LEFT)
        self.history_button = ttk.Button(
            bottom, text="History", command=self._show_history
        )
        self.history_button.pack(side=tk.LEFT)
        self.undo_button = ttk.Button(
            bottom, text="Undo latest", command=self._undo_latest
        )
        self.undo_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(bottom, textvariable=self.status_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )

    def _make_tree(self, parent: ttk.Frame, key: str) -> ttk.Treeview:
        if key == "renames":
            columns = ("selected", "action", "current", "proposed", "confidence")
            headings = {
                "selected": "",
                "action": "Action",
                "current": "Current filename",
                "proposed": "Proposed filename",
                "confidence": "Confidence",
            }
            widths = {
                "selected": 26,
                "action": 82,
                "current": 440,
                "proposed": 440,
                "confidence": 72,
            }
        elif key == "tags":
            columns = (
                "selected",
                "action",
                "file",
                "current",
                "proposed",
                "confidence",
            )
            headings = {
                "selected": "",
                "action": "Action",
                "file": "File",
                "current": "Current tags",
                "proposed": "Proposed tags",
                "confidence": "Confidence",
            }
            widths = {
                "selected": 26,
                "action": 82,
                "file": 170,
                "current": 350,
                "proposed": 350,
                "confidence": 72,
            }
        else:
            columns = ("selected", "action", "file", "details", "confidence")
            headings = {
                "selected": "",
                "action": "Action",
                "file": "File",
                "details": "Details",
                "confidence": "Confidence",
            }
            widths = {
                "selected": 26,
                "action": 140,
                "file": 350,
                "details": 420,
                "confidence": 72,
            }
        selectmode = "extended" if key in {"renames", "tags"} else "browse"
        style = ttk.Style(parent)
        style.configure(_TREE_STYLE, rowheight=24)
        tree = ttk.Treeview(
            parent,
            columns=columns,
            show="headings",
            selectmode=selectmode,
            style=_TREE_STYLE,
        )
        for column in columns:
            tree.heading(column, text=headings[column])
            fixed = column in _FIXED_TREE_COLUMNS
            tree.column(
                column,
                width=widths[column],
                minwidth=widths[column] if fixed else 180,
                stretch=not fixed,
                anchor=tk.CENTER if column in {"selected", "confidence"} else tk.W,
            )
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.bind(
            "<Button-1>",
            lambda event, name=key: self._handle_tree_click(name, event),
        )
        return tree

    def _browse(self) -> None:
        selected = filedialog.askdirectory(title="Choose music folder")
        if selected:
            self.folder_var.set(selected)

    def _set_busy(self, busy: bool) -> None:
        state = tk.DISABLED if busy else tk.NORMAL
        self.analyze_button.configure(state=state)
        self.apply_button.configure(state=state)
        self.edit_button.configure(state=state)
        self.cancel_button.configure(state=tk.NORMAL if busy else tk.DISABLED)
        self.history_button.configure(state=state)
        self.undo_button.configure(state=state)
        if busy:
            self.status_var.set("Working…")

    def _analyze(self) -> None:
        folder = self.folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Folder required", "Choose an existing music folder.")
            return
        self.plan = None
        self.selected_ids.clear()
        self._clear_trees()
        self.cancel_event = threading.Event()
        self._set_busy(True)
        self.status_var.set("Analyzing read-only…")
        recursive = self.recursive_var.get()
        fingerprint = self.fingerprint_var.get()
        acoustid_key = self.acoustid_key

        def worker() -> None:
            try:
                plan = analyze_folder(
                    folder,
                    recursive=recursive,
                    lookup=bool(acoustid_key),
                    acoustid_key=acoustid_key,
                    fingerprint=fingerprint,
                    progress=lambda stage, current, total, path: self.events.put(
                        ("progress", stage, current, total, path)
                    ),
                    cancel_event=self.cancel_event,
                )
                self.events.put(("analysis-complete", plan))
            except Exception as exc:
                self.events.put(("failed", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _apply(self) -> None:
        if self.plan is None:
            messagebox.showinfo("Nothing to apply", "Analyze a folder first.")
            return
        if not self.selected_ids:
            messagebox.showinfo("Nothing selected", "Select at least one proposal.")
            return
        if batches_requiring_recovery():
            messagebox.showwarning(
                "Recovery required",
                "Undo the latest incomplete batch from the History window before "
                "applying new changes. This restores actions that completed before "
                "the previous apply stopped.",
            )
            return
        if not self.plan.validate_digest():
            messagebox.showerror(
                "Plan invalid",
                "The reviewed plan no longer matches its digest. Analyze again.",
            )
            return
        if not messagebox.askyesno(
            "Confirm selected changes",
            f"Apply {len(self.selected_ids)} selected rename/tag actions?\n\n"
            "The reviewed plan will be revalidated before any file is changed.",
        ):
            return
        selected = tuple(self.selected_ids)
        plan = self.plan
        self.cancel_event = threading.Event()
        self._set_busy(True)

        def worker() -> None:
            try:
                results = apply_review_plan(
                    plan,
                    selected,
                    cancel_event=self.cancel_event,
                    progress=lambda stage, current, total, result: self.events.put(
                        ("progress", stage, current, total, result.path if result else "")
                    ),
                )
                self.events.put(("apply-complete", results))
            except Exception as exc:
                self.events.put(("failed", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.cancel_event.set()
            self.status_var.set("Cancellation requested…")

    def _undo_latest(self) -> None:
        batch = latest_undoable_batch()
        if batch is None:
            messagebox.showinfo("Nothing to undo", "No recoverable batch is available.")
            return
        if not messagebox.askyesno(
            "Undo latest batch",
            f"Restore the latest batch for {batch.get('root', 'the selected folder')}?",
        ):
            return
        self.cancel_event = threading.Event()
        self._set_busy(True)

        def worker() -> None:
            try:
                results = undo_batch(batch["batch_id"])
                self.events.put(("undo-complete", results))
            except Exception as exc:
                self.events.put(("failed", str(exc)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _show_history(self) -> None:
        batches = batch_history()
        window = tk.Toplevel(self.root)
        window.title(f"{GUI_TITLE} history")
        window.geometry("760x360")
        listbox = tk.Listbox(window)
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        for batch in batches:
            listbox.insert(
                tk.END,
                f"{batch.get('status', 'unknown'):18} "
                f"{_format_local_timestamp(batch.get('created_at', ''))}  "
                f"{batch.get('root', '')}",
            )
        ttk.Label(
            window,
            text="Undo latest restores completed actions from the newest "
            "completed or interrupted batch. Restore remains guarded by "
            "the batch journal.",
        ).pack(fill=tk.X, padx=10, pady=(0, 10))

    def _show_startup_recovery(self) -> None:
        pending = batches_requiring_recovery()
        if pending:
            self.status_var.set(
                f"Recovery required: {len(pending)} incomplete batch(es)."
            )
            messagebox.showwarning(
                "Recovery required",
                f"{len(pending)} previous batch(es) did not finish cleanly. "
                "Use Undo latest to restore completed actions, then analyze "
                "the folder again before applying new changes.",
            )

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "progress":
            _, stage, current, total, path = event
            self.status_var.set(f"{stage}: {current}/{total}  {path}")
        elif kind == "analysis-complete":
            self.plan = event[1]
            self._set_busy(False)
            self._populate_plan(self.plan)
            self.status_var.set(
                f"Analysis complete: {len(self.plan.rename_proposals)} renames, "
                f"{len(self.plan.tag_proposals)} tag repairs, "
                f"{len(self.plan.duplicate_findings)} duplicate findings."
            )
        elif kind == "undo-complete":
            results = event[1]
            self._set_busy(False)
            succeeded = sum(result.status == "succeeded" for result in results)
            failed = sum(result.status == "failed" for result in results)
            self.status_var.set(
                f"Undo complete: {succeeded} restored, {failed} failed."
            )
            if failed:
                messagebox.showwarning(
                    "Undo needs attention",
                    f"{failed} action(s) could not be restored. "
                    "Review the batch journal.",
                )
        elif kind == "apply-complete":
            results = event[1]
            self._set_busy(False)
            succeeded = sum(result.status == "succeeded" for result in results)
            blocked = sum(result.status == "blocked" for result in results)
            failed = sum(result.status in {"failed", "stale"} for result in results)
            for result in results:
                if result.status in {"failed", "stale", "blocked"}:
                    self._insert_row(
                        "errors",
                        f"apply-{result.proposal_id}",
                        result.status,
                        result.path,
                        result.message,
                        "error",
                    )
            self.status_var.set(
                f"Apply complete: {succeeded} succeeded, "
                f"{blocked} blocked, {failed} failed."
            )
            if failed or blocked:
                messagebox.showwarning(
                    "Apply finished with issues",
                    f"{succeeded} actions succeeded, {blocked} blocked, "
                    f"and {failed} failed. "
                    + (
                        "Blocked actions were skipped; the successful actions "
                        "do not need to be undone. "
                        if blocked and not failed
                        else "Use Undo latest to restore successful actions "
                        "when a mutation failed. "
                    )
                    + "Open the error tab for details.",
                )
        elif kind == "failed":
            self._set_busy(False)
            self.status_var.set("Operation failed.")
            messagebox.showerror("Operation failed", event[1])

    def _clear_trees(self) -> None:
        for tree in self.trees.values():
            tree.delete(*tree.get_children())
        self._row_ids.clear()

    def _populate_plan(self, plan: ReviewPlan) -> None:
        self._clear_trees()
        for item in plan.rename_proposals:
            self._insert_change_row(
                "renames",
                item.id,
                "Rename",
                item.old_path,
                item.current_values.get("filename", ""),
                item.proposed_values.get("filename", ""),
                item.confidence,
            )
        for item in plan.tag_proposals:
            self._insert_change_row(
                "tags",
                item.id,
                "Tag repair",
                item.path,
                _tag_display(item.before),
                _tag_display(item.after),
                item.confidence,
            )
        for item in plan.duplicate_findings:
            self._insert_duplicate_finding(item)
        for issue in plan.issues:
            self._insert_row(
                "errors",
                f"issue-{len(self._row_ids)}",
                issue.get("category", "error"),
                issue.get("path", ""),
                issue.get("message", ""),
                "warning",
            )

    def _insert_duplicate_finding(self, item) -> None:
        paths = item.paths or ("",)
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            self._insert_row(
                "duplicates",
                f"{item.id}:{index}",
                f"{item.classification} ({index}/{total})",
                path,
                item.recommendation,
                item.confidence,
            )

    def _insert_row(
        self,
        tree_name: str,
        item_id: str,
        action: str,
        path: str,
        summary: str,
        confidence: str,
    ) -> None:
        tree = self.trees[tree_name]
        display_file = Path(path).name if path else ""
        row = tree.insert(
            "",
            tk.END,
            values=("☐", action, display_file, summary, confidence),
        )
        self._row_ids[(tree_name, row)] = item_id

    def _insert_change_row(
        self,
        tree_name: str,
        item_id: str,
        action: str,
        path: str,
        current: str,
        proposed: str,
        confidence: str,
    ) -> None:
        tree = self.trees[tree_name]
        values = ["☐", action]
        if tree_name == "tags":
            values.append(Path(path).name if path else "")
        values.extend((current, proposed, confidence))
        row = tree.insert("", tk.END, values=values)
        self._row_ids[(tree_name, row)] = item_id

    def _handle_tree_click(self, tree_name: str, event):
        tree = self.trees[tree_name]
        if tree.identify_region(event.x, event.y) == "separator":
            column = tree.identify_column(event.x)
            index = int(column[1:]) - 1 if column.startswith("#") else -1
            adjacent = {column}
            if index > 0:
                adjacent.add(f"#{index}")
            tree_columns = tree["columns"]
            if any(
                tree_columns[int(name[1:]) - 1] in _FIXED_TREE_COLUMNS
                for name in adjacent
                if name.startswith("#")
                and int(name[1:]) <= len(tree_columns)
            ):
                return "break"
        if tree_name not in {"renames", "tags"}:
            return None
        if tree.identify_column(event.x) != "#1":
            return None
        row = tree.identify_row(event.y)
        if not row:
            return "break"

        rows = list(tree.selection())
        if row not in rows:
            tree.selection_set(row)
            rows = [row]
        item_ids = {
            item_id
            for selected_row in rows
            if (item_id := self._row_ids.get((tree_name, selected_row)))
        }
        clicked_id = self._row_ids.get((tree_name, row))
        if not clicked_id or not item_ids:
            return "break"
        if clicked_id in self.selected_ids:
            self._set_selected_ids(self.selected_ids - item_ids)
        else:
            self._set_selected_ids(self.selected_ids | item_ids)
        return "break"

    def _select_recommended(self) -> None:
        if self.plan is None:
            return
        recommended = _recommended_ids(self.plan)
        self._set_selected_ids(recommended)
        self.status_var.set(f"Selected {len(recommended)} recommended actions.")

    def _select_all(self) -> None:
        if self.plan is None:
            return
        selected = {
            item.id
            for item in (*self.plan.rename_proposals, *self.plan.tag_proposals)
        }
        self._set_selected_ids(selected)
        self.status_var.set(f"Selected all {len(selected)} actions.")

    def _edit_selected_filename(self) -> None:
        if self.plan is None:
            messagebox.showinfo("Nothing to edit", "Analyze a folder first.")
            return
        tree = self.trees["renames"]
        rows = tree.selection()
        if len(rows) != 1:
            messagebox.showinfo(
                "Choose a rename",
                "Click one row in Proposed renames, then choose Edit filename.",
            )
            return
        row = rows[0]
        item_id = self._row_ids.get(("renames", row))
        proposal = next(
            (
                item
                for item in self.plan.rename_proposals
                if item.id == item_id
            ),
            None,
        )
        if proposal is None:
            messagebox.showerror("Rename unavailable", "That rename is no longer available.")
            return

        filename = _ask_filename(
            self.root,
            proposal.proposed_values.get("filename", ""),
        )
        if filename is None:
            return
        filename = filename.strip()
        error = _filename_validation_error(filename, proposal.old_path)
        if error:
            messagebox.showerror("Invalid filename", error)
            return
        new_path = canonical_path(str(Path(proposal.old_path).with_name(filename)))
        if path_key(new_path) == path_key(proposal.old_path):
            messagebox.showerror(
                "No change",
                "The corrected filename must differ from the current filename.",
            )
            return
        if any(
            item.id != proposal.id and path_key(item.new_path) == path_key(new_path)
            for item in self.plan.rename_proposals
        ):
            messagebox.showerror(
                "Filename already proposed",
                "Another reviewed rename already uses that filename.",
            )
            return

        new_id = proposal_id("rename", proposal.old_path, new_path)
        updated = replace(
            proposal,
            id=new_id,
            new_path=new_path,
            proposed_values={
                **proposal.proposed_values,
                "filename": filename,
            },
            reason=f"{proposal.reason} Filename corrected during review.",
        )
        proposals = tuple(
            updated if item.id == proposal.id else item
            for item in self.plan.rename_proposals
        )
        self.plan = self.plan.with_rename_proposals(proposals)
        self._row_ids[("renames", row)] = new_id
        if proposal.id in self.selected_ids:
            self.selected_ids.remove(proposal.id)
            self.selected_ids.add(new_id)
        values = list(tree.item(row, "values"))
        values[3] = updated.proposed_values.get("filename", "")
        tree.item(row, values=values)
        self.status_var.set(f"Corrected proposed filename to {filename}.")

    def _set_selected_ids(self, selected_ids) -> None:
        self.selected_ids = set(selected_ids)
        for tree_name in ("renames", "tags"):
            tree = self.trees[tree_name]
            for row in tree.get_children(""):
                values = list(tree.item(row, "values"))
                if values:
                    values[0] = (
                        "☑"
                        if self._row_ids.get((tree_name, row))
                        in self.selected_ids
                        else "☐"
                    )
                    tree.item(row, values=values)

    def _close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "Cancel operation", "Request cancellation and close the application?"
            ):
                return
            self.cancel_event.set()
            self.root.after(100, self._close)
            return
        self.root.destroy()


def run() -> None:
    ensure_app_dirs()
    root = tk.Tk()
    SongOrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
