from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
import uuid
from sqlalchemy import (
    String, Boolean, DECIMAL, Text, ForeignKey, Date, 
    CheckConstraint, Index, UniqueConstraint, TIMESTAMP
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET
from app.core.database import Base
from app.models.enums import (
    UserRole, WorkItemType, WorkItemState, Priority, 
    IterationState, NonWorkingDayType
)

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500))
    role: Mapped[UserRole] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    capacity_per_day: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), default=8.0, nullable=False)
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)
    modified_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    created_projects: Mapped[List["Project"]] = relationship(back_populates="creator", foreign_keys="Project.created_by")
    project_memberships: Mapped[List["ProjectMember"]] = relationship(back_populates="user", foreign_keys="ProjectMember.user_id")
    assigned_work_items: Mapped[List["WorkItem"]] = relationship(back_populates="assignee", foreign_keys="WorkItem.assigned_to")
    non_working_days: Mapped[List["NonWorkingDay"]] = relationship(back_populates="user")

    __table_args__ = (
        CheckConstraint("capacity_per_day > 0 AND capacity_per_day <= 24", name="chk_capacity_range"),
        Index("idx_users_email", "email"),
        Index("idx_users_role", "role"),
    )

class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)
    modified_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    creator: Mapped["User"] = relationship(back_populates="created_projects", foreign_keys=[created_by])
    members: Mapped[List["ProjectMember"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    iterations: Mapped[List["Iteration"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    work_items: Mapped[List["WorkItem"]] = relationship(back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(name) >= 3", name="chk_project_name_length"),
        Index("idx_projects_created_by", "created_by"),
        Index("idx_projects_is_active", "is_active"),
    )

class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    added_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    added_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="project_memberships", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_project_members_user_id", "user_id"),
        Index("idx_project_members_project_id", "project_id"),
    )

class Iteration(Base):
    __tablename__ = "iterations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[IterationState] = mapped_column(String(50), default=IterationState.FUTURE, nullable=False)
    goal: Mapped[Optional[str]] = mapped_column(Text)
    working_days: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)
    modified_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="iterations")
    work_items: Mapped[List["WorkItem"]] = relationship(back_populates="iteration")
    drop_plan_items: Mapped[List["DropPlanItem"]] = relationship(back_populates="iteration", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("end_date > start_date", name="chk_iteration_dates"),
        Index("idx_iterations_project_id", "project_id"),
        Index("idx_iterations_state", "state"),
    )

class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[WorkItemType] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[WorkItemState] = mapped_column(String(50), default=WorkItemState.NEW, nullable=False)
    priority: Mapped[Priority] = mapped_column(String(50), default=Priority.MEDIUM, nullable=False)
    assigned_to: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)
    modified_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("work_items.id", ondelete="CASCADE"))
    iteration_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("iterations.id", ondelete="SET NULL"))
    estimation_hours: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2))
    remaining_hours: Mapped[Optional[Decimal]] = mapped_column(DECIMAL(10, 2))
    completed_hours: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), default=0, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="work_items")
    assignee: Mapped[Optional["User"]] = relationship(back_populates="assigned_work_items", foreign_keys=[assigned_to])
    iteration: Mapped[Optional["Iteration"]] = relationship(back_populates="work_items")
    parent: Mapped[Optional["WorkItem"]] = relationship(back_populates="children", remote_side=[id])
    children: Mapped[List["WorkItem"]] = relationship(back_populates="parent")
    drop_plan_items: Mapped[List["DropPlanItem"]] = relationship(back_populates="work_item", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(title) >= 3", name="chk_workitem_title_length"),
        CheckConstraint("estimation_hours > 0 OR estimation_hours IS NULL", name="chk_estimation_positive"),
        CheckConstraint("remaining_hours >= 0 OR remaining_hours IS NULL", name="chk_remaining_positive"),
        CheckConstraint("completed_hours >= 0", name="chk_completed_positive"),
        Index("idx_workitems_project_id", "project_id"),
        Index("idx_workitems_parent_id", "parent_id"),
        Index("idx_workitems_iteration_id", "iteration_id"),
        Index("idx_workitems_assigned_to", "assigned_to"),
        Index("idx_workitems_type", "type"),
        Index("idx_workitems_state", "state"),
    )

class DropPlanItem(Base):
    __tablename__ = "drop_plan_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    iteration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("iterations.id", ondelete="CASCADE"), nullable=False)
    work_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False)
    assigned_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    planned_date: Mapped[date] = mapped_column(Date, nullable=False)
    order_index: Mapped[int] = mapped_column(default=0, nullable=False)
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)
    modified_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    iteration: Mapped["Iteration"] = relationship(back_populates="drop_plan_items")
    work_item: Mapped["WorkItem"] = relationship(back_populates="drop_plan_items")

    __table_args__ = (
        UniqueConstraint("work_item_id", "iteration_id", name="uq_dropplan_workitem_iteration"),
        Index("idx_dropplan_iteration_id", "iteration_id"),
        Index("idx_dropplan_assigned_user_id", "assigned_user_id"),
        Index("idx_dropplan_planned_date", "planned_date"),
    )

class Holiday(Base):
    __tablename__ = "holidays"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("idx_holidays_date", "date"),)

class NonWorkingDay(Base):
    __tablename__ = "non_working_days"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[NonWorkingDayType] = mapped_column(String(50), default=NonWorkingDayType.PERSONAL_LEAVE, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    created_date: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="non_working_days")

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_nonworking_user_date"),
        Index("idx_nonworking_user_id", "user_id"),
        Index("idx_nonworking_date", "date"),
    )

class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    performed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), default=datetime.utcnow, nullable=False)
    changes: Mapped[Optional[dict]] = mapped_column(JSONB)
    ip_address: Mapped[Optional[str]] = mapped_column(INET)

    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_performed_by", "performed_by"),
        Index("idx_audit_performed_at", "performed_at"),
    )