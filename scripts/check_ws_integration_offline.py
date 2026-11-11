from pathlib import Path
import sys, types, importlib.util
root=Path(__file__).resolve().parents[1]
web=root/'apps/BackendWebFlask'
w=root/'apps/VisionWorker'

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

# Real issuer and verifier used by the two services; no external framework required.
issuer=load('issuer_test',web/'services/vision_ws_ticket.py')
verifier=load('verifier_test',w/'ws_ticket.py')
issued=issuer.issue_ws_ticket('test-session-123')
assert verifier.verify_ticket(issued['ticket'],session_id='test-session-123',secret=issuer.WS_SECRET)
assert not verifier.verify_ticket(issued['ticket'],session_id='someone-else',secret=issuer.WS_SECRET)
assert not verifier.verify_ticket(issued['ticket']+'x',session_id='test-session-123',secret=issuer.WS_SECRET)
print('PASS HMAC browser ticket cross-service + wrong-session and tamper denied')

# Offline WS bind regression: shared V1 state MUST NOT be cleared by ZIP3 preview.
request=types.SimpleNamespace(headers={'Origin':'http://localhost:5000'})
sys.modules['flask']=types.SimpleNamespace(request=request)
sys.modules['config']=types.SimpleNamespace(WS_ORIGINS={'http://localhost:5000'},WS_SECRET='key')
class State:
 def __init__(self,session_id):
  self.session_id=session_id
  self.status='ready'; self.message='idle'; self.capture_source='file'; self.expected={}; self.class_map={}
  self.model_version='v1'; self.conf_threshold=0.7;self.cancel=False
  self.counts={};self.boxes=[];self.latency_ms=None;self.last_yolo_ms=None;self.last_post_ms=None
 def to_public(self):return {'session_id':self.session_id,'counts':self.counts}

def prepare(state):
 state.capture_source='live';state.status='processing';state.counts={};state.boxes=[]
def cancel(state):state.cancel=True;state.capture_source=None
sys.modules['inference']=types.SimpleNamespace(SessionState=State,cancel_capture=cancel,decode_jpeg_b64=lambda s:object(),prepare_live_run=prepare,run_frame=lambda f,s,video_t:s.counts.update({'demo':{'detected_quantity':2}}))
sys.modules['ws_ticket']=types.SimpleNamespace(verify_ticket=lambda token,session_id,secret:token=='good')
service=load('isolated_ws_test',w/'live_ws.py')
class Sock:
 def route(self,path):
  assert path=='/ws'
  def wrapper(fn):self.handler=fn;return fn
  return wrapper
class Socket:
 def __init__(self,packets):self.packets=packets;self.sent=[]
 def receive(self):
  import time
  if self.packets:
   value=self.packets.pop(0);time.sleep(.02);return value
  return None
 def send(self,msg):self.sent.append(msg)
shared=State('test-session-123')
shared.counts={'instruments':{'detected_quantity':15}}
shared.video_id='v1-count-file'
reg=Sock();service.register_live_ws(reg,get_state=lambda sid:shared)
import json
ws=Socket([json.dumps({'type':'session.bind','session_id':'test-session-123','auth_token':'good'}),json.dumps({'type':'session.cancel'})])
reg.handler(ws)
assert shared.counts=={'instruments':{'detected_quantity':15}}
assert shared.video_id=='v1-count-file'
assert shared.status=='ready'
assert any(json.loads(p).get('state')=='ready' for p in ws.sent)
print('PASS websocket bind/cancel does not mutate persistent registered file/15 instruments')

# Existing in-progress file job must not be converted to live stream.
shared.status='processing'
ws=Socket([json.dumps({'type':'session.bind','session_id':'test-session-123','auth_token':'good'})])
reg.handler(ws)
assert any(json.loads(p).get('code')=='busy' for p in ws.sent)
print('PASS active video job rejects live bind')
