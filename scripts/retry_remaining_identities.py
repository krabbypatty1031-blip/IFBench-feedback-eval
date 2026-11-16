"""Retry remaining non-filtered identities without altering prior experiment records."""
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from feedback_eval.cli import _prepare_analysis_records
from feedback_eval.analysis import _model_of
from feedback_eval.client import build_http_client, classify_generation
from feedback_eval.environment import load_dotenv_file
from feedback_eval.store import load_mapping_yaml
from feedback_eval.runner import load_prompt_templates, build_revision_user_message
from feedback_eval.feedback import make_feedback
from feedback_eval.scoring import score

store=ROOT/'runs/feedback_eval/official'; freeze=json.loads((store/'freeze.json').read_text()); rows,_,_=_prepare_analysis_records(store,freeze,None,False)
index={(_model_of(r),str(r['item_id']),r['condition']):r for r in rows}
items={str(x['key']):x for x in map(json.loads,(ROOT/'data/IFBench_test.jsonl').read_text().splitlines())}
targets=sorted((k for k,r in index.items() if r['accounting_status']!='success' and k[1]!='147'),key=lambda k:(k[0],int(k[1]),k[2]))
assert len(targets)==13 and sum(index[k]['accounting_status']=='empty_response' for k in targets)==11
load_dotenv_file(ROOT/'.env')
if not any(os.environ.get(k) for k in ('API_KEY','FEEDBACK_EVAL_API_KEY','AZURE_OPENAI_API_KEY')): raise SystemExit('API key is not available')
config=load_mapping_yaml(ROOT/'configs/feedback_eval/official.yaml');clients={m['id']:build_http_client(m,timeout_s=300,max_retries=0) for m in config['models']};templates=load_prompt_templates()
out=ROOT/'runs/feedback_eval/retry_remaining_20261116';out.mkdir(parents=True,exist_ok=False)
summary=[]
for m,i,c in targets:
 old=index[m,i,c];b0=index[m,i,'B0'];assert b0['accounting_status']=='success' and b0['generation']['text'];item=items[i]; passed=b0['score']['strict']['all_pass']; feedback=make_feedback(item,b0['score']['strict'],c) if c in ('B2','B3') else None
 msg=[{'role':'user','content':item['prompt']},{'role':'assistant','content':b0['generation']['text']},{'role':'user','content':build_revision_user_message(templates,c,feedback,passed if c!='B1' else None)}]
 settings=dict(old['settings']); settings['max_tokens']=16384 if old['accounting_status']=='empty_response' else 4096
 gen=clients[m].generate(msg,settings);status=classify_generation(gen);result={'model':m,'item_id':i,'condition':c,'previous_status':old['accounting_status'],'accounting_status':status,'messages':msg,'settings':settings,'generation':gen,'feedback':feedback,'score':score(item,gen.get('text') or '') if status=='success' else None,'created_at':datetime.now(timezone.utc).isoformat(),'baseline_b0_record_key':b0.get('record_key')}
 (out/f'{m}_{i}_{c}.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n'); summary.append({k:result[k] for k in ('model','item_id','condition','previous_status','accounting_status')});(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(f'{m} item={i} {c}: {status}',flush=True)
 if gen.get('error') and any(s in gen['error'] for s in ('HTTP 401','HTTP 403','HTTP 429')):
  print('Stopped after authentication or quota error',flush=True);break
