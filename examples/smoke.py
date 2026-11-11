"""Real HTTP local-demo smoke test; refuses to run against live-provider mode."""
import argparse
import time
import uuid
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://localhost:8000')
args=parser.parse_args()
base=args.base_url.rstrip('/')
client=httpx.Client(base_url=base,timeout=10)

def request(method,path,expected=200,**kwargs):
    response=client.request(method,'/api/v1/'+path,**kwargs)
    assert response.status_code==expected,(path,response.status_code,response.text[:500])
    return response.json()

health=request('GET','health/live/')
assert health['data']['mode']=='local-demo-no-ai','This smoke script is for local-demo mode only.'
suffix=uuid.uuid4().hex[:12]
registration=request('POST','auth/register/',201,json={'username':'smoke_'+suffix,'email':suffix+'@example.test','password':'strong-demo-only-password-692!','organisation_name':'Smoke Shop'})
jwt={'Authorization':'Bearer '+registration['data']['access']}
bot=request('POST','bots/',201,headers=jwt,json={'name':'Smoke Bot','allowed_origins':['http://localhost:8000']})['data']['id']
source=request('POST',f'bots/{bot}/sources/',202,headers=jwt,json={'kind':'faq','title':'Delivery FAQ','text':'Delivery in Lagos costs 2500 naira. Delivery takes three business days.'})['data']['id']
for _ in range(40):
    status=request('GET',f'bots/{bot}/sources/{source}/',headers=jwt)['data']
    if status['status']=='ready':break
    assert status['status']!='failed',status['error']
    time.sleep(.5)
else:raise AssertionError('Worker did not process knowledge within 20 seconds.')
headers={**jwt,'Idempotency-Key':'smoke-chat'}
body={'message':'What is the delivery fee?'}
answer=request('POST',f'bots/{bot}/chat/',headers=headers,json=body)
assert '2500' in answer['data']['answer'] and answer['data']['sources']
assert answer==request('POST',f'bots/{bot}/chat/',headers=headers,json=body)
request('POST',f'bots/{bot}/chat/',409,headers=headers,json={'message':'changed'})
key=request('POST','keys/',201,headers=jwt,json={'name':'Smoke API','bot_id':bot,'scopes':['chat:write','conversations:read']})['data']
key_headers={'Authorization':'ApiKey '+key['secret'],'Idempotency-Key':'smoke-key'}
request('POST',f'bots/{bot}/chat/',headers=key_headers,json=body)
request('GET','keys/',403,headers=key_headers)
assert request('GET','conversations/',headers=key_headers)['count']==1
visitor=request('POST',f'widget/{bot}/session/',201,json={'origin':'http://localhost:8000'})['data']['token']
visitor_headers={'X-Visitor-Token':visitor,'X-Website-Origin':'http://localhost:8000','Idempotency-Key':'smoke-visitor'}
request('POST',f'widget/{bot}/chat/',headers=visitor_headers,json=body)
request('POST',f'widget/{bot}/session/',403,json={'origin':'https://evil.example'})
assert client.get(f'/widget/{bot}/?origin=http://localhost:8000',headers={'Referer':'http://localhost:8000/widget-example/'}).status_code==200
other=request('POST','auth/register/',201,json={'username':'other_'+suffix,'email':'other_'+suffix+'@example.test','password':'strong-demo-only-password-941!','organisation_name':'Other Shop'})
request('GET',f'bots/{bot}/',404,headers={'Authorization':'Bearer '+other['data']['access']})
usage=request('GET','usage/',headers=jwt)['data']
assert usage['successful_answers']==3,usage
request('POST',f"keys/{key['key']['id']}/revoke/",headers=jwt)
request('POST',f'bots/{bot}/chat/',401,headers=key_headers,json=body)
print('PASS: real HTTP registration, asynchronous ingestion, demo chat, idempotency, scoped keys, tenant isolation, usage and visitor chat. No external AI/payment calls.')
print('Widget review URL: '+base+'/widget-example/?bot='+bot)
