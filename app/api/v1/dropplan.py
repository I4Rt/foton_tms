from typing import List
from uuid import UUID
from decimal import Decimal
from datetime import date
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import DropPlanItem, Iteration, WorkItem, User, ProjectMember
from app.models.enums import UserRole
from app.schemas.schemas import (
    DropPlanItemCreate, DropPlanItemUpdate, DropPlanItemResponse,
    UserDropPlanResponse, LoadByDay, IterationCapacityResponse,
    CapacityByDay, CapacityByUser
)

router = APIRouter(
    prefix="/projects/{project_id}/iterations/{iteration_id}/dropplan",
    tags=["Drop Plan"]
)

@router.get("/users/{user_id}", response_model=UserDropPlanResponse)
async def get_user_drop_plan(
    project_id: UUID,
    iteration_id: UUID,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get drop plan for specific user in iteration."""
    # Get iteration
    iter_result = await db.execute(
        select(Iteration).where(Iteration.id == iteration_id)
    )
    iteration = iter_result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")
    
    # Get user
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    # Get drop plan items
    items_result = await db.execute(
        select(DropPlanItem, WorkItem)
        .join(WorkItem, DropPlanItem.work_item_id == WorkItem.id)
        .where(
            DropPlanItem.iteration_id == iteration_id,
            DropPlanItem.assigned_user_id == user_id
        )
        .order_by(DropPlanItem.planned_date, DropPlanItem.order_index)
    )
    items = items_result.all()
    
    # Build planned items response
    planned_items = []
    load_by_day_data = defaultdict(Decimal)
    
    for dpi, wi in items:
        # Get parent title if exists
        parent_title = None
        if wi.parent_id:
            parent_result = await db.execute(
                select(WorkItem.title).where(WorkItem.id == wi.parent_id)
            )
            parent_title = parent_result.scalar_one_or_none()
        
        planned_items.append(DropPlanItemResponse(
            id=dpi.id,
            work_item_id=wi.id,
            work_item_title=wi.title,
            work_item_type=wi.type,
            work_item_state=wi.state,
            planned_date=dpi.planned_date,
            estimation_hours=wi.estimation_hours,
            remaining_hours=wi.remaining_hours,
            completed_hours=wi.completed_hours,
            priority=wi.priority,
            tags=wi.tags,
            parent_title=parent_title
        ))
        load_by_day_data[dpi.planned_date] += wi.estimation_hours or Decimal(0)
    
    # Build load by day
    working_days = [date.fromisoformat(d) if isinstance(d, str) else d for d in iteration.working_days]
    load_by_day = []
    for day in working_days:
        planned = load_by_day_data.get(day, Decimal(0))
        load_by_day.append(LoadByDay(
            date=day,
            capacity=user.capacity_per_day,
            planned=planned,
            is_overcommitted=planned > user.capacity_per_day
        ))
    
    return UserDropPlanResponse(
        iteration_id=iteration.id,
        iteration_name=iteration.name,
        start_date=iteration.start_date,
        end_date=iteration.end_date,
        working_days=working_days,
        user={
            "userId": str(user.id),
            "displayName": user.display_name,
            "avatarUrl": user.avatar_url,
            "capacityPerDay": float(user.capacity_per_day),
            "totalCapacity": float(user.capacity_per_day * len(working_days))
        },
        planned_items=planned_items,
        load_by_day=load_by_day
    )

@router.post("/items", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_drop_plan_item(
    project_id: UUID,
    iteration_id: UUID,
    item_data: DropPlanItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Add work item to drop plan."""
    # Validate work item exists
    wi_result = await db.execute(
        select(WorkItem).where(WorkItem.id == item_data.work_item_id)
    )
    work_item = wi_result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    
    # Check for duplicate
    existing = await db.execute(
        select(DropPlanItem).where(
            DropPlanItem.work_item_id == item_data.work_item_id,
            DropPlanItem.iteration_id == iteration_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Work item already in drop plan")
    
    drop_plan_item = DropPlanItem(
        iteration_id=iteration_id,
        work_item_id=item_data.work_item_id,
        assigned_user_id=item_data.assigned_user_id,
        planned_date=item_data.planned_date
    )
    db.add(drop_plan_item)
    await db.flush()
    await db.refresh(drop_plan_item)
    
    logger.info(f"Drop plan item created for work item {item_data.work_item_id}")
    
    return {
        "id": str(drop_plan_item.id),
        "workItemId": str(drop_plan_item.work_item_id),
        "assignedUserId": str(drop_plan_item.assigned_user_id),
        "plannedDate": drop_plan_item.planned_date.isoformat(),
        "order": drop_plan_item.order_index
    }

@router.patch("/items/{item_id}", response_model=dict)
async def update_drop_plan_item(
    project_id: UUID,
    iteration_id: UUID,
    item_id: UUID,
    item_data: DropPlanItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Update drop plan item (change date, user, order)."""
    result = await db.execute(
        select(DropPlanItem).where(DropPlanItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drop plan item not found")
    
    update_data = item_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)
    
    await db.flush()
    await db.refresh(item)
    
    logger.info(f"Drop plan item {item_id} updated")
    
    return {
        "id": str(item.id),
        "plannedDate": item.planned_date.isoformat(),
        "modifiedDate": item.modified_date.isoformat()
    }

@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_drop_plan_item(
    project_id: UUID,
    iteration_id: UUID,
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Remove work item from drop plan."""
    result = await db.execute(
        select(DropPlanItem).where(DropPlanItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drop plan item not found")
    
    await db.delete(item)
    await db.flush()
    
    logger.info(f"Drop plan item {item_id} deleted")

@router.get("/capacity", response_model=IterationCapacityResponse)
async def get_iteration_capacity(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get total capacity and load for iteration."""
    # Get iteration
    iter_result = await db.execute(
        select(Iteration).where(Iteration.id == iteration_id)
    )
    iteration = iter_result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")
    
    working_days = [date.fromisoformat(d) if isinstance(d, str) else d for d in iteration.working_days]
    num_days = len(working_days)
    
    # Get project members and their capacities
    members_result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id, User.is_active == True)
    )
    members = members_result.all()
    
    total_capacity = sum(user.capacity_per_day * num_days for _, user in members)
    
    # Get all drop plan items for iteration
    items_result = await db.execute(
        select(DropPlanItem, WorkItem)
        .join(WorkItem, DropPlanItem.work_item_id == WorkItem.id)
        .where(DropPlanItem.iteration_id == iteration_id)
    )
    items = items_result.all()
    
    # Calculate totals
    total_planned = sum(wi.estimation_hours or Decimal(0) for _, wi in items)
    
    # Capacity by day
    planned_by_day = defaultdict(Decimal)
    capacity_per_day_total = sum(user.capacity_per_day for _, user in members)
    
    for dpi, wi in items:
        planned_by_day[dpi.planned_date] += wi.estimation_hours or Decimal(0)
    
    capacity_by_day = [
        CapacityByDay(
            date=day,
            total_capacity=capacity_per_day_total,
            total_planned=planned_by_day.get(day, Decimal(0)),
            is_overcommitted=planned_by_day.get(day, Decimal(0)) > capacity_per_day_total
        )
        for day in working_days
    ]
    
    # Capacity by user
    planned_by_user = defaultdict(Decimal)
    for dpi, wi in items:
        planned_by_user[dpi.assigned_user_id] += wi.estimation_hours or Decimal(0)
    
    capacity_by_user = [
        CapacityByUser(
            user_id=user.id,
            display_name=user.display_name,
            total_capacity=user.capacity_per_day * num_days,
            total_planned=planned_by_user.get(user.id, Decimal(0)),
            utilization_percent=round(
                (planned_by_user.get(user.id, Decimal(0)) / (user.capacity_per_day * num_days) * 100)
                if user.capacity_per_day * num_days > 0 else Decimal(0), 2
            )
        )
        for _, user in members
    ]
    
    utilization = round((total_planned / total_capacity * 100) if total_capacity > 0 else Decimal(0), 2)
    
    return IterationCapacityResponse(
        iteration_id=iteration_id,
        total_capacity=total_capacity,
        total_planned=total_planned,
        utilization_percent=utilization,
        is_overcommitted=total_planned > total_capacity,
        capacity_by_day=capacity_by_day,
        capacity_by_user=capacity_by_user
    )
