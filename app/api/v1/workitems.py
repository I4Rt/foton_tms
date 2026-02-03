from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import WorkItem, Project, ProjectMember, User
from app.models.enums import UserRole, WorkItemType, WorkItemState
from app.schemas.schemas import WorkItemCreate, WorkItemUpdate, WorkItemResponse

router = APIRouter(prefix="/projects/{project_id}/workitems", tags=["Work Items"])

async def check_project_access(project_id: UUID, user: User, db: AsyncSession):
    """Check if user has access to project."""
    if user.role == UserRole.ADMINISTRATOR:
        return True
    
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied to project")
    return True

@router.get("", response_model=List[WorkItemResponse])
async def list_work_items(
    project_id: UUID,
    type: Optional[WorkItemType] = Query(None),
    state: Optional[WorkItemState] = Query(None),
    assigned_to: Optional[UUID] = Query(None),
    iteration_id: Optional[UUID] = Query(None),
    parent_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List work items with filters."""
    await check_project_access(project_id, current_user, db)
    
    query = select(WorkItem).where(WorkItem.project_id == project_id)
    
    if type:
        query = query.where(WorkItem.type == type)
    if state:
        query = query.where(WorkItem.state == state)
    if assigned_to:
        query = query.where(WorkItem.assigned_to == assigned_to)
    if iteration_id:
        query = query.where(WorkItem.iteration_id == iteration_id)
    if parent_id:
        query = query.where(WorkItem.parent_id == parent_id)
    
    result = await db.execute(query.order_by(WorkItem.created_date.desc()))
    return result.scalars().all()

@router.get("/{work_item_id}", response_model=WorkItemResponse)
async def get_work_item(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get work item details."""
    await check_project_access(project_id, current_user, db)
    
    result = await db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.project_id == project_id
        )
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    return work_item

@router.get("/{work_item_id}/children", response_model=List[WorkItemResponse])
async def get_work_item_children(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get child work items."""
    await check_project_access(project_id, current_user, db)
    
    result = await db.execute(
        select(WorkItem).where(WorkItem.parent_id == work_item_id)
    )
    return result.scalars().all()

@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
async def create_work_item(
    project_id: UUID,
    work_item_data: WorkItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Create new work item."""
    await check_project_access(project_id, current_user, db)
    
    # Validate parent hierarchy
    if work_item_data.parent_id:
        parent_result = await db.execute(
            select(WorkItem).where(WorkItem.id == work_item_data.parent_id)
        )
        parent = parent_result.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parent not found")
        
        # Validate hierarchy rules
        valid_parents = {
            WorkItemType.FEATURE: WorkItemType.EPIC,
            WorkItemType.USER_STORY: WorkItemType.FEATURE,
            WorkItemType.TASK: WorkItemType.USER_STORY
        }
        
        if work_item_data.type in valid_parents:
            if parent.type != valid_parents[work_item_data.type]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{work_item_data.type.value} must have {valid_parents[work_item_data.type].value} as parent"
                )
        elif work_item_data.type != WorkItemType.EPIC:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{work_item_data.type.value} must have a parent"
            )
    
    work_item = WorkItem(
        project_id=project_id,
        type=work_item_data.type,
        title=work_item_data.title,
        description=work_item_data.description,
        priority=work_item_data.priority,
        parent_id=work_item_data.parent_id,
        assigned_to=work_item_data.assigned_to,
        iteration_id=work_item_data.iteration_id,
        estimation_hours=work_item_data.estimation_hours,
        remaining_hours=work_item_data.estimation_hours,
        tags=work_item_data.tags,
        created_by=current_user.id
    )
    
    db.add(work_item)
    await db.flush()
    await db.refresh(work_item)
    
    logger.info(f"Work item created: {work_item.title} by {current_user.email}")
    return work_item

@router.patch("/{work_item_id}", response_model=WorkItemResponse)
async def update_work_item(
    project_id: UUID,
    work_item_id: UUID,
    work_item_data: WorkItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update work item."""
    await check_project_access(project_id, current_user, db)
    
    result = await db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.project_id == project_id
        )
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    
    update_data = work_item_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(work_item, field, value)
    
    await db.flush()
    await db.refresh(work_item)
    
    logger.info(f"Work item updated: {work_item.title} by {current_user.email}")
    return work_item

@router.delete("/{work_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_work_item(
    project_id: UUID,
    work_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Delete work item."""
    await check_project_access(project_id, current_user, db)
    
    result = await db.execute(
        select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.project_id == project_id
        )
    )
    work_item = result.scalar_one_or_none()
    if not work_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work item not found")
    
    await db.delete(work_item)
    await db.flush()
    
    logger.info(f"Work item deleted: {work_item.title} by {current_user.email}")
