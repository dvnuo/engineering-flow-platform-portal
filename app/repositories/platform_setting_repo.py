import json

from sqlalchemy.orm import Session

from app.models.platform_setting import PlatformSetting


class PlatformSettingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, key: str) -> PlatformSetting | None:
        return self.db.get(PlatformSetting, key)

    def get_value(self, key: str, default: dict | None = None) -> dict:
        record = self.get(key)
        if record is None:
            return dict(default or {})
        try:
            parsed = json.loads(record.value_json or "{}")
        except (TypeError, ValueError):
            return dict(default or {})
        return parsed if isinstance(parsed, dict) else dict(default or {})

    def set_value(self, key: str, value: dict, *, updated_by_user_id: int | None = None) -> PlatformSetting:
        record = self.get(key)
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if record is None:
            record = PlatformSetting(key=key, value_json=payload, updated_by_user_id=updated_by_user_id)
        else:
            record.value_json = payload
            record.updated_by_user_id = updated_by_user_id
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record
