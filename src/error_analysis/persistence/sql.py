"""SQLAlchemy-backed implementation of the analysis repository."""

from __future__ import annotations

import logging
import re
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, event, func, inspect, select, text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from error_analysis.fingerprint.algorithm import Fingerprint
from error_analysis.persistence.records import (
    ClassOverview,
    ClassResolution,
    ClassTriage,
    EquivalenceClassRecord,
    InsightRecord,
    Severity,
)
from error_analysis.persistence.repository import AnalysisRepository

log = logging.getLogger(__name__)


class _UtcDateTime(TypeDecorator[datetime]):
    """
    A datetime column type that persists timezone-aware instants as naive UTC values and restores
    awareness on retrieval, yielding backend-independent behavior.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class _Base(DeclarativeBase):
    """
    The declarative base of the analysis schema.
    """


class _EquivalenceClassEntity(_Base):
    __tablename__ = "equivalence_class"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    signature: Mapped[str] = mapped_column(Text)
    algorithm_version: Mapped[int] = mapped_column(Integer)
    exemplar_hint: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(_UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(_UtcDateTime)
    issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fixed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    def to_record(self) -> EquivalenceClassRecord:
        return EquivalenceClassRecord(
            id=self.id,
            digest=self.digest,
            signature=self.signature,
            algorithm_version=self.algorithm_version,
            exemplar_hint=self.exemplar_hint,
            first_seen_at=self.first_seen_at,
            last_seen_at=self.last_seen_at,
            triage=ClassTriage(
                severity=Severity(self.severity) if self.severity else None,
                issue_number=self.issue_number,
                pr_number=self.pr_number,
                fixed=bool(self.fixed),
            ),
        )

    def apply_triage(self, triage: ClassTriage) -> None:
        """
        Writes the given triage state to the entity's columns.

        :param triage: the triage state to apply
        """
        self.severity = triage.severity.value if triage.severity is not None else None
        self.issue_number = triage.issue_number
        self.pr_number = triage.pr_number
        self.fixed = triage.fixed


class _ReportAssociationEntity(_Base):
    __tablename__ = "report_association"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("equivalence_class.id"), index=True)
    report_created_at: Mapped[datetime] = mapped_column(_UtcDateTime, index=True)
    report_version: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)


class _InsightEntity(_Base):
    __tablename__ = "insight"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("equivalence_class.id"), index=True)
    analyzed_report_id: Mapped[str] = mapped_column(String(36))
    markdown: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UtcDateTime)

    def to_record(self) -> InsightRecord:
        return InsightRecord(
            id=self.id,
            class_id=self.class_id,
            analyzed_report_id=UUID(self.analyzed_report_id),
            markdown=self.markdown,
            author=self.author,
            created_at=self.created_at,
        )


class SqlAnalysisRepository(AnalysisRepository):
    """
    An analysis repository backed by a relational database via SQLAlchemy.

    The schema is created on instantiation if it does not exist.
    """

    _ID_QUERY_CHUNK_SIZE = 10_000
    """the maximum number of identifiers per bulk lookup query, staying well below SQLite's
    limit of 32766 bind variables per statement"""

    def __init__(self, engine_url: str) -> None:
        """
        :param engine_url: the SQLAlchemy engine URL of the backing database
        """
        self._engine = create_engine(engine_url)
        self._tune_sqlite(self._engine)
        self._session_factory = sessionmaker(self._engine, expire_on_commit=False)
        _Base.metadata.create_all(self._engine)
        self._upgrade_schema()

    def _upgrade_schema(self) -> None:
        """
        Adds columns and indexes that the schema defines but an existing database lacks.

        Tables that already exist are left untouched by the schema creation, so databases created by
        an earlier version of the schema are brought up to date here. Only nullable columns can be
        added in this manner, which suffices for the columns introduced so far.
        """
        # add the columns absent from the existing tables
        inspector = inspect(self._engine)
        for table in _Base.metadata.sorted_tables:
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing_columns:
                    column_type = column.type.compile(self._engine.dialect)
                    log.info("Adding missing column %s.%s to the database schema", table.name, column.name)
                    with self._engine.begin() as connection:
                        connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}"))

        # create the indexes covering columns that were added after their table's creation
        with self._engine.begin() as connection:
            for table in _Base.metadata.sorted_tables:
                for index in table.indexes:
                    index.create(connection, checkfirst=True)

    @staticmethod
    def _tune_sqlite(engine: Any) -> None:
        """
        Applies performance pragmas to SQLite connections (write-ahead logging with relaxed
        synchronization), avoiding a full fsync per transaction. Has no effect for other backends.

        :param engine: the engine to tune
        """
        if engine.dialect.name != "sqlite":
            return

        @event.listens_for(engine, "connect")
        def _apply_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    @classmethod
    def for_sqlite(cls, db_file: Path) -> SqlAnalysisRepository:
        """
        Creates a repository backed by a SQLite database file, creating parent directories as needed.

        :param db_file: the path of the SQLite database file
        :return: the repository
        """
        db_file.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite:///{db_file}")

    def get_or_create_class(self, fingerprint: Fingerprint, exemplar_hint: str) -> ClassResolution:
        with self._session_factory.begin() as session:
            # return the existing class if one matches the digest
            existing = session.scalars(select(_EquivalenceClassEntity).where(_EquivalenceClassEntity.digest == fingerprint.digest)).first()
            if existing is not None:
                return ClassResolution(equivalence_class=existing.to_record(), created=False)

            # create a new class, initializing the seen-instant cache to the current time as a placeholder
            # (the instants are refined by subsequent associations)
            now = datetime.now(UTC)
            entity = _EquivalenceClassEntity(
                digest=fingerprint.digest,
                signature=fingerprint.signature,
                algorithm_version=fingerprint.algorithm_version,
                exemplar_hint=exemplar_hint,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(entity)
            session.flush()
            return ClassResolution(equivalence_class=entity.to_record(), created=True)

    def associate(self, report_id: UUID, class_id: int, report_created_at: datetime, report_version: str | None = None) -> None:
        with self._session_factory.begin() as session:
            # insert the association unless it already exists
            if session.get(_ReportAssociationEntity, str(report_id)) is None:
                session.add(
                    _ReportAssociationEntity(
                        report_id=str(report_id),
                        class_id=class_id,
                        report_created_at=report_created_at,
                        report_version=report_version,
                    )
                )

            # widen the class's seen-instant cache to cover the report
            entity = session.get(_EquivalenceClassEntity, class_id)
            if entity is not None:
                entity.first_seen_at = min(entity.first_seen_at, self._as_utc(report_created_at))
                entity.last_seen_at = max(entity.last_seen_at, self._as_utc(report_created_at))

    def associated_report_ids(self, report_ids: Collection[UUID]) -> set[UUID]:
        with self._session_factory() as session:
            # query in chunks, as each id is a bind variable and SQLite caps these per statement
            id_strings = [str(report_id) for report_id in report_ids]
            found: set[UUID] = set()
            for start in range(0, len(id_strings), self._ID_QUERY_CHUNK_SIZE):
                chunk = id_strings[start : start + self._ID_QUERY_CHUNK_SIZE]
                rows = session.scalars(
                    select(_ReportAssociationEntity.report_id).where(_ReportAssociationEntity.report_id.in_(chunk))
                ).all()
                found.update(UUID(row) for row in rows)
            return found

    def member_report_ids(self, class_id: int, limit: int | None = None, newest_first: bool = True) -> list[tuple[UUID, datetime]]:
        with self._session_factory() as session:
            order = _ReportAssociationEntity.report_created_at.desc() if newest_first else _ReportAssociationEntity.report_created_at.asc()
            statement = select(_ReportAssociationEntity).where(_ReportAssociationEntity.class_id == class_id).order_by(order)
            if limit is not None:
                statement = statement.limit(limit)
            return [(UUID(entity.report_id), entity.report_created_at) for entity in session.scalars(statement).all()]

    def get_class(self, class_id: int) -> EquivalenceClassRecord | None:
        with self._session_factory() as session:
            entity = session.get(_EquivalenceClassEntity, class_id)
            return entity.to_record() if entity is not None else None

    def list_class_overviews(self, count_since: datetime | None = None, version: str | None = None) -> list[ClassOverview]:
        with self._session_factory() as session:
            report_counts = self._report_counts(session, count_since, version)
            insight_counts = self._insight_counts(session)

            # assemble overviews for all classes with a non-zero report count, ordered by count descending
            restricted = count_since is not None or version is not None
            overviews = [
                ClassOverview(
                    equivalence_class=entity.to_record(),
                    report_count=report_counts.get(entity.id, 0),
                    insight_count=insight_counts.get(entity.id, 0),
                )
                for entity in session.scalars(select(_EquivalenceClassEntity)).all()
                if not restricted or report_counts.get(entity.id, 0) > 0
            ]
            return sorted(overviews, key=lambda o: o.report_count, reverse=True)

    def list_report_versions(self, since: datetime | None = None) -> list[str]:
        with self._session_factory() as session:
            statement = (
                select(_ReportAssociationEntity.report_version).where(_ReportAssociationEntity.report_version.is_not(None)).distinct()
            )
            if since is not None:
                statement = statement.where(_ReportAssociationEntity.report_created_at > since)
            versions = [version for version in session.scalars(statement).all() if version is not None]
            return sorted(versions, key=self._version_sort_key, reverse=True)

    def add_insight(self, class_id: int, analyzed_report_id: UUID, markdown: str, author: str | None = None) -> InsightRecord:
        with self._session_factory.begin() as session:
            entity = _InsightEntity(
                class_id=class_id,
                analyzed_report_id=str(analyzed_report_id),
                markdown=markdown,
                author=author,
                created_at=datetime.now(UTC),
            )
            session.add(entity)
            session.flush()
            return entity.to_record()

    def set_triage(self, class_id: int, triage: ClassTriage) -> EquivalenceClassRecord:
        with self._session_factory.begin() as session:
            entity = session.get(_EquivalenceClassEntity, class_id)
            if entity is None:
                raise KeyError(f"No equivalence class with id {class_id}")
            entity.apply_triage(triage)
            session.flush()
            return entity.to_record()

    def list_insights(self, class_id: int) -> list[InsightRecord]:
        with self._session_factory() as session:
            statement = select(_InsightEntity).where(_InsightEntity.class_id == class_id).order_by(_InsightEntity.created_at.asc())
            return [entity.to_record() for entity in session.scalars(statement).all()]

    @staticmethod
    def _report_counts(session: Session, count_since: datetime | None, version: str | None = None) -> dict[int, int]:
        """
        :param session: the session to query in
        :param count_since: if given, only reports created after this instant are counted
        :param version: if given, only reports originating from this deployment version are counted
        :return: a mapping from class identifier to the number of associated reports
        """
        statement: Any = select(_ReportAssociationEntity.class_id, func.count()).group_by(_ReportAssociationEntity.class_id)
        if count_since is not None:
            statement = statement.where(_ReportAssociationEntity.report_created_at > count_since)
        if version is not None:
            statement = statement.where(_ReportAssociationEntity.report_version == version)
        return {row[0]: row[1] for row in session.execute(statement)}

    @staticmethod
    def _insight_counts(session: Session) -> dict[int, int]:
        """
        :param session: the session to query in
        :return: a mapping from class identifier to the number of stored insights
        """
        statement = select(_InsightEntity.class_id, func.count()).group_by(_InsightEntity.class_id)
        return {row[0]: row[1] for row in session.execute(statement)}

    @staticmethod
    def _version_sort_key(version: str) -> list[tuple[int, int | str]]:
        """
        :param version: the version designation to derive a sort key from
        :return: a key ordering version designations naturally, i.e. comparing embedded numbers
            numerically rather than lexicographically (2.10.0 thus ranking above 2.9.1)
        """
        return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", version) if part]

    @staticmethod
    def _as_utc(instant: datetime) -> datetime:
        """
        :param instant: the instant to convert; a naive value is interpreted as UTC
        :return: the instant as a timezone-aware UTC value
        """
        if instant.tzinfo is None:
            return instant.replace(tzinfo=UTC)
        return instant.astimezone(UTC)
