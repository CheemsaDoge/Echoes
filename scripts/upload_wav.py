#!/usr/bin/env python3
"""Import an explicitly supplied 16kHz/mono/PCM16 WAV through the public API."""
import argparse, hashlib, io, json, os, time, uuid, wave
from datetime import datetime
from pathlib import Path
import httpx

p=argparse.ArgumentParser()
p.add_argument('file',type=Path)
p.add_argument('--url',default='http://127.0.0.1:8000')
p.add_argument('--started-at-ms',type=int,help='Original recording UTC epoch milliseconds; defaults to import time')
p.add_argument('--purpose',choices=['journal','calibration'],default='journal')
a=p.parse_args()
token=os.environ.get('API_TOKEN','')
if not token: raise SystemExit('Export API_TOKEN before running this script.')
sid=str(uuid.uuid4());sample=0;seq=0
with wave.open(str(a.file),'rb') as source:
    if (source.getframerate(),source.getnchannels(),source.getsampwidth(),source.getcomptype())!=(16000,1,2,'NONE'):
        raise SystemExit('Input must be an uncompressed 16000Hz, mono, PCM16 WAV. Convert it explicitly first.')
    if source.getnframes()==0: raise SystemExit('Input is empty.')
    with httpx.Client(base_url=a.url,headers={'Authorization':'Bearer '+token},timeout=90) as client:
        def request(method,path,**kw):
            r=client.request(method,path,**kw);r.raise_for_status();return r.json()
        started=a.started_at_ms if a.started_at_ms is not None else time.time_ns()//1_000_000
        offset=int(datetime.now().astimezone().utcoffset().total_seconds()/60)
        request('POST','/v1/sessions',json={'id':sid,'started_at_ms':started,'purpose':a.purpose,'timezone_offset_min':offset,'device_id':'explicit-wav-import'})
        while True:
            raw=source.readframes(480000)
            if not raw:break
            out=io.BytesIO()
            with wave.open(out,'wb') as target:
                target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000);target.writeframes(raw)
            blob=out.getvalue()
            meta={'id':str(uuid.uuid4()),'start_sample':sample,'sample_count':len(raw)//2,'sha256':hashlib.sha256(blob).hexdigest()}
            request('POST',f'/v1/sessions/{sid}/chunks/{seq}',content=blob,headers={'Content-Type':'audio/wav','X-Chunk-Metadata':json.dumps(meta)})
            sample+=len(raw)//2;seq+=1
        request('POST',f'/v1/sessions/{sid}/close',json={'final_seq':seq-1})
print(json.dumps({'session_id':sid,'chunks':seq,'audio_seconds':sample/16000,'purpose':a.purpose,'note':'Saved and queued. Completion depends on worker and provider configuration.'},indent=2))
