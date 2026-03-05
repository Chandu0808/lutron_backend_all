import json
from sqlalchemy.orm import Session
from app.models.home import HomePageContent

def upsert_home_page_content(page: str, content: dict, db: Session):
    for item, value in content.items():
        # Store lists as JSON array of objects
        if isinstance(value, list):
            value = json.dumps([{"solution": v} for v in value])

        existing = db.query(HomePageContent).filter_by(page=page, item=item).first()
        if existing:
            existing.value = value
        else:
            new_entry = HomePageContent(page=page, item=item, value=value)
            db.add(new_entry)

    db.flush()
    db.commit()
    return get_home_page_content_by_page(page, db)


def get_home_page_content_by_page(page: str, db: Session):
    results = db.query(HomePageContent).filter(HomePageContent.page == page).all()
    content = {}
    for r in results:
        try:
            parsed = json.loads(r.value)
            content[r.item] = parsed
        except:
            content[r.item] = r.value
    return content
