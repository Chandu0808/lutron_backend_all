from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.database.session import get_db
from app.models.user_model import User
from app.core.security import decode_access_token
from app.core.security import ALGORITHM  # still imported in case needed later

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(get_db)
):
    """
    Dependency to extract user from JWT token.
    Validates the token and retrieves user from DB.
    """
    token = credentials.credentials

    payload = decode_access_token(token)
    email = payload.get("sub")
    if email is None:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.query(User).filter(
        User.email == email,
        User.is_active == True
    ).first()
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def require_admin(user: User = Depends(get_current_user)):
    """
    Dependency to restrict access to Superadmin role only.
    """
    if user.role != "Superadmin":
        raise HTTPException(status_code=403, detail="Admin only access")
    return user
