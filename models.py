from sqlalchemy import Column, Integer, String, ForeignKey, DateTime,Boolean
from database import Base
from datetime import datetime,date

class Parent(Base):
    __tablename__ = "parents"

    id = Column(Integer, primary_key=True)
    line_user_id = Column(String, unique=True, index=True)


class Child(Base):
    __tablename__ = "children"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey("parents.id"), unique=True)
    device_id = Column(String, unique=True, index=True)
    default_limit = Column(Integer, default=1800)
    pair_code=Column(String,unique=True)
    target=Column(String,default="nothing")


class Control(Base):
    __tablename__ = "controls"

    id = Column(Integer, primary_key=True)
    child_id = Column(Integer, ForeignKey("children.id"))
    start_time = Column(DateTime, default=datetime.utcnow)
    used_time = Column(Integer, default=0)
    limit = Column(Integer)
    running = Column(Boolean, default=False)
    dates=Column(String,default=str(date.today))
