import cProfile,hashlib,json,os,pstats,shutil,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
sys.path.insert(0,str(Path.cwd()/'apps/api'))
from alembic import command
from alembic.config import Config
from app.db.session import create_database_engine
from app.db.testing import temporary_database
from app.tests.dashboard_scenario import DashboardScenario,STAGES
from app.tests.test_demo_seed import database_snapshot
root=Path.cwd(); destination=root/'.runtime/MVP-401-label-browser';destination.mkdir(exist_ok=True)
clock=destination/'clock.json'; processes=[];logs=[]
expected={'initial':(3462400,3157400,0,0),'goal_confirmed':(3462400,None,None,None),'goal_created':(3462400,3157400,0,0),'salary':(3662400,3357400,0,0),'goal_allocated':(3662400,3347400,10000,0),'purchase_prepared':(3662400,3347400,10000,0),'purchased':(3412400,3097400,10000,250000),'consumption':(280000,0,10000,250000),'redeem_prepared':(280000,0,10000,250000),'redeemed':(530000,215000,10000,0)}
manifest={'status':'IN_PROGRESS','started_at':datetime.now(timezone.utc).isoformat(),'classification':'INSTRUMENTED_TEST_CHAIN_NOT_PRODUCT_SLA','stages':[],'commands':[],'phases':[],'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
manifest_file=destination/'manifest.json'
source_paths=sorted([p for p in (root/'apps/api/app').rglob('*.py') if 'tests' not in p.parts]+list((root/'apps/api/alembic').rglob('*.py'))+list((root/'apps/web/src').rglob('*.tsx'))+list((root/'apps/web/src').rglob('*.ts'))+list((root/'apps/web/src').rglob('*.css'))+[root/'apps/api/app/tests/dashboard_scenario.py',root/'packages/contracts/openapi.json',root/'packages/contracts/schema.d.ts'])
source_before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
manifest['source_before']=source_before
def save(): manifest_file.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
def phase(label,function):
 profile=cProfile.Profile();started=time.monotonic();profile.enable()
 try:return function()
 finally:
  profile.disable();profile.dump_stats(str(destination/(label+'.pstats')))
  stats=pstats.Stats(profile);top=sorted(stats.stats.items(),key=lambda pair:pair[1][3],reverse=True)[:20]
  manifest['phases'].append({'phase':label,'elapsed_seconds':time.monotonic()-started,'top_cumulative':[{'file':key[0],'line':key[1],'function':key[2],'primitive_calls':value[0],'calls':value[1],'self_seconds':value[2],'cumulative_seconds':value[3]} for key,value in top]});save()
def native_browser_body(path):
 candidate=(root/path).resolve()
 if not candidate.is_relative_to((root/'.runtime/MVP-401-browser-cli').resolve()):raise ValueError('Use the actual browser CLI response log')
 source=candidate.read_text(encoding='utf-8');decoder=json.JSONDecoder()
 for offset,char in enumerate(source):
  if char!='{':continue
  try:doc,_=decoder.raw_decode(source[offset:])
  except json.JSONDecodeError:continue
  if isinstance(doc,dict) and doc.get('schema_version')=='dashboard-v1':return doc,candidate
 raise ValueError('No actual dashboard-v1 browser response in this log')
def wait(url):
 deadline=time.monotonic()+60
 while time.monotonic()<deadline:
  try:
   with urlopen(url,timeout=2) as r:
    if r.status==200:return
  except Exception:pass
  time.sleep(.2)
 raise TimeoutError(url)
def launch(cmd,env,name):
 log=(destination/name).open('w',encoding='utf-8');logs.append(log)
 p=subprocess.Popen(cmd,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
 processes.append(p);manifest['commands'].append({'command':cmd,'pid':p.pid});save()
 return p
try:
 database_started=time.monotonic()
 with temporary_database() as url:
  manifest['phases'].append({'phase':'create_database','elapsed_seconds':time.monotonic()-database_started});save()
  config=Config(str(root/'alembic.ini'));config.set_main_option('sqlalchemy.url',url.replace('%','%%'));phase('migration',lambda:command.upgrade(config,'head'))
  engine=create_database_engine(url)
  try:
   flow=DashboardScenario(engine);phase('seed',flow.initialize);clock.write_text(json.dumps({'as_of':flow.now.isoformat()}),encoding='utf-8')
   env=dict(os.environ,PYTHONUTF8='1',PYTHONPATH=str(root/'apps/api'),DATABASE_URL=url,BF_DASHBOARD_TEST_CLOCK=str(clock),API_PROXY_TARGET='http://127.0.0.1:18040')
   launch([sys.executable,'-m','uvicorn','--no-access-log','--app-dir','.runtime','mvp401_e2e_server:api','--host','127.0.0.1','--port','18040'],env,'api.log')
   launch([shutil.which('node'),str(root/'apps/web/node_modules/vite/bin/vite.js'),str(root/'apps/web'),'--host','127.0.0.1','--port','15173'],env,'web.log')
   wait('http://127.0.0.1:18040/api/v1/health');wait('http://127.0.0.1:15173')
   for stage in ('initial',):
    event=phase('funds-'+stage,lambda:flow.advance(stage)) if stage!='initial' else {'stage':stage,'as_of':flow.now.isoformat()}
    clock.write_text(json.dumps({'as_of':flow.now.isoformat()}),encoding='utf-8')
    before=database_snapshot(engine)
    item={'stage':stage,'as_of':flow.now.isoformat(),'expected':expected[stage],'event':event,'browser_url':'http://127.0.0.1:15173'}
    manifest['stages'].append(item);save()
    print('STAGE_WAIT '+json.dumps({k:v for k,v in item.items() if k!='event'},ensure_ascii=False),flush=True)
    control=destination/(stage+'.command.txt')
    while not control.exists():time.sleep(.2)
    text=control.read_text(encoding='utf-8').strip()
    assert database_snapshot(engine)==before,'Browser passive view wrote database'
    item['browser_readonly_all_tables']=True;save()
    if text=='stop':raise RuntimeError('Explicit fixture driver stop')
    if not text.startswith('next '):raise RuntimeError('Expected next and the actual browser response log')
    doc,source=native_browser_body(text[5:])
    actual=(doc['account_facts']['facts']['cash_balance_cents'],doc['boundary']['safe_idle_cents'],doc['goal_ownership']['allocated_cents'],doc['managed_assets']['managed_current_principal_cents'])
    assert actual==expected[stage],(stage,actual,expected[stage])
    assert doc['as_of']==flow.now.isoformat().replace('+00:00','Z'),(stage,doc['as_of'])
    assert doc['audit']['status']=='VALID',(stage,doc['audit'])
    item.update(actual=actual,browser_response_log=str(source.relative_to(root)),browser_response_log_sha256=hashlib.sha256(source.read_bytes()).hexdigest());save()
   manifest['status']='COMPLETE';save()
  finally:engine.dispose()
except BaseException as error:
 manifest['status']='FAILED';manifest['error']=repr(error);save();raise
finally:
 for p in reversed(processes):
  if p.poll() is None:
   p.terminate()
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:p.kill();p.wait(timeout=10)
 for log in logs:log.close()
 manifest['finished_at']=datetime.now(timezone.utc).isoformat();manifest['changed_during_run']=[p for p,h in source_before.items() if hashlib.sha256((root/p).read_bytes()).hexdigest()!=h];save()
