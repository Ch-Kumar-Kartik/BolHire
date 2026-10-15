# Codex Session Initialization: BolHire

## Purpose

This document defines how a new Codex session should recover project context, verify repository state, and safely continue development.

It is not a replacement for source-code inspection or an authorization to implement tasks.

## 1. Understand the project

BolHire is a multilingual, voice-first AI recruitment platform for India.

The long-term product will include:

- A Candidate Agent for conversational onboarding and job discovery.
- A Recruiter Agent for conversational job intake and candidate shortlisting.
- A shared matching engine connecting candidates with relevant opportunities.

Development is divided into approved milestones. The current implementation scope is defined by `docs/progress.md` and the user's latest instructions.

## 2. Required reading order

Read these resources:

1. `AGENTS.md`: Project-wide engineering rules.
2. `docs/progress.md`: Current implementation state and milestone.
3. `docs/codebase-map.md`: Module responsibilities and relevant files.
4. `docs/architecture.md`: System architecture and design decisions.
5. Relevant MVP specification, if available.

Inspect only the source files needed to validate and perform the current task.

## 3. Verify repository state

Before editing:

- Run `git status`.
- Inspect recent commits.
- Identify uncommitted changes.
- Check whether the previous milestone is complete.
- Determine whether any implementation was interrupted.
- Preserve existing work.

Never assume a milestone is completed merely because a progress document says so.

## 4. Recover current task context

Determine:

- Latest completed and verified milestone.
- Current approved task, if any.
- Pending manual verification.
- Known bugs or blockers.
- Relevant API contracts and implementation modules.
- Any outstanding architectural decisions.

If the current task is unclear, request clarification rather than selecting another milestone independently.

## 5. Session readiness report

For a fresh session without an explicit implementation authorization, provide a concise report containing:

- Current project status.
- Last completed milestone.
- Relevant source modules.
- Known blockers or limitations.
- Recommended next action.

Do not modify code during a context-recovery-only request.

If the user has explicitly authorized a task in the same prompt, perform the context checks and proceed unless significant blockers or inconsistencies require clarification.

## 6. Implementation workflow

For each approved task:

1. Inspect relevant implementation files.
2. Confirm existing contracts and dependencies.
3. Implement only the approved scope.
4. Add focused tests.
5. Run relevant checks.
6. Report results and limitations.
7. Update progress and codebase documentation.
8. Stop and wait for milestone approval.

Do not implement future milestones opportunistically.

## 7. Session handoff

Before ending an implementation session, ensure `docs/progress.md` records:

- What was actually implemented.
- Which tests and builds passed or failed.
- What requires manual verification.
- Known limitations and blockers.
- Which milestone is next.
- Whether that next milestone has been approved.

Update `docs/codebase-map.md` if implementation structure changed.

Do not duplicate the entire conversation history.

## 8. Source of truth

- Source code: Actual implementation.
- Git history: Committed changes.
- `AGENTS.md`: Engineering rules.
- `docs/architecture.md`: Architectural decisions.
- `docs/progress.md`: Milestone status and handoff.
- `docs/codebase-map.md`: Navigation guide.

When sources conflict, investigate rather than guessing.
