import logging
import time
from typing import Optional
from uuid import uuid4

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Integer, Text, and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Answer and report lifecycle states, so the literals live in one place.
STATUS_PENDING = 'pending'
STATUS_COMPLETE = 'complete'
STATUS_FAILED = 'failed'

# How many times a revision insert retries when a concurrent insert takes the
# number first. The browser fires three providers at once and a user can
# double-click; the unique index makes the collision visible, and re-reading
# max(revision) resolves it. Bounded so a genuinely broken index cannot spin.
_REVISION_INSERT_ATTEMPTS = 5


####################
# Answer Compare DB Schema
####################


class AnswerCompareRun(Base):
    __tablename__ = 'answer_compare_run'

    id = Column(Text, primary_key=True)
    prompt = Column(Text, nullable=False)
    reference = Column(Text, nullable=True)
    # server_default mirrors the migration so create_all() builds the same schema
    # the production database gets; default keeps ORM inserts from relying on it.
    #
    # **Deliberately unused, recorded as a decision (stage 6a).** Every run is
    # written 'active' and nothing reads this column: there is no archive or hide
    # action in the product, and GET /runs lists everything. It is kept because
    # PLAN-RU.md §4.2 mandates it and archiving is a plausible next ask — but a
    # column that is written and never read is a question every reader asks once,
    # so the answer lives here rather than in someone's memory.
    status = Column(Text, nullable=False, default='active', server_default=text("'active'"))  # active | archived
    rerun_of_run_id = Column(Text, nullable=True)
    # user_id is the durable key; admin_email is for display only (emails change).
    user_id = Column(Text, nullable=False)
    admin_email = Column(Text, nullable=False)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index('ix_answer_compare_run_created', 'created_at'),)


class AnswerCompareAnswer(Base):
    __tablename__ = 'answer_compare_answer'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)  # chatgpt | gemini | vesqor
    # 1-based; a regeneration inserts revision + 1 and leaves the old row intact,
    # which is what the derived "outdated" check compares against.
    revision = Column(Integer, nullable=False)
    # requested_model is what we asked for, model is what the provider reported —
    # providers resolve aliases, and without both a run cannot be reproduced.
    requested_model = Column(Text, nullable=True)
    model = Column(Text, nullable=True)
    engine_version = Column(Text, nullable=True)
    params = Column(JSON, nullable=True)
    text = Column(Text, nullable=True)
    status = Column(Text, nullable=False)  # pending | complete | failed
    error = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_answer_compare_answer_run', 'run_id'),
        Index('ux_answer_compare_answer_run_provider_rev', 'run_id', 'provider', 'revision', unique=True),
    )


class AnswerCompareReport(Base):
    __tablename__ = 'answer_compare_report'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False)
    judge = Column(Text, nullable=False)  # chatgpt | gemini | vesqor
    # 1-based per (run_id, judge); re-judging inserts revision + 1 and never
    # overwrites or deletes an earlier verdict. The current report is the max.
    revision = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)  # pending | complete | failed
    requested_model = Column(Text, nullable=True)
    model = Column(Text, nullable=True)
    # anonymous label -> provider, shuffled per judge and stored with the report
    label_map = Column(JSON, nullable=True)
    report = Column(JSON, nullable=True)
    judged_versions = Column(JSON, nullable=True)  # [{'provider': ..., 'revision': N}, ...]
    blinding_compromised = Column(JSON, nullable=True)
    # What was actually sent to the judge, plus the structured-output ladder
    # diagnostics (mode used, every rejection). Added by v2q5p0a0r0m.
    params = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_answer_compare_report_run', 'run_id'),
        Index('ux_answer_compare_report_run_judge_rev', 'run_id', 'judge', 'revision', unique=True),
    )


class AnswerCompareSummary(Base):
    __tablename__ = 'answer_compare_summary'

    id = Column(Text, primary_key=True)
    run_id = Column(Text, nullable=False)
    # 1-based per run_id; a recomputed summary inserts revision + 1 and the
    # summaries built on a previous answer set are kept.
    revision = Column(Integer, nullable=False)
    narrative = Column(Text, nullable=False)
    tally = Column(JSON, nullable=False)
    judged_versions = Column(JSON, nullable=False)
    partial = Column(Boolean, nullable=False, default=False, server_default=text('false'))
    included_judges = Column(JSON, nullable=False)

    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('ix_answer_compare_summary_run', 'run_id'),
        Index('ux_answer_compare_summary_run_rev', 'run_id', 'revision', unique=True),
    )


####################
# Pydantic Models
####################


class AnswerCompareRunModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    prompt: str
    reference: Optional[str] = None
    status: str
    rerun_of_run_id: Optional[str] = None
    user_id: str
    admin_email: str

    created_at: int
    updated_at: int


class AnswerCompareAnswerModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    provider: str
    revision: int
    requested_model: Optional[str] = None
    model: Optional[str] = None
    engine_version: Optional[str] = None
    params: Optional[dict] = None
    text: Optional[str] = None
    status: str
    error: Optional[str] = None

    created_at: int
    updated_at: int


class AnswerCompareReportModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    judge: str
    revision: int
    status: str
    requested_model: Optional[str] = None
    model: Optional[str] = None
    label_map: Optional[dict] = None
    report: Optional[dict] = None
    judged_versions: Optional[list] = None
    blinding_compromised: Optional[list] = None
    params: Optional[dict] = None
    error: Optional[str] = None

    created_at: int
    updated_at: int


class AnswerCompareSummaryModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    revision: int
    narrative: str
    tally: dict
    judged_versions: list
    partial: bool
    included_judges: list

    created_at: int
    updated_at: int


class AnswerCompareRunForm(BaseModel):
    prompt: str
    reference: Optional[str] = None
    rerun_of_run_id: Optional[str] = None


class AnswerVersion(BaseModel):
    provider: str
    revision: int


####################
# AnswerCompareRunTable
####################


class AnswerCompareRunTable:
    async def insert(
        self,
        user_id: str,
        admin_email: str,
        form: AnswerCompareRunForm,
        db: Optional[AsyncSession] = None,
    ) -> AnswerCompareRunModel:
        async with get_async_db_context(db) as db:
            now = int(time.time())
            row = AnswerCompareRun(
                id=uuid4().hex,
                prompt=form.prompt,
                reference=form.reference,
                status='active',
                rerun_of_run_id=form.rerun_of_run_id,
                user_id=user_id,
                admin_email=admin_email,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            return AnswerCompareRunModel.model_validate(row)

    async def get_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[AnswerCompareRunModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AnswerCompareRun, id)
            return AnswerCompareRunModel.model_validate(row) if row else None

    async def list_runs(
        self,
        limit: int = 50,
        before: Optional[tuple[int, str]] = None,
        db: Optional[AsyncSession] = None,
    ) -> list[AnswerCompareRunModel]:
        """Runs newest first, optionally strictly older than a cursor.

        The cursor is the compound ``(created_at, id)``, not the timestamp alone:
        timestamps are whole seconds, so several runs created in the same second
        and straddling a page boundary would otherwise fall through the gap and
        appear on no page at all.

        **Known limitation, accepted:** within one second the relative order of
        two runs is decided by their uuid, so it is arbitrary — but it is *stable*
        between requests, which is all paging needs. "Which of these two is newer"
        simply has no answer at second precision, and seconds are the project's
        timestamp unit throughout (DECISIONS: stage 1). Callers must not assume
        the most recently created run sorts first.
        """
        async with get_async_db_context(db) as db:
            stmt = select(AnswerCompareRun)
            if before is not None:
                created_at, run_id = before
                stmt = stmt.where(
                    or_(
                        AnswerCompareRun.created_at < created_at,
                        and_(AnswerCompareRun.created_at == created_at, AnswerCompareRun.id < run_id),
                    )
                )
            stmt = stmt.order_by(AnswerCompareRun.created_at.desc(), AnswerCompareRun.id.desc()).limit(limit)
            result = await db.execute(stmt)
            return [AnswerCompareRunModel.model_validate(r) for r in result.scalars().all()]

    async def count_reruns_of(self, run_ids: list[str], db: Optional[AsyncSession] = None) -> dict[str, int]:
        """How many runs name each of these as their source — one grouped query."""
        if not run_ids:
            return {}
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AnswerCompareRun.rerun_of_run_id, func.count())
                .where(AnswerCompareRun.rerun_of_run_id.in_(run_ids))
                .group_by(AnswerCompareRun.rerun_of_run_id)
            )
            return {parent: count for parent, count in result.all() if parent is not None}


####################
# AnswerCompareAnswerTable
####################


class AnswerCompareAnswerTable:
    async def insert_next_revision(
        self,
        run_id: str,
        provider: str,
        status: str,
        requested_model: Optional[str] = None,
        model: Optional[str] = None,
        engine_version: Optional[str] = None,
        params: Optional[dict] = None,
        text: Optional[str] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> AnswerCompareAnswerModel:
        """Insert a new answer row at ``max(revision) + 1`` for (run_id, provider).

        Earlier revisions are never touched — the history is what the derived
        "outdated review" check reads.

        The browser fires all three providers at once and a user can double-click,
        so two callers can read the same ``max(revision)``. The unique index on
        (run_id, provider, revision) turns that into an IntegrityError rather than
        a lost update; re-reading the maximum and retrying resolves it, which is
        why this is not allowed to surface as a 500.
        """
        for attempt in range(_REVISION_INSERT_ATTEMPTS):
            async with get_async_db_context(db) as db_session:
                result = await db_session.execute(
                    select(func.max(AnswerCompareAnswer.revision)).where(
                        AnswerCompareAnswer.run_id == run_id,
                        AnswerCompareAnswer.provider == provider,
                    )
                )
                next_revision = (result.scalar() or 0) + 1

                now = int(time.time())
                row = AnswerCompareAnswer(
                    id=uuid4().hex,
                    run_id=run_id,
                    provider=provider,
                    revision=next_revision,
                    requested_model=requested_model,
                    model=model,
                    engine_version=engine_version,
                    params=params,
                    text=text,
                    status=status,
                    error=error,
                    created_at=now,
                    updated_at=now,
                )
                db_session.add(row)

                try:
                    await db_session.commit()
                except IntegrityError:
                    # Someone else took this revision number between the read and
                    # the commit. Roll back so a shared session stays usable, then
                    # re-read the maximum.
                    await db_session.rollback()
                    if attempt == _REVISION_INSERT_ATTEMPTS - 1:
                        raise
                    log.debug(
                        'answer revision collision for run=%s provider=%s, retrying (attempt %s)',
                        run_id,
                        provider,
                        attempt + 1,
                    )
                    continue

                return AnswerCompareAnswerModel.model_validate(row)

        raise RuntimeError('unreachable: revision insert loop exhausted without returning or raising')

    async def update_result(
        self,
        id: str,
        status: str,
        model: Optional[str] = None,
        engine_version: Optional[str] = None,
        params: Optional[dict] = None,
        text: Optional[str] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[AnswerCompareAnswerModel]:
        """Settle a **pending** row in place with the outcome of the provider call.

        The row is written before the call so a connection dropped mid-request
        does not throw away a completion that was already paid for; this is the
        other half of that.

        Only a pending row is ever touched. Returns None — writing nothing — when
        the row is gone or has already been settled, so an already-complete answer
        can never be overwritten with a later failure. Nothing reaches this with a
        settled id today, but "nothing overwrites an existing answer" (owner
        decision, 2026-09-12) must hold in the table, not in the discipline of
        whoever calls it; stage 3 adds callers.
        """
        async with get_async_db_context(db) as db:
            row = await db.get(AnswerCompareAnswer, id)
            if not row:
                return None
            if row.status != STATUS_PENDING:
                log.warning(
                    'refusing to settle answer %s: already %s, not %s',
                    id,
                    row.status,
                    STATUS_PENDING,
                )
                return None

            row.status = status
            row.model = model
            row.engine_version = engine_version
            if params is not None:
                row.params = params
            row.text = text
            row.error = error
            row.updated_at = int(time.time())

            await db.commit()
            return AnswerCompareAnswerModel.model_validate(row)

    async def get_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[AnswerCompareAnswerModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AnswerCompareAnswer, id)
            return AnswerCompareAnswerModel.model_validate(row) if row else None

    async def get_current_by_run(
        self, run_id: str, db: Optional[AsyncSession] = None
    ) -> list[AnswerCompareAnswerModel]:
        """The current answer per provider: the highest revision that is complete.

        Pending and failed rows are kept (append-only) but are never current. A
        failed retry must not erase the last good answer from the card, and — via
        get_current_versions — must not mark every existing judge report outdated.
        """
        async with get_async_db_context(db) as db:
            latest = (
                select(
                    AnswerCompareAnswer.provider,
                    func.max(AnswerCompareAnswer.revision).label('max_revision'),
                )
                .where(
                    AnswerCompareAnswer.run_id == run_id,
                    AnswerCompareAnswer.status == STATUS_COMPLETE,
                )
                .group_by(AnswerCompareAnswer.provider)
                .subquery()
            )
            result = await db.execute(
                select(AnswerCompareAnswer)
                .join(
                    latest,
                    (AnswerCompareAnswer.provider == latest.c.provider)
                    & (AnswerCompareAnswer.revision == latest.c.max_revision),
                )
                .where(
                    AnswerCompareAnswer.run_id == run_id,
                    AnswerCompareAnswer.status == STATUS_COMPLETE,
                )
                .order_by(AnswerCompareAnswer.provider)
            )
            return [AnswerCompareAnswerModel.model_validate(r) for r in result.scalars().all()]

    async def get_latest_attempt_by_run(
        self, run_id: str, db: Optional[AsyncSession] = None
    ) -> list[AnswerCompareAnswerModel]:
        """The highest revision per provider regardless of status — the retry state."""
        async with get_async_db_context(db) as db:
            latest = (
                select(
                    AnswerCompareAnswer.provider,
                    func.max(AnswerCompareAnswer.revision).label('max_revision'),
                )
                .where(AnswerCompareAnswer.run_id == run_id)
                .group_by(AnswerCompareAnswer.provider)
                .subquery()
            )
            result = await db.execute(
                select(AnswerCompareAnswer)
                .join(
                    latest,
                    (AnswerCompareAnswer.provider == latest.c.provider)
                    & (AnswerCompareAnswer.revision == latest.c.max_revision),
                )
                .where(AnswerCompareAnswer.run_id == run_id)
                .order_by(AnswerCompareAnswer.provider)
            )
            return [AnswerCompareAnswerModel.model_validate(r) for r in result.scalars().all()]

    async def get_latest_attempt(
        self, run_id: str, provider: str, db: Optional[AsyncSession] = None
    ) -> Optional[AnswerCompareAnswerModel]:
        """The highest revision for one provider, any status."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AnswerCompareAnswer)
                .where(
                    AnswerCompareAnswer.run_id == run_id,
                    AnswerCompareAnswer.provider == provider,
                )
                .order_by(AnswerCompareAnswer.revision.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return AnswerCompareAnswerModel.model_validate(row) if row else None

    async def count_by_status_for_runs(
        self, run_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, dict[str, int]]:
        """Per run: how many providers are complete, failed on their latest attempt, pending.

        "Current" in this project means the highest **complete** revision
        (DECISIONS.md#010), so a provider can be both: revision 1 complete and
        revision 2 failed is one complete answer AND one failed attempt. Two
        grouped queries, no matter how many runs.
        """
        if not run_ids:
            return {}

        async with get_async_db_context(db) as db:
            complete_max = (
                select(
                    AnswerCompareAnswer.run_id.label('run_id'),
                    AnswerCompareAnswer.provider.label('provider'),
                )
                .where(
                    AnswerCompareAnswer.run_id.in_(run_ids),
                    AnswerCompareAnswer.status == STATUS_COMPLETE,
                )
                .group_by(AnswerCompareAnswer.run_id, AnswerCompareAnswer.provider)
                .subquery()
            )
            complete_rows = await db.execute(
                select(complete_max.c.run_id, func.count()).group_by(complete_max.c.run_id)
            )

            latest = (
                select(
                    AnswerCompareAnswer.run_id.label('run_id'),
                    AnswerCompareAnswer.provider.label('provider'),
                    func.max(AnswerCompareAnswer.revision).label('max_revision'),
                )
                .where(AnswerCompareAnswer.run_id.in_(run_ids))
                .group_by(AnswerCompareAnswer.run_id, AnswerCompareAnswer.provider)
                .subquery()
            )
            latest_rows = await db.execute(
                select(AnswerCompareAnswer.run_id, AnswerCompareAnswer.status, func.count())
                .join(
                    latest,
                    (AnswerCompareAnswer.run_id == latest.c.run_id)
                    & (AnswerCompareAnswer.provider == latest.c.provider)
                    & (AnswerCompareAnswer.revision == latest.c.max_revision),
                )
                .group_by(AnswerCompareAnswer.run_id, AnswerCompareAnswer.status)
            )

            counts: dict[str, dict[str, int]] = {
                run_id: {'complete': 0, 'failed_attempts': 0, 'pending': 0} for run_id in run_ids
            }
            for run_id, complete in complete_rows.all():
                counts[run_id]['complete'] = complete
            for run_id, status, total in latest_rows.all():
                if status == STATUS_FAILED:
                    counts[run_id]['failed_attempts'] += total
                elif status == STATUS_PENDING:
                    counts[run_id]['pending'] += total
            return counts

    async def get_current_versions(self, run_id: str, db: Optional[AsyncSession] = None) -> list[AnswerVersion]:
        """The run's current [{provider, revision}] set — input to the outdated check.

        Complete rows only. A provider with no complete answer contributes no
        entry, so this set — and therefore every report's outdated status —
        changes when the *answer set* changes, never because an attempt failed.
        """
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(
                    AnswerCompareAnswer.provider,
                    func.max(AnswerCompareAnswer.revision),
                )
                .where(
                    AnswerCompareAnswer.run_id == run_id,
                    AnswerCompareAnswer.status == STATUS_COMPLETE,
                )
                .group_by(AnswerCompareAnswer.provider)
                .order_by(AnswerCompareAnswer.provider)
            )
            return [AnswerVersion(provider=provider, revision=revision) for provider, revision in result.all()]


####################
# AnswerCompareReportTable
####################


class AnswerCompareReportTable:
    async def insert_next_revision(
        self,
        run_id: str,
        judge: str,
        status: str,
        requested_model: Optional[str] = None,
        model: Optional[str] = None,
        label_map: Optional[dict] = None,
        report: Optional[dict] = None,
        judged_versions: Optional[list] = None,
        blinding_compromised: Optional[list] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> AnswerCompareReportModel:
        """Insert this judge's verdict at ``max(revision) + 1`` for (run_id, judge).

        Append-only by owner decision (2026-09-12): re-judging never overwrites or
        deletes an earlier verdict, it only supersedes it. The unique index on
        (run_id, judge, revision) turns a concurrent insert into an IntegrityError;
        re-reading the maximum and retrying resolves it, exactly as for answers.
        """
        for attempt in range(_REVISION_INSERT_ATTEMPTS):
            async with get_async_db_context(db) as db_session:
                result = await db_session.execute(
                    select(func.max(AnswerCompareReport.revision)).where(
                        AnswerCompareReport.run_id == run_id,
                        AnswerCompareReport.judge == judge,
                    )
                )
                next_revision = (result.scalar() or 0) + 1

                now = int(time.time())
                row = AnswerCompareReport(
                    id=uuid4().hex,
                    run_id=run_id,
                    judge=judge,
                    revision=next_revision,
                    status=status,
                    requested_model=requested_model,
                    model=model,
                    label_map=label_map,
                    report=report,
                    judged_versions=judged_versions,
                    blinding_compromised=blinding_compromised,
                    error=error,
                    created_at=now,
                    updated_at=now,
                )
                db_session.add(row)

                try:
                    await db_session.commit()
                except IntegrityError:
                    await db_session.rollback()
                    if attempt == _REVISION_INSERT_ATTEMPTS - 1:
                        raise
                    log.debug(
                        'report revision collision for run=%s judge=%s, retrying (attempt %s)',
                        run_id,
                        judge,
                        attempt + 1,
                    )
                    continue

                return AnswerCompareReportModel.model_validate(row)

        raise RuntimeError('unreachable: revision insert loop exhausted without returning or raising')

    async def update_result(
        self,
        id: str,
        status: str,
        model: Optional[str] = None,
        report: Optional[dict] = None,
        params: Optional[dict] = None,
        error: Optional[str] = None,
        db: Optional[AsyncSession] = None,
    ) -> Optional[AnswerCompareReportModel]:
        """Settle a **pending** report row in place with the judge's outcome.

        Only a pending row is ever touched; None means nothing was written — the
        row is gone or already settled. Same guard as for answers: "nothing
        overwrites an existing verdict" holds in the table, not in the caller.
        """
        async with get_async_db_context(db) as db:
            row = await db.get(AnswerCompareReport, id)
            if not row:
                return None
            if row.status != STATUS_PENDING:
                log.warning('refusing to settle report %s: already %s, not %s', id, row.status, STATUS_PENDING)
                return None

            row.status = status
            row.model = model
            row.report = report
            if params is not None:
                row.params = params
            row.error = error
            row.updated_at = int(time.time())

            await db.commit()
            return AnswerCompareReportModel.model_validate(row)

    async def get_by_id(self, id: str, db: Optional[AsyncSession] = None) -> Optional[AnswerCompareReportModel]:
        async with get_async_db_context(db) as db:
            row = await db.get(AnswerCompareReport, id)
            return AnswerCompareReportModel.model_validate(row) if row else None

    async def get_current_by_run(
        self, run_id: str, db: Optional[AsyncSession] = None
    ) -> list[AnswerCompareReportModel]:
        """The current report per judge: the highest revision that is complete.

        A failed re-judge must not erase a good verdict or change what counts as
        current — the same rule as for answers, for the same reason.
        """
        async with get_async_db_context(db) as db:
            latest = (
                select(
                    AnswerCompareReport.judge,
                    func.max(AnswerCompareReport.revision).label('max_revision'),
                )
                .where(
                    AnswerCompareReport.run_id == run_id,
                    AnswerCompareReport.status == STATUS_COMPLETE,
                )
                .group_by(AnswerCompareReport.judge)
                .subquery()
            )
            result = await db.execute(
                select(AnswerCompareReport)
                .join(
                    latest,
                    (AnswerCompareReport.judge == latest.c.judge)
                    & (AnswerCompareReport.revision == latest.c.max_revision),
                )
                .where(
                    AnswerCompareReport.run_id == run_id,
                    AnswerCompareReport.status == STATUS_COMPLETE,
                )
                .order_by(AnswerCompareReport.judge)
            )
            return [AnswerCompareReportModel.model_validate(r) for r in result.scalars().all()]

    async def get_latest_attempt_by_run(
        self, run_id: str, db: Optional[AsyncSession] = None
    ) -> list[AnswerCompareReportModel]:
        """The highest revision per judge regardless of status — the retry state."""
        async with get_async_db_context(db) as db:
            latest = (
                select(
                    AnswerCompareReport.judge,
                    func.max(AnswerCompareReport.revision).label('max_revision'),
                )
                .where(AnswerCompareReport.run_id == run_id)
                .group_by(AnswerCompareReport.judge)
                .subquery()
            )
            result = await db.execute(
                select(AnswerCompareReport)
                .join(
                    latest,
                    (AnswerCompareReport.judge == latest.c.judge)
                    & (AnswerCompareReport.revision == latest.c.max_revision),
                )
                .where(AnswerCompareReport.run_id == run_id)
                .order_by(AnswerCompareReport.judge)
            )
            return [AnswerCompareReportModel.model_validate(r) for r in result.scalars().all()]

    async def get_latest_attempt(
        self, run_id: str, judge: str, db: Optional[AsyncSession] = None
    ) -> Optional[AnswerCompareReportModel]:
        """The highest revision for one judge, any status."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AnswerCompareReport)
                .where(
                    AnswerCompareReport.run_id == run_id,
                    AnswerCompareReport.judge == judge,
                )
                .order_by(AnswerCompareReport.revision.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return AnswerCompareReportModel.model_validate(row) if row else None

    async def count_by_status_for_runs(
        self, run_ids: list[str], db: Optional[AsyncSession] = None
    ) -> dict[str, dict[str, int]]:
        """Per run: judges with a complete report, and judges whose latest attempt failed."""
        if not run_ids:
            return {}

        async with get_async_db_context(db) as db:
            complete_judges = (
                select(
                    AnswerCompareReport.run_id.label('run_id'),
                    AnswerCompareReport.judge.label('judge'),
                )
                .where(
                    AnswerCompareReport.run_id.in_(run_ids),
                    AnswerCompareReport.status == STATUS_COMPLETE,
                )
                .group_by(AnswerCompareReport.run_id, AnswerCompareReport.judge)
                .subquery()
            )
            complete_rows = await db.execute(
                select(complete_judges.c.run_id, func.count()).group_by(complete_judges.c.run_id)
            )

            latest = (
                select(
                    AnswerCompareReport.run_id.label('run_id'),
                    AnswerCompareReport.judge.label('judge'),
                    func.max(AnswerCompareReport.revision).label('max_revision'),
                )
                .where(AnswerCompareReport.run_id.in_(run_ids))
                .group_by(AnswerCompareReport.run_id, AnswerCompareReport.judge)
                .subquery()
            )
            latest_rows = await db.execute(
                select(AnswerCompareReport.run_id, AnswerCompareReport.status, func.count())
                .join(
                    latest,
                    (AnswerCompareReport.run_id == latest.c.run_id)
                    & (AnswerCompareReport.judge == latest.c.judge)
                    & (AnswerCompareReport.revision == latest.c.max_revision),
                )
                .group_by(AnswerCompareReport.run_id, AnswerCompareReport.status)
            )

            counts: dict[str, dict[str, int]] = {run_id: {'complete': 0, 'failed_attempts': 0} for run_id in run_ids}
            for run_id, complete in complete_rows.all():
                counts[run_id]['complete'] = complete
            for run_id, status, total in latest_rows.all():
                if status == STATUS_FAILED:
                    counts[run_id]['failed_attempts'] += total
            return counts

    async def get_all_by_run(self, run_id: str, db: Optional[AsyncSession] = None) -> list[AnswerCompareReportModel]:
        """Every verdict of a run, superseded ones included — the judgement history."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AnswerCompareReport)
                .where(AnswerCompareReport.run_id == run_id)
                .order_by(AnswerCompareReport.judge, AnswerCompareReport.revision)
            )
            return [AnswerCompareReportModel.model_validate(r) for r in result.scalars().all()]


####################
# AnswerCompareSummaryTable
####################


class AnswerCompareSummaryTable:
    async def insert_next_revision(
        self,
        run_id: str,
        narrative: str,
        tally: dict,
        judged_versions: list,
        partial: bool,
        included_judges: list,
        db: Optional[AsyncSession] = None,
    ) -> AnswerCompareSummaryModel:
        """Insert the run's summary at ``max(revision) + 1``; earlier ones are kept.

        The unique index on (run_id, revision) turns a concurrent insert into an
        IntegrityError; re-reading the maximum and retrying resolves it, exactly
        as for answers and reports.
        """
        for attempt in range(_REVISION_INSERT_ATTEMPTS):
            async with get_async_db_context(db) as db_session:
                result = await db_session.execute(
                    select(func.max(AnswerCompareSummary.revision)).where(AnswerCompareSummary.run_id == run_id)
                )
                next_revision = (result.scalar() or 0) + 1

                now = int(time.time())
                row = AnswerCompareSummary(
                    id=uuid4().hex,
                    run_id=run_id,
                    revision=next_revision,
                    narrative=narrative,
                    tally=tally,
                    judged_versions=judged_versions,
                    partial=partial,
                    included_judges=included_judges,
                    created_at=now,
                    updated_at=now,
                )
                db_session.add(row)

                try:
                    await db_session.commit()
                except IntegrityError:
                    await db_session.rollback()
                    if attempt == _REVISION_INSERT_ATTEMPTS - 1:
                        raise
                    log.debug('summary revision collision for run=%s, retrying (attempt %s)', run_id, attempt + 1)
                    continue

                return AnswerCompareSummaryModel.model_validate(row)

        raise RuntimeError('unreachable: revision insert loop exhausted without returning or raising')

    async def get_current_by_run(
        self, run_id: str, db: Optional[AsyncSession] = None
    ) -> Optional[AnswerCompareSummaryModel]:
        """The highest-revision summary of a run."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AnswerCompareSummary)
                .where(AnswerCompareSummary.run_id == run_id)
                .order_by(AnswerCompareSummary.revision.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return AnswerCompareSummaryModel.model_validate(row) if row else None

    async def run_ids_with_summary(self, run_ids: list[str], db: Optional[AsyncSession] = None) -> set[str]:
        """Which of these runs have at least one summary — one grouped query."""
        if not run_ids:
            return set()
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(AnswerCompareSummary.run_id)
                .where(AnswerCompareSummary.run_id.in_(run_ids))
                .group_by(AnswerCompareSummary.run_id)
            )
            return {row[0] for row in result.all()}

    async def count_by_run(self, run_id: str, db: Optional[AsyncSession] = None) -> int:
        """How many summaries this run has — the history depth stage 6 will list."""
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(func.count()).select_from(AnswerCompareSummary).where(AnswerCompareSummary.run_id == run_id)
            )
            return result.scalar() or 0


AnswerCompareRuns = AnswerCompareRunTable()
AnswerCompareAnswers = AnswerCompareAnswerTable()
AnswerCompareReports = AnswerCompareReportTable()
AnswerCompareSummaries = AnswerCompareSummaryTable()
