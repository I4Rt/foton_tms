import enum

class UserRole(str, enum.Enum):
    ADMINISTRATOR = "Administrator"
    MANAGER = "Manager"
    EXECUTOR = "Executor"

class WorkItemType(str, enum.Enum):
    EPIC = "Epic"
    FEATURE = "Feature"
    USER_STORY = "UserStory"
    TASK = "Task"

class WorkItemState(str, enum.Enum):
    NEW = "New"
    ACTIVE = "Active"
    IN_PROGRESS = "InProgress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"
    REMOVED = "Removed"

class Priority(str, enum.Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

class IterationState(str, enum.Enum):
    FUTURE = "Future"
    CURRENT = "Current"
    PAST = "Past"

class NonWorkingDayType(str, enum.Enum):
    PERSONAL_LEAVE = "PersonalLeave"
    VACATION = "Vacation"
    SICK = "Sick" 