"""Durable Vercel Queue worker for Agnes video pipelines."""
from __future__ import annotations
import logging
from core import supabase_store
from core.config import get_api_key
from core.task_manager import TaskManager
from models.task import StepStatus
from web import app_state, deps
from vercel.queue import subscribe

logger = logging.getLogger(__name__)
AGNES_QUEUE_TOPIC = "agnes-video-tasks"

async def process_agnes_task(message: dict) -> None:
    task_id = str(message.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("Queue message is missing task_id")
    durable = supabase_store.get_task(task_id)
    if not durable:
        raise RuntimeError(f"Durable task {task_id} was not found")
    config = durable.get("config") or {}
    dir_name = str(config.get("dir_name") or task_id)
    tm = TaskManager(task_id, dir_name=dir_name)
    state = tm.load()
    if state is None:
        raise RuntimeError(f"Unable to hydrate task {task_id} from Supabase")
    if state.status in (StepStatus.COMPLETED, StepStatus.FAILED):
        logger.info("[Queue] Task %s already terminal; acknowledging duplicate", task_id)
        return
    if not supabase_store.claim_task(task_id):
        latest = supabase_store.get_task(task_id) or {}
        latest_status = str(latest.get("status") or "")
        if latest_status in ("running", "completed", "failed"):
            logger.info("[Queue] Task %s already claimed/status=%s; acknowledging duplicate", task_id, latest_status)
            return
        raise RuntimeError(f"Unable to claim durable task {task_id}")
    state.status = StepStatus.RUNNING
    input_files = config.get("input_files") or {}
    try:
        if not supabase_store.input_manifest_complete(state, input_files):
            tm.update_state(
                status=StepStatus.FAILED,
                current_status="failed",
                current_message="No se pudieron restaurar todos los archivos de entrada.",
            )
            return
        supabase_store.download_input_files(task_id, input_files)
        api_key = get_api_key()
        if not api_key:
            tm.update_state(status=StepStatus.FAILED, current_status="failed",
                            current_message="AGNES API key is not configured")
            return
        pipeline = deps.create_pipeline_for_type(state.task_type, api_key, task_id, dir_name)
        app_state.active_pipelines[task_id] = pipeline
        logger.info("[Queue] Starting durable Agnes task %s", task_id)
        await deps.run_pipeline_with_concurrency(pipeline, state, tm)
        logger.info("[Queue] Finished durable Agnes task %s", task_id)
    except Exception:
        # Queue is at-least-once. If the function itself fails after claiming,
        # release the durable claim so the redelivery can execute the task.
        supabase_store.release_task_claim(task_id)
        raise

@subscribe(topic=AGNES_QUEUE_TOPIC, consumer_group="agnes-video-worker",
            retry_after=900, max_concurrency=1, max_attempts=5)
async def handle_agnes_task(message: dict) -> None:
    await process_agnes_task(message)
