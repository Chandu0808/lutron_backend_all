# from fastapi import APIRouter, Depends, HTTPException, Query
# from sqlalchemy.orm import Session
# from app.database.session import get_db
# from app.dependencies.auth import get_current_user
# from app.crud.area_tree import get_area_tree_by_floor

# router = APIRouter()


# @router.get("/tree/{floor_id}")
# def get_area_tree(floor_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
#     try:
#         tree = get_area_tree_by_floor(db, floor_id)
#         return {"status": "success", "floor_id": floor_id, "tree": tree}
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
