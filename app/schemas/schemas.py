from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.models.enums import (
    UserRole, WorkItemType, WorkItemState, Priority,
    IterationState, NonWorkingDayType
)


# ===== User Schemas =====

class UserBase(BaseModel):
    email: EmailStr
    display_name: str = Field(..., min_length=1, max_length=255)
    avatar_url: Optional[str] = None
    capacity_per_day: Decimal = Field(default=Decimal("8.0"), gt=0, le=24)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8)
    role: UserRole = UserRole.EXECUTOR


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar_url: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    capacity_per_day: Optional[Decimal] = Field(None, gt=0, le=24)


class UserResponse(UserBase):
    id: UUID
    role: UserRole
    is_active: bool
    created_date: datetime

    class Config:
        from_attributes = True


class UserMeResponse(UserResponse):
    pass


# ===== Project Schemas =====

class ProjectBase(BaseModel):
    name: str = Field(..., min_length=3, max_length=200)
    description: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=200)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class ProjectResponse(ProjectBase):
    id: UUID
    created_by: UUID
    created_date: datetime
    is_active: bool
    member_count: int = 0

    class Config:
        from_attributes = True


# ===== Project Member Schemas =====

class ProjectMemberAdd(BaseModel):
    user_id: UUID


class ProjectMemberResponse(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    role: UserRole
    avatar_url: Optional[str]
    capacity_per_day: Decimal
    added_date: datetime

    class Config:
        from_attributes = True


# ===== Iteration Schemas =====

class IterationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    start_date: date
    end_date: date
    goal: Optional[str] = None
    working_days: List[date] = Field(default_factory=list)

    @field_validator("end_date")
    @classmethod
    def validate_end_date(cls, v, info):
        if "start_date" in info.data and v <= info.data["start_date"]:
            raise ValueError("end_date must be greater than start_date")
        return v


class IterationCreate(IterationBase):
    pass


class IterationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    state: Optional[IterationState] = None
    goal: Optional[str] = None
    working_days: Optional[List[date]] = None


class IterationResponse(IterationBase):
    id: UUID
    project_id: UUID
    state: IterationState
    created_date: datetime

    class Config:
        from_attributes = True


# ===== Work Item Schemas =====

class WorkItemBase(BaseModel):
    type: WorkItemType
    title: str = Field(..., min_length=3, max_length=500)
    description: Optional[str] = None
    priority: Priority = Priority.MEDIUM
    tags: List[str] = Field(default_factory=list)


class WorkItemCreate(WorkItemBase):
    parent_id: Optional[UUID] = None
    assigned_to: Optional[UUID] = None
    iteration_id: Optional[UUID] = None
    estimation_hours: Optional[Decimal] = Field(None, gt=0)
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @field_validator("estimation_hours")
    @classmethod
    def validate_estimation(cls, v, info):
        if info.data.get("type") == WorkItemType.TASK and v is None:
            raise ValueError("estimation_hours is required for Task")
        return v


class WorkItemUpdate(BaseModel):
    type: Optional[WorkItemType] = None
    title: Optional[str] = Field(None, min_length=3, max_length=500)
    description: Optional[str] = None
    state: Optional[WorkItemState] = None
    parent_id: Optional[UUID] = None
    priority: Optional[Priority] = None
    assigned_to: Optional[UUID] = None
    iteration_id: Optional[UUID] = None
    estimation_hours: Optional[Decimal] = Field(None, gt=0)
    tags: Optional[List[str]] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class WorkItemResponse(WorkItemBase):
    id: UUID
    project_id: UUID
    state: WorkItemState
    assigned_to: Optional[UUID]
    created_by: UUID
    created_date: datetime
    modified_date: datetime
    parent_id: Optional[UUID]
    iteration_id: Optional[UUID]
    estimation_hours: Optional[Decimal]
    completed_hours: Decimal = Decimal(0)
    remaining_hours: Optional[Decimal] = None
    start_date: Optional[date]
    end_date: Optional[date]

    class Config:
        from_attributes = True


# ===== Work Session Schemas =====

class WorkSessionCreate(BaseModel):
    description: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None

    @field_validator("ended_at")
    @classmethod
    def validate_ended_at(cls, v, info):
        if v is not None and "started_at" in info.data and v <= info.data["started_at"]:
            raise ValueError("ended_at must be after started_at")
        return v


class WorkSessionUpdate(BaseModel):
    description: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class WorkSessionResponse(BaseModel):
    id: UUID
    work_item_id: UUID
    user_id: UUID
    description: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    total_hours: Optional[Decimal]
    created_date: datetime

    class Config:
        from_attributes = True


class WorkSessionsByDayResponse(BaseModel):
    date: date
    sessions: List[WorkSessionResponse]
    total_hours: Decimal


# ===== Drop Plan Schemas =====

class DropPlanTaskResponse(BaseModel):
    """Single task in the drop plan grid."""
    id: UUID
    title: str
    state: WorkItemState
    priority: Priority
    estimation_hours: Optional[Decimal]
    completed_hours: Decimal = Decimal(0)
    remaining_hours: Optional[Decimal] = None
    start_date: date
    end_date: date
    parent_id: Optional[UUID]
    parent_title: Optional[str] = None
    tags: List[str]

    class Config:
        from_attributes = True


class DropPlanMemberInfo(BaseModel):
    """Member info for drop plan row."""
    user_id: UUID
    display_name: str
    email: str
    avatar_url: Optional[str]
    capacity_per_day: Decimal


class DropPlanUserRow(BaseModel):
    """One row in the drop plan: user + their tasks."""
    member: DropPlanMemberInfo
    tasks: List[DropPlanTaskResponse]
    total_estimation: Decimal
    total_completed: Decimal


class DropPlanSprintResponse(BaseModel):
    """Full sprint drop plan overview."""
    iteration_id: UUID
    iteration_name: str
    state: IterationState
    start_date: date
    end_date: date
    working_days: List[date]
    members: List[DropPlanMemberInfo]
    total_tasks: int
    total_estimation_hours: Decimal
    total_completed_hours: Decimal


class DropPlanUserResponse(BaseModel):
    """User's tasks within a sprint."""
    iteration_id: UUID
    iteration_name: str
    start_date: date
    end_date: date
    working_days: List[date]
    member: Optional[DropPlanMemberInfo] = None
    tasks: List[DropPlanTaskResponse]
    total_estimation: Decimal
    total_completed: Decimal


class DropPlanTaskMove(BaseModel):
    """Move a task within the sprint."""
    start_date: date
    end_date: date


class CapacityByDay(BaseModel):
    date: date
    total_capacity: Decimal
    total_planned: Decimal
    is_overcommitted: bool


class CapacityByUser(BaseModel):
    user_id: UUID
    display_name: str
    total_capacity: Decimal
    total_planned: Decimal
    utilization_percent: Decimal


class IterationCapacityResponse(BaseModel):
    iteration_id: UUID
    total_capacity: Decimal
    total_planned: Decimal
    utilization_percent: Decimal
    is_overcommitted: bool
    capacity_by_day: List[CapacityByDay]
    capacity_by_user: List[CapacityByUser]


# ===== Calendar Schemas =====

class HolidayCreate(BaseModel):
    date: date
    description: Optional[str] = None


class HolidayResponse(BaseModel):
    id: UUID
    date: date
    description: Optional[str]

    class Config:
        from_attributes = True


class NonWorkingDayCreate(BaseModel):
    date: date
    type: NonWorkingDayType = NonWorkingDayType.PERSONAL_LEAVE
    description: Optional[str] = None


class NonWorkingDayResponse(BaseModel):
    id: UUID
    user_id: UUID
    date: date
    type: NonWorkingDayType
    description: Optional[str]
    created_date: datetime

    class Config:
        from_attributes = True


# ===== Error Schema =====

class ErrorResponse(BaseModel):
    error: dict = Field(..., example={
        "code": "VALIDATION_ERROR",
        "message": "Invalid input data",
        "details": "Field 'name' is required",
        "timestamp": "2026-02-02T16:50:00Z"
    })
