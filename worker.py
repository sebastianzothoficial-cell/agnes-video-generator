"""Durable Vercel Queue worker for Agnes video pipelines."""
from __future__ import annotations
import asyncio
import contextlib
import logging
import os
from core import supabase_store
from core.config import get_api_key

from core.task_manager import TaskManager
from models.task import StepStatus
from web import app_state, deps
from vercel.queue import subscribe

logger = logging.getLogger(__name__)
AGNES_QUEUE_TOPIC = "agnes-video-tasks"
# Leave a safety margin below the current Hobby maximum of 300s. The pipeline
# checkpoints video IDs/files so a follow-up queue message can resume on a new
# invocation instead of relying on one long-lived function.
WORKER_BUDGET_SECONDS = max(
    60,
    min(int(os.getenv("AGNES_WORKER_BUDGET_SECONDS", "240")), 270),
)
WORKER_CONTINUATION_DELAY_SECONDS = max(
    1,
    min(int(os.getenv("AGNES_WORKER_CONTINUATION_DELAY_SECONDS", "5")), 60),
)

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
            logger.info(
                "[Queue] Task %s already claimed/status=%s; acknowledging duplicate",
                task_id, latest_status,
            )
            return
        raise RuntimeError(f"Unable to claim durable task {task_id}")

    state.status = StepStatus.RUNNING
    input_files = config.get("input_files") or {}
    budget_expired = False
    try:
        if not supabase_store.input_manifest_complete(state, input_files):
            tm.update_state(
                status=StepStatus.FAILED,
                current_status="failed",
                current_message="No se pudieron restaurar todos los archivos de entrada.",
            )
            return
        if not supabase_store.download_input_files(task_id, input_files, state=state):
            raise RuntimeError(f"Unable to restore durable input files for task {task_id}")
        api_key = get_api_key()
        if not api_key:
            tm.update_state(
                status=StepStatus.FAILED,
                current_status="failed",
                current_message="AGNES API key is not configured",
            )
            return
        pipeline = deps.create_pipeline_for_type(
            state.task_type,
            api_key,
            task_id,
            dir_name,
            selected_models=getattr(state, "selected_models", None),
        )
        app_state.active_pipelines[task_id] = pipeline
        logger.info(
            "[Queue] Starting durable Agnes task %s with budget=%ss",
            task_id, WORKER_BUDGET_SECONDS,
        )

        async def budget_watch() -> None:
            nonlocal budget_expired
            await asyncio.sleep(WORKER_BUDGET_SECONDS)
            budget_expired = True
            logger.warning(
                "[Queue] Worker budget reached for task %s; requesting resumable stop",
                task_id,
            )
            pipeline.stop()

        budget_watcher = asyncio.create_task(budget_watch())
        stop_watcher = asyncio.create_task(_watch_durable_stop(task_id, pipeline))
        try:
            await deps.run_pipeline_with_concurrency(
                pipeline, state, tm, already_claimed=True
            )
        finally:
            budget_watcher.cancel()
            stop_watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await budget_watcher
            with contextlib.suppress(asyncio.CancelledError):
                await stop_watcher

        if budget_expired:
            latest = supabase_store.get_task(task_id) or {}
            latest_status = str(latest.get("status") or "").lower()
            if latest_status not in {"completed", "failed"}:
                tm.update_state(
                    status=StepStatus.QUEUED,
                    current_status="running",
                    current_message="Ejecución dividida por límite de tiempo; reanudando automáticamente.",
                )
                from vercel.queue import send

                await send(
                    AGNES_QUEUE_TOPIC,
                    {"task_id": task_id},
                    idempotency_key=f"agnes-task-resume:{task_id}:{state.updated_at}",
                    retention=timedelta(days=1),
                    delay=timedelta(seconds=WORKER_CONTINUATION_DELAY_SECONDS),
                )
                logger.info(
                    "[Queue] Task %s checkpointed and continuation enqueued",
                    task_id,
                )
                # The task is durable QUEUED again. Do not raise: acknowledging
                # this delivery prevents Vercel from also redelivering the old
                # message while the explicit continuation is pending.
                return

        logger.info("[Queue] Finished durable Agnes task %s", task_id)
    except Exception:
        # Queue is at-least-once. If the function itself fails after claiming,
        # release the durable claim so the redelivery can execute the task.
        supabase_store.release_task_claim(task_id)
        raise

async def _watch_durable_stop(task_id: str, pipeline) -> None:
    """Stop a running Vercel pipeline when the durable task is cancelled."""
    while True:
        await asyncio.sleep(2)
        durable = supabase_store.get_task(task_id)
        if durable and str(durable.get("status") or "").lower() == "pending":
            logger.info("[Queue] Durable stop requested for task %s", task_id)
            pipeline.stop()
            return


@subscribe(topic=AGNES_QUEUE_TOPIC, consumer_group="agnes-video-worker",
            retry_after=900, max_concurrency=1, max_attempts=5)
async def handle_agnes_task(message: dict) -> None:
    await process_agnes_task(message)
