"""Reproduce post-hoc offline analyses from the merged experiment records."""
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from feedback_eval.cli import _prepare_analysis_records, _load_homology_groups
from feedback_eval.analysis import _model_of, summarize
from feedback_eval.scoring import score

STORE = ROOT / 'runs/feedback_eval/official'
OUT = ROOT / 'outputs/offline_analysis'
conditions = ('B0', 'B1', 'B2', 'B3')
freeze = json.loads((STORE / 'freeze.json').read_text())
rows, replaced, sources = _prepare_analysis_records(STORE, freeze, None, False)
assert len(rows) == 2080 and len(replaced) == 169
assert summarize(rows) == json.loads((STORE / 'analysis.json').read_text())['summary']
idx = {(_model_of(r), str(r['item_id']), r['condition']): r for r in rows}
items = {str(x['key']): x for x in map(json.loads, (ROOT/'data/IFBench_test.jsonl').read_text().splitlines())}
group = {str(item): f'g{n}' for n, ids in enumerate(_load_homology_groups(None)) for item in ids}
models = sorted({k[0] for k in idx})

def flag(r):
    return r['score']['strict']['all_pass'] if r['accounting_status'] == 'success' else None

def paired(m, a, b, mode):
    out = []
    ids = sorted({i for model, i, c in idx if model == m})
    for i in ids:
        vals = [flag(idx[(m, i, c)]) for c in conditions]
        if mode == 'complete_four' and any(v is None for v in vals):
            continue
        x, y = vals[conditions.index(a)], vals[conditions.index(b)]
        if mode == 'missing_fail':
            x, y = bool(x), bool(y)
        if x is not None and y is not None:
            out.append((i, int(y)-int(x)))
    return out

def exact_sign_flip(pairs):
    sums = Counter()
    for item, delta in pairs:
        sums[group.get(item, 'item:'+item)] += delta
    weights = [abs(x) for x in sums.values() if x]
    dist = {0: 1}
    for w in weights:
        nxt = defaultdict(int)
        for s, n in dist.items():
            nxt[s+w] += n
            nxt[s-w] += n
        dist = nxt
    observed = abs(sum(delta for _, delta in pairs))
    return sum(n for s, n in dist.items() if abs(s) >= observed) / (2 ** len(weights)), len(sums), len(weights)

comparisons = [('B0','B1'), ('B0','B2'), ('B0','B3'), ('B1','B2'), ('B3','B2')]
tests = []
for family, pairspec in [('baseline_six', comparisons[:3]), ('exploratory_four', comparisons[3:])]:
    family_tests = []
    for model in models:
        for a,b in pairspec:
            ps = paired(model,a,b,'available')
            p, nclusters, nonzero = exact_sign_flip(ps)
            entry = dict(family=family, model=model, contrast=b+'-'+a, n=len(ps), clusters=nclusters, nonzero_clusters=nonzero, delta_pp=100*sum(d for _,d in ps)/len(ps), p=p)
            family_tests.append(entry)
    ordered = sorted(family_tests, key=lambda x:x['p'])
    prev = 0
    for j, entry in enumerate(ordered):
        prev = max(prev, min(1, (len(ordered)-j)*entry['p']))
        entry['holm_p'] = prev
    tests.extend(family_tests)
robust = []
for m in models:
    for a,b in comparisons:
        for mode in ('available','complete_four','missing_fail'):
            ps = paired(m,a,b,mode)
            robust.append(dict(model=m,contrast=b+'-'+a,mode=mode,n=len(ps),delta_pp=100*sum(d for _,d in ps)/len(ps)))
categories = defaultdict(Counter)
for (m,i,c), r in idx.items():
    if flag(r) is None: continue
    flags = r['score']['strict']['per_constraint']
    ids = items[i]['instruction_id_list']
    assert len(flags) == len(ids)
    for name, ok in zip(ids,flags):
        k=(m,c,name.split(':')[0]); categories[k]['n']+=1; categories[k]['fail']+=not ok
cats=[dict(model=m,condition=c,category=k,**v) for (m,c,k),v in sorted(categories.items())]
key=json.loads((STORE/'review_pack/key.json').read_text())['key']
reviewids = {str(x['item_id']) for x in key}
assert len(key)==320 and len(reviewids)==40
review=[]
for m in models:
 for c in conditions:
    for sample, select in [('review',lambda i:i in reviewids),('remaining',lambda i:i not in reviewids),('all',lambda i:True)]:
        vals=[flag(r) for (model,i,cond),r in idx.items() if model==m and cond==c and select(i) and flag(r) is not None]
        review.append(dict(model=m,condition=c,sample=sample,n=len(vals),passed=sum(vals)))
cases=[]
for m,i,cs in [('gpt-4.1','173',['B0','B2']),('gpt-4.1','287',['B0','B1']),('gpt-4o','247',['B2'])]:
 for c in cs:
    r=idx[(m,i,c)]; rescored=score(items[i],r['generation']['text'])
    assert rescored['strict'] == r['score']['strict']
    matches=[]
    for base in [STORE,*map(Path,sources)]:
        for p in (base/'records').glob('*.json'):
            obj=json.loads(p.read_text())
            if obj.get('record_key')==r.get('record_key') and (obj.get('generation') or {}).get('text')==r['generation']['text']:
                matches.append(str(p.relative_to(ROOT)))
    cases.append(dict(model=m,item=i,condition=c,strict=flag(r),response=r['generation']['text'],sources=matches))
result=dict(methods={'test':'exact two-sided cluster sign-flip, statistic absolute sum of paired differences; independent cluster swaps under exchangeability','families':'Holm correction within six baseline comparisons and separately four exploratory comparisons; all tests post hoc','robustness':'available paired; complete all four conditions; missing as failure (sensitivity only)','categories':'strict per constraint, prefix of instruction ID, descriptive; constraints within items not independent'},tests=tests,robustness=robust,categories=cats,review=review,cases=cases,n_replaced=len(replaced))
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
for name in ['tests','robustness','categories','review','cases']:
    data=result[name]
    with (OUT/(name+'.csv')).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=data[0].keys());w.writeheader();w.writerows(data)
print(json.dumps({'tests':tests,'robustness':robust,'review':review,'cases':cases},ensure_ascii=False,indent=2))
