import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, BackgroundTasks
import uuid
import asyncio
from enum import Enum
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from ForkTestProxyForTG.src.proxy_checker.httpx_service import main

app = FastAPI()


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    created_at: datetime
    completed_at: Optional[datetime] = None
    details: Optional[str] = None
    error: bool = False


tasks_store: dict[str, TaskInfo] = {}


def update_proxies_background(task_id: str):
    try:
        tasks_store[task_id].status = TaskStatus.PROCESSING
        tasks_store[task_id].details = "Your request is in progress"
        asyncio.run(main(
            bot_url=f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/getMe",
            proxy_list_url=os.getenv("PROXY_LIST_URL"),
        ))
        tasks_store[task_id].status = TaskStatus.COMPLETED
        tasks_store[task_id].completed_at = datetime.now()
        tasks_store[task_id].details = "Successfully loaded proxy list"

    except Exception as e:
        tasks_store[task_id].status = TaskStatus.FAILED
        tasks_store[task_id].completed_at = datetime.now()

        tasks_store[task_id].error = True,
        tasks_store[task_id].details = str(e)


def get_last_completed_task() -> Optional[TaskInfo]:
    completed_tasks = [
        task for task in tasks_store.values()
        if task.status == TaskStatus.COMPLETED and task.completed_at is not None
    ]

    if not completed_tasks:
        return None

    return max(completed_tasks, key=lambda t: t.completed_at)


def can_start_new_task(cooldown_seconds: int = 90) -> tuple[bool, Optional[int]]:
    last_task = get_last_completed_task()

    if last_task is None:
        return True, None

    now = datetime.now()
    time_since_completion = (now - last_task.completed_at).total_seconds()

    if time_since_completion >= cooldown_seconds:
        return True, None

    remaining = int(cooldown_seconds - time_since_completion)
    return False, remaining


@app.get("/update_proxies")
async def update_proxies(background_tasks: BackgroundTasks):
    active_task = next(
        (t for t in tasks_store.values() if t.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)),
        None
    )
    if active_task:
        return {
            "error": True,
            "status": "failed",
            "details": f"Failed to process request: already processing another request. "
                       f"Current task id is {active_task.task_id}. "
                       f"You can see task's status in this page: http://127.0.0.1:8000/status/{active_task.task_id}",
        }
    task_readiness = can_start_new_task(cooldown_seconds=90)
    if not task_readiness[0]:
        return {
            "error": True,
            "status": "failed",
            "details": f"Failed to process request: too many requests; try again after {task_readiness[1]} seconds",
        }
    task_id = str(uuid.uuid4())

    tasks_store[task_id] = TaskInfo(
        task_id=task_id,
        status=TaskStatus.PENDING,
        created_at=datetime.now(),
        completed_at=None,
        details=None,
        error=False
    )

    background_tasks.add_task(update_proxies_background, task_id)

    return {
        "error": False,
        "status": "processing",
        "details": f"Your request is in progress. Please wait for an answer. Current task id is {task_id}. "
                   f"You can see task's status in this page: http://127.0.0.1:8000/status/{task_id}"
    }


@app.get("/status/{task_id}")
async def update_proxies(task_id: str):
    task = tasks_store.get(task_id)

    if not task:
        return {
            "error": True,
            "status": "failed",
            "details": "Failed to get request: no request with that task id",
        }

    return {
        "task_id": task.task_id,
        "status": task.status,
        "details": task.details,
        "created_at": task.created_at,
        "completed_at": task.completed_at
    }


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8000)
