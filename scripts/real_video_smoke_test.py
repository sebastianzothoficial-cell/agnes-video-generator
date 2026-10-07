import asyncio, json, os, pathlib
from core.api.agnes_video import AgnesVideoAPI
from core.api.openrouter_planner import plan_video

IDEA = 'A cinematic aerial view of Buenos Aires, Argentina at golden hour, showing the Obelisco, Avenida 9 de Julio and the city skyline. Elegant premium travel commercial, realistic photography, smooth cinematic camera movement, warm natural light, high-end tourism advertisement.'

async def main():
    if not os.getenv('AGNES_API_KEY'): raise SystemExit('AGNES_API_KEY is missing')
    if not os.getenv('OPENROUTER_API_KEY'): raise SystemExit('OPENROUTER_API_KEY is missing')
    if not os.getenv('OPENROUTER_MODEL'): raise SystemExit('OPENROUTER_MODEL is missing')
    plan = plan_video(IDEA)
    print(json.dumps({'openrouter':'OK','plan':plan.model_dump()}, ensure_ascii=False))
    api = AgnesVideoAPI(api_key=os.environ['AGNES_API_KEY'], model='agnes-video-2.5-flash')
    video_id = await api.submit_video(prompt=plan.prompt, generation_mode='text', duration=5, width=1280, height=720, video_size='720P', progress_callback=None)
    print(json.dumps({'agnes':'SUBMITTED','video_id':video_id}))
    output = await api.wait_for_video(video_id, progress_callback=None)
    out = pathlib.Path('real-video-smoke.mp4')
    await output.save(str(out))
    if not out.exists() or out.stat().st_size <= 0: raise SystemExit('Agnes completed without a usable video file')
    print(json.dumps({'status':'completed','video_id':video_id,'video_path':str(out),'bytes':out.stat().st_size}))

if __name__ == '__main__': asyncio.run(main())
