from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.core.logging import logger
from app.models.models import Iteration, WorkItem, ProjectMember, User
from app.models.enums import UserRole
from app.schemas.schemas import IterationCreate, IterationUpdate, IterationResponse, WorkItemResponse

router = APIRouter(prefix="/projects/{project_id}/iterations", tags=["Iterations"])

@router.get("", response_model=List[IterationResponse])
async def list_iterations(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List project iterations."""
    result = await db.execute(
        select(Iteration)
        .where(Iteration.project_id == project_id)
        .order_by(Iteration.start_date.desc())
    )
    return result.scalars().all()

@router.get("/{iteration_id}", response_model=IterationResponse)
async def get_iteration(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get iteration details."""
    result = await db.execute(
        select(Iteration).where(
            Iteration.id == iteration_id,
            Iteration.project_id == project_id
        )
    )
    iteration = result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")
    return iteration

@router.post("", response_model=IterationResponse, status_code=status.HTTP_201_CREATED)
async def create_iteration(
    project_id: UUID,
    iteration_data: IterationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Create new iteration."""
    iteration = Iteration(
        project_id=project_id,
        name=iteration_data.name,
        start_date=iteration_data.start_date,
        end_date=iteration_data.end_date,
        goal=iteration_data.goal,
        working_days=[d.isoformat() for d in iteration_data.working_days]
    )
    
    db.add(iteration)
    await db.flush()
    await db.refresh(iteration)
    
    logger.info(f"Iteration created: {iteration.name} by {current_user.email}")
    return iteration

@router.patch("/{iteration_id}", response_model=IterationResponse)
async def update_iteration(
    project_id: UUID,
    iteration_id: UUID,
    iteration_data: IterationUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Update iteration."""
    result = await db.execute(
        select(Iteration).where(
            Iteration.id == iteration_id,
            Iteration.project_id == project_id
        )
    )
    iteration = result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")
    
    update_data = iteration_data.model_dump(exclude_unset=True)
    if "working_days" in update_data and update_data["working_days"]:
        update_data["working_days"] = [d.isoformat() for d in update_data["working_days"]]
    
    for field, value in update_data.items():
        setattr(iteration, field, value)
    
    await db.flush()
    await db.refresh(iteration)
    
    logger.info(f"Iteration updated: {iteration.name} by {current_user.email}")
    return iteration

@router.delete("/{iteration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_iteration(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.MANAGER, UserRole.ADMINISTRATOR))
):
    """Delete iteration."""
    result = await db.execute(
        select(Iteration).where(
            Iteration.id == iteration_id,
            Iteration.project_id == project_id
        )
    )
    iteration = result.scalar_one_or_none()
    if not iteration:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Iteration not found")
    
    await db.delete(iteration)
    await db.flush()
    
    logger.info(f"Iteration deleted: {iteration.name} by {current_user.email}")

@router.get("/{iteration_id}/workitems", response_model=List[WorkItemResponse])
async def get_iteration_work_items(
    project_id: UUID,
    iteration_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get work items assigned to iteration."""
    result = await db.execute(
        select(WorkItem).where(WorkItem.iteration_id == iteration_id)
    )
    return result.scalars().all()
