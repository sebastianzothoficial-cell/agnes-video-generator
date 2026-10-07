"""OpenRouter planning layer for real Agnes video generation."""
from __future__ import annotations
import json, logging
from typing import Any
import requests
from pydantic import BaseModel, Field, ValidationError
from core.config import get_settings

logger = logging.getLogger(__name__)

class VideoPlan(BaseModel):
    title: str = ""
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    duration: int = 5
    aspect_ratio: str = "16:9"
    size: str = "720P"
    model: str = "agnes-video-2.5-flash"
    mode: str = "text"
    scenes: list[dict[str, Any]] = Field(default_factory=list)

class OpenRouterPlanningError(RuntimeError):
    pass

_SYSTEM_PROMPT = """You are the planning layer for a real video generator. Agnes AI is the ONLY video generation provider. Return JSON only. Transform the user idea into one coherent production-ready video request. Use only duration 4..12, aspect ratios 21:9/16:9/4:3/1:1/3:4/9:16, and modes text/keyframe/reference. For Flash use size 720P."""

def _extract_json(content: str) -> dict:
    text = (content or '').strip()
    if text.startswith('```'):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == '```': lines = lines[:-1]
        text = '\n'.join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end <= start: raise OpenRouterPlanningError('OpenRouter returned invalid JSON') from exc
        try: value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as inner: raise OpenRouterPlanningError('OpenRouter returned invalid JSON') from inner
    if not isinstance(value, dict): raise OpenRouterPlanningError('OpenRouter plan must be a JSON object')
    return value

def _validate_plan(data: dict) -> VideoPlan:
    try: plan = VideoPlan.model_validate(data)
    except ValidationError as exc: raise OpenRouterPlanningError(f'OpenRouter plan validation failed: {exc}') from exc
    if not 4 <= plan.duration <= 12: raise OpenRouterPlanningError('OpenRouter returned unsupported duration')
    if plan.aspect_ratio not in {'21:9','16:9','4:3','1:1','3:4','9:16'}: raise OpenRouterPlanningError('OpenRouter returned unsupported aspect ratio')
    if plan.mode not in {'text','keyframe','reference'}: raise OpenRouterPlanningError('OpenRouter returned unsupported Agnes mode')
    if plan.model.startswith('agnes-video-2.5-flash'): plan.size = '720P'
    return plan

def plan_video(user_idea: str) -> VideoPlan:
    settings = get_settings()
    api_key = (settings.openrouter_api_key or '').strip()
    if not api_key: raise OpenRouterPlanningError('OPENROUTER_API_KEY is not configured')
    model = (settings.openrouter_model or '').strip()
    if not model: raise OpenRouterPlanningError('OPENROUTER_MODEL is not configured')
    base_url = (settings.openrouter_base_url or 'https://openrouter.ai/api/v1').rstrip('/')
    logger.info('[OPENROUTER] request started')
    try:
        response = requests.post(
            f'{base_url}/chat/completions',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'HTTP-Referer': 'https://github.com/sebastianzothoficial-cell/agnes-video-generator', 'X-Title': 'Agnes Video Generator'},
            json={'model': model, 'messages': [{'role':'system','content':_SYSTEM_PROMPT},{'role':'user','content':user_idea.strip()}], 'temperature':0.2, 'response_format':{'type':'json_object'}},
            timeout=max(10, int(settings.openrouter_timeout)),
        )
        if response.status_code == 401: raise OpenRouterPlanningError('OpenRouter authentication failed')
        if response.status_code == 403: raise OpenRouterPlanningError('OpenRouter access forbidden')
        if response.status_code == 429: raise OpenRouterPlanningError('OpenRouter rate limit exceeded')
        if response.status_code >= 500: raise OpenRouterPlanningError(f'OpenRouter provider error (HTTP {response.status_code})')
        response.raise_for_status()
        payload = response.json()
    except OpenRouterPlanningError: raise
    except requests.Timeout as exc: raise OpenRouterPlanningError('OpenRouter request timed out') from exc
    except requests.RequestException as exc: raise OpenRouterPlanningError('OpenRouter request failed') from exc
    try: content = payload['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc: raise OpenRouterPlanningError('OpenRouter response did not contain a message') from exc
    plan = _validate_plan(_extract_json(content))
    logger.info('[OPENROUTER] prompt generated')
    return plan
