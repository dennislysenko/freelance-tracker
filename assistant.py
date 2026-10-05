"""Assistant session: transcript, proposals, and the write path.

State lives here rather than in the web view because `loadHTML_` reloads the
whole document on every dashboard refresh (and from ~10 other call sites), so
anything kept only in the DOM is wiped mid-conversation. The view renders from
this; it never owns it.

Nothing here writes to Toggl until `apply_proposal` is called with a proposal
id the session itself handed out. The web view applies *by id* and never sends
entry data back across the bridge, so a rendering bug cannot become a wrong
time entry.
"""

from __future__ import annotations

import nl_time

# Cap on retained turns. The transcript is re-rendered into the document on
# every refresh, so an unbounded one would grow the popover's HTML forever.
MAX_TURNS = 40


class Turn:
    """One entry in the transcript."""

    def __init__(self, role, text, proposal=None, error=None, note=None):
        self.role = role  # "user" | "assistant"
        self.text = text
        self.proposal = proposal  # Proposal | None
        self.error = error
        self.note = note


class Proposal:
    """Resolved entries awaiting confirmation, plus which ones collide."""

    def __init__(self, proposal_id, entries, collisions):
        self.id = proposal_id
        self.entries = entries  # resolve_entries() output
        self.collisions = set(collisions)
        self.applied = False
        self.applied_count = 0
        self.applying = False  # writes in flight on a worker thread

    def default_selection(self):
        """Rows checked when the card first renders.

        Colliding rows start unchecked: re-running "the past 2 days" should not
        silently double-log, but overlapping work is legitimate, so the user
        can still tick them back on.
        """
        return [i for i in range(len(self.entries)) if i not in self.collisions]


class AssistantSession:
    """Owns the transcript and pending proposals for the popover assistant."""

    def __init__(self, projects_provider=None, entries_provider=None, writer=None):
        # Injected so the session is testable without Toggl or OpenAI.
        self._projects_provider = projects_provider
        self._entries_provider = entries_provider
        self._writer = writer
        self.turns = []
        self._proposals = {}
        self._next_proposal_id = 0
        self.pending = False

    # --- transcript -----------------------------------------------------

    def add_user_turn(self, text):
        self._append(Turn("user", text))

    def add_error_turn(self, message):
        self._append(Turn("assistant", "", error=message))

    def add_note_turn(self, message):
        self._append(Turn("assistant", message))

    def _append(self, turn):
        self.turns.append(turn)
        if len(self.turns) > MAX_TURNS:
            dropped = self.turns[:-MAX_TURNS]
            self.turns = self.turns[-MAX_TURNS:]
            for old in dropped:
                if old.proposal is not None:
                    self._proposals.pop(old.proposal.id, None)

    def clear(self):
        self.turns = []
        self._proposals = {}
        self.pending = False

    # --- the slow part (worker thread) ----------------------------------

    def resolve(self, utterance):
        """
        Parse an utterance into a result dict. Runs on a worker thread, so it
        must not touch AppKit or mutate the transcript — the caller applies the
        outcome on the main thread via `accept`.
        """
        projects = (self._projects_provider or _default_projects)()
        projects_map = nl_time.project_name_map(projects)
        if not projects_map:
            raise nl_time.NLConfigError("No Toggl projects available to log against.")

        parsed = nl_time.interpret(utterance, sorted(projects_map))
        intent = parsed.get("intent")

        if intent == "log":
            entries = nl_time.resolve_entries(parsed.get("entries"), projects_map)
            if not entries:
                return {"kind": "note", "message": "Nothing to log from that."}
            existing = self._existing_entries_for(entries)
            collisions = nl_time.find_collisions(entries, existing)
            return {
                "kind": "proposal",
                "entries": entries,
                "collisions": collisions,
                "message": parsed.get("message"),
            }

        if intent == "question":
            return {
                "kind": "answer",
                "query": parsed.get("query"),
                "message": parsed.get("message"),
            }

        return {
            "kind": "note",
            "message": parsed.get("message") or "I could not tell what to log.",
        }

    def _existing_entries_for(self, entries):
        """Cached Toggl entries spanning the proposed days, for collision checks."""
        if not entries or self._entries_provider is None:
            return []
        days = [item["day"] for item in entries]
        try:
            return self._entries_provider(min(days), max(days)) or []
        except Exception:
            # A collision check that fails should not block logging; the user
            # still confirms every entry.
            return []

    # --- applying the outcome (main thread) -----------------------------

    def accept(self, result):
        """Fold a `resolve` result into the transcript. Main thread."""
        kind = result.get("kind")
        if kind == "proposal":
            proposal = Proposal(
                self._mint_proposal_id(), result["entries"], result["collisions"]
            )
            self._proposals[proposal.id] = proposal
            self._append(
                Turn("assistant", "", proposal=proposal, note=result.get("message"))
            )
            return proposal
        self._append(Turn("assistant", result.get("message") or ""))
        return None

    def _mint_proposal_id(self):
        self._next_proposal_id += 1
        return f"p{self._next_proposal_id}"

    def get_proposal(self, proposal_id):
        return self._proposals.get(proposal_id)

    def apply_proposal(self, proposal_id, selected_indices):
        """
        Write the selected rows of a proposal to Toggl.

        Returns (written_count, error_message). Each entry is one POST; the
        caller is responsible for invalidating the affected day caches.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return 0, "That proposal is no longer available."
        if proposal.applied:
            return 0, "Those entries were already applied."

        chosen = [
            proposal.entries[i]
            for i in sorted(set(selected_indices))
            if 0 <= i < len(proposal.entries)
        ]
        if not chosen:
            return 0, "Nothing selected to log."

        write = self._writer or _default_writer
        written = 0
        for item in chosen:
            try:
                write(
                    start=item["start"],
                    duration_seconds=item["duration_seconds"],
                    description=item["description"],
                    project_id=item["project_id"],
                    billable=item["billable"],
                )
            except Exception as exc:
                # Report partial success honestly rather than implying none of
                # it landed -- the earlier entries really are in Toggl.
                return written, f"Logged {written} of {len(chosen)}, then failed: {exc}"
            written += 1

        proposal.applied = True
        proposal.applied_count = written
        return written, None

    def applied_days(self, proposal_id):
        """Days touched by a proposal, for cache invalidation."""
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return []
        return sorted({item["day"] for item in proposal.entries})


def _default_projects():
    import toggl_data

    return toggl_data.get_projects()


def _default_writer(**kwargs):
    import toggl_data

    return toggl_data.create_time_entry(**kwargs)
