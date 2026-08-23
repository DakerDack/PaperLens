# PaperLens AI Development Rules

Before editing code, read `docs/DEV_PLAN.md` and use exactly one task card from its current phase.

For every task, state before editing:

1. The single observable goal.
2. At most four files allowed to change.
3. The fixed input/output contract and error codes.
4. The exact verification commands.

Hard rules:

- Do not add directories, dependencies, API routes, database tables, schema fields, or product features outside `docs/DEV_PLAN.md` without explicit user approval.
- Keep backend modules flat. Do not create speculative abstractions or placeholder modules.
- `backend/app/models.py` is the data-contract source of truth.
- Only `backend/app/hy3_service.py` may call Hy3.
- Only `backend/app/document_service.py` may read MinerU raw output.
- Only `backend/app/project_store.py` may execute SQL.
- Hy3 must never be treated as the source of page numbers, bbox coordinates, verified quotations, scores, or pass/fail decisions.
- Live API failures must remain failures. Never silently return Mock data.
- Add or update focused tests before the minimum implementation.
- Run the task-specific test, then the current phase regression tests.
- Do not refactor unrelated files or weaken tests to make a change pass.

Stop and report the conflict instead of guessing when an official API contradicts the contract, a new dependency is unavoidable, or a change requires more than four unplanned files.
