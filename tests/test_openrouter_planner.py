import pytest
from core.api.openrouter_planner import OpenRouterPlanningError, _extract_json, _validate_plan

def test_extracts_json_from_fenced_response():
    assert _extract_json('```json\n{"prompt":"x"}\n```')['prompt'] == 'x'

def test_rejects_unsupported_agnes_model():
    data={'prompt':'x','duration':5,'aspect_ratio':'16:9','size':'720P','model':'other-video-model','mode':'text'}
    with pytest.raises(OpenRouterPlanningError): _validate_plan(data)


def test_validates_agnes_flash_contract():
    plan = _validate_plan({'prompt':'x','duration':5,'aspect_ratio':'16:9','size':'1080P','model':'agnes-video-2.5-flash','mode':'text'})
    assert plan.size == '720P'

@pytest.mark.parametrize('field,value',[('duration',13),('aspect_ratio','2:1'),('mode','video')])
def test_rejects_invalid_plan(field,value):
    data={'prompt':'x','duration':5,'aspect_ratio':'16:9','size':'720P','model':'agnes-video-2.5-flash','mode':'text'}
    data[field]=value
    with pytest.raises(OpenRouterPlanningError): _validate_plan(data)
