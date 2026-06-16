from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.dependencies.auth import get_current_user
from app.dependencies.permissions import require_operator_permission_for_scope
from app.models.user_model import User


def require_superadmin(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    """Restrict mutation to Superadmin only (required_level=5)."""
    require_operator_permission_for_scope(
        required_level=5,
        area_ids=None,
        floor_ids=None,
        enforce_on_empty_scope=False,
        db=db,
        current_user=current_user,
    )
    return current_user
