from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.robot import Robot
from typing import Optional


class RobotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, **kwargs) -> Robot:
        robot = Robot(**kwargs)
        self.db.add(robot)
        self.db.commit()
        self.db.refresh(robot)
        return robot

    def list_by_owner(self, owner_user_id: int) -> list[Robot]:
        return list(
            self.db.scalars(select(Robot).where(Robot.owner_user_id == owner_user_id).order_by(Robot.created_at.desc())).all()
        )

    def list_public(self) -> list[Robot]:
        return list(
            self.db.scalars(select(Robot).where(Robot.visibility == "public").order_by(Robot.created_at.desc())).all()
        )

    def list_all(self) -> list[Robot]:
        return list(self.db.scalars(select(Robot).order_by(Robot.created_at.desc())).all())

    def get_by_id(self, robot_id: str) -> Optional[Robot]:
        return self.db.get(Robot, robot_id)

    def save(self, robot: Robot) -> Robot:
        self.db.add(robot)
        self.db.commit()
        self.db.refresh(robot)
        return robot

    def delete(self, robot: Robot) -> None:
        self.db.delete(robot)
        self.db.commit()
