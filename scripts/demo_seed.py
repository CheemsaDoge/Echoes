#!/usr/bin/env python3
"""Upload synthetic audio through real HTTP routes. Requires ASR_PROVIDER=mock."""
import argparse, hashlib, io, json, os, time, uuid, wave
from datetime import datetime, timedelta
import numpy as np
import httpx

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://127.0.0.1:8000')
a = p.parse_args()
token = os.environ.get('API_TOKEN', '')
if not token:
    raise SystemExit('Export API_TOKEN first; never pass secrets in command-line arguments.')
headers = {'Authorization': 'Bearer ' + token}
with httpx.Client(base_url=a.url, headers=headers, timeout=20) as client:
    def req(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    status = req('GET', '/v1/status')
    if status['asr_provider'] != 'mock':
        raise SystemExit('Demo seeding requires ASR_PROVIDER=mock; it never calls a paid provider.')
    titles = [('[演示] 讨论算法学习与下次组会安排', 'study'), ('[演示] 午饭时确认打包和取餐时间', 'meal'), ('[演示] 散步时聊起今天的计划', 'social')]
    session_ids = []
    base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    for index, (title, typ) in enumerate(titles):
        sid, cid = 'demo-' + str(uuid.uuid4()), str(uuid.uuid4())
        session_ids.append((sid, title, typ))
        started = base + timedelta(minutes=45 * index)
        req('POST', '/v1/sessions', json={'id': sid, 'started_at_ms': int(started.timestamp()*1000), 'timezone_offset_min': int(started.utcoffset().total_seconds()/60), 'device_id': 'SYNTHETIC-DEMO'})
        x = np.zeros(20*16000, dtype=np.float32)
        for left, right in [(1,5),(7,10),(13,18)]:
            t=np.arange((right-left)*16000)/16000
            x[left*16000:right*16000] = .18*np.sin(2*np.pi*220*t)
        b=io.BytesIO()
        with wave.open(b,'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes((x*32767).astype('<i2').tobytes())
        content=b.getvalue()
        meta={'id':cid, 'start_sample':0, 'sample_count':len(x), 'sha256':hashlib.sha256(content).hexdigest()}
        req('POST', f'/v1/sessions/{sid}/chunks/0', headers={'X-Chunk-Metadata':json.dumps(meta),'Content-Type':'audio/wav'}, content=content)
        req('POST', f'/v1/sessions/{sid}/close', json={'final_seq':0})
    deadline=time.monotonic()+30
    pending={r[0]:r for r in session_ids}
    while pending and time.monotonic()<deadline:
        for event in req('GET','/v1/events?limit=500'):
            if event['session_id'] in pending:
                sid,title,typ=pending.pop(event['session_id'])
                req('PATCH','/v1/events/'+event['id'], json={'title':title,'type_id':typ})
        if pending: time.sleep(.25)
    if pending: raise SystemExit('Demo uploads persisted, but processing did not finish. Check /v1/batches.')
    print(json.dumps({'synthetic_demo':True, 'sessions':[r[0] for r in session_ids], 'events_created':len(session_ids)}, ensure_ascii=False, indent=2))
