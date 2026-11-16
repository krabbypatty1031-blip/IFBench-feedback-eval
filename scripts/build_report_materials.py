"""Build merged-result report materials locally; never calls a model API.

Run with project Python for data, system Python --plots for matplotlib figures.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/report_materials'
STORE = ROOT / 'runs/feedback_eval/official'
CONDITIONS = ('B0', 'B1', 'B2', 'B3')


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n')


def rate(n, d):
    return {'n': n, 'd': d, 'value': n / d if d else None}


def fmt(r):
    return f"{r['n']}/{r['d']} ({100*r['value']:.1f}%)" if r['d'] else 'NA (0/0)'


def load_all_content_labels(idx):
    """Load 2080 content labels from the original 40-item pack plus the remaining 220."""
    packs = [STORE / 'review_pack', STORE / 'review_pack_remaining_220']
    content = collections.defaultdict(collections.Counter)
    cross = collections.defaultdict(lambda: collections.Counter())
    label_by_identity = {}
    pack_paths = []
    for pack in packs:
        labels = list(csv.DictReader((pack / 'judgments.csv').open()))
        keys = {r['review_id']: r for r in json.loads((pack / 'key.json').read_text())['key']}
        entries = {r['review_id']: r for r in json.loads((pack / 'blinded.json').read_text())['entries']}
        pack_paths += [pack / 'judgments.csv', pack / 'key.json', pack / 'blinded.json']
        for row in labels:
            k = keys[row['review_id']]
            identity = (k['model'], str(k['item_id']), k['condition'])
            rec = idx[identity]
            assert identity not in label_by_identity
            blinded_text = entries[row['review_id']].get('response') or ''
            record_text = (rec.get('generation') or {}).get('text') or ''
            assert blinded_text == record_text
            assert row['label'] in ('complete', 'partial', 'incomplete', 'cannot_judge')
            content[k['condition']][row['label']] += 1
            if rec.get('accounting_status') == 'success' and (rec.get('score') or {}).get('strict'):
                key = 'strict_pass' if rec['score']['strict']['all_pass'] else 'strict_fail'
            else:
                key = 'no_score'
            cross[row['label']][key] += 1
            label_by_identity[identity] = row['label']
    assert len(label_by_identity) == 2080
    return content, cross, label_by_identity, pack_paths


def build():
    sys.path.insert(0, str(ROOT))
    from feedback_eval.cli import _prepare_analysis_records, _load_homology_groups
    from feedback_eval.analysis import summarize, bootstrap_paired_diffs, _model_of
    freeze = json.loads((STORE / 'freeze.json').read_text())
    rows, replaced, sources = _prepare_analysis_records(STORE, freeze, None, False)
    summary = summarize(rows)
    saved = json.loads((STORE / 'analysis.json').read_text())
    assert saved['summary'] == summary, 'Saved analysis differs from current records'
    idx = {(_model_of(r), str(r['item_id']), r['condition']): r for r in rows}
    assert len(idx) == len(rows) == 2080
    groups = _load_homology_groups(None)
    boot = bootstrap_paired_diffs(rows, groups=groups)
    assert boot == saved['bootstrap']
    tables = []
    for model in sorted(summary['by_model']):
        for condition in CONDITIONS:
            rs = [r for r in rows if _model_of(r) == model and r['condition'] == condition]
            usable = [r for r in rs if r['accounting_status'] == 'success']
            entry = {'model': model, 'condition': condition, 'expected': 260,
                     'usable': len(usable), 'status': dict(collections.Counter(r['accounting_status'] for r in rs))}
            for metric in ('strict', 'loose'):
                flags = [r['score'][metric]['all_pass'] for r in usable]
                assert all(isinstance(x, bool) for x in flags)
                cs = [v for r in usable for v in r['score'][metric]['per_constraint']]
                entry[metric+'_prompt'] = rate(sum(flags), len(flags))
                entry[metric+'_constraint'] = rate(sum(cs), len(cs))
            tables.append(entry)
    contrasts = []
    for ref, target in [('B0','B1'), ('B0','B2'), ('B0','B3'), ('B1','B2'), ('B3','B2')]:
        rr = []
        for r in rows:
            if r['condition'] in (ref, target):
                x = dict(r); x['condition'] = 'B0' if r['condition'] == ref else 'B1'; rr.append(x)
        estimates = bootstrap_paired_diffs(rr, groups=groups)
        for model, block in estimates.items():
            estimate = boot[model][target]['strict_prompt_diff'] if ref == 'B0' else block['B1']['strict_prompt_diff']
            contrasts.append({'model': model, 'contrast': target+'−'+ref,
                              'exploratory': ref != 'B0', **estimate})
    content, cross, label_by_identity, pack_paths = load_all_content_labels(idx)
    payload = {'policy': 'Current merged results only; existing successes retained; recovered text replaces failed generations.',
               'n_records': len(rows), 'n_replaced': len(replaced), 'summary': summary,
               'tables': tables, 'contrasts': contrasts, 'content_by_condition': dict(content),
               'content_vs_strict': dict(cross),
               'review_scope': '260 test items, 2 models, 4 conditions; 2080 single-rater content labels covering the full test set.'}
    write_json(OUT/'results.json', payload)
    lines = ['# 最终结果表与解读', '', '统一使用当前合并结果。空回答和 API 错误作为缺失，分母为可评分回答；配对差异只使用两条件均可评分的题目。', '',
             '## 表 1：提示级通过率', '', '| Model | Condition | Available/260 | Strict | Loose |', '|---|---|---:|---:|---:|']
    for t in tables:
        lines.append(f"| {t['model']} | {t['condition']} | {t['usable']}/260 | {fmt(t['strict_prompt'])} | {fmt(t['loose_prompt'])} |")
    lines += ['', '## 表 2：约束级通过率', '', '每条约束为一个计数单位，双约束题贡献两次；该表为描述性统计，不把同题约束视为独立样本进行推断。', '', '| Model | Condition | Strict | Loose |', '|---|---|---:|---:|']
    for t in tables:
        lines.append(f"| {t['model']} | {t['condition']} | {fmt(t['strict_constraint'])} | {fmt(t['loose_constraint'])} |")
    lines += ['', '## 表 3：Strict 配对差异', '', '单位：百分点。使用已识别同源组（其余按题目）聚类 bootstrap，1000 次重采样，seed=42，百分位 95% 区间。不同对比可使用不同的可用题目集合；区间未做多重比较校正。', '', '| Model | Contrast | Paired items | Estimate | 95% interval | Type |', '|---|---|---:|---:|---:|---|']
    for c in contrasts:
        lines.append(f"| {c['model']} | {c['contrast']} | {c['n_pairs']} | {100*c['point']:+.2f} | [{100*c['low']:+.2f}, {100*c['high']:+.2f}] | {'探索性' if c['exploratory'] else '预定 B0 对比'} |")
    lines += ['', '## 表 4：规则修复率与退化率', '', '修复率分母是配对中 B0 未通过的题数；退化率分母是配对中 B0 已通过的题数。不是内容质量变化率。', '', '| Model | Condition | Repair | Regression |', '|---|---|---:|---:|']
    for model,b in summary['by_model'].items():
        for c in CONDITIONS[1:]: lines.append(f"| {model} | {c} | {fmt(b['repair'][c])} | {fmt(b['regress'][c])} |")
    lines += ['', '## 表 5：人工审核后的内容标签', '', '260 道测试题 × 两模型 × 四条件，共 2080 条。协议为全测试集内容审核；空答和 API 错误标为 cannot_judge，不计入“已判定”分母。单人标注，不是第二套验证器，也不等同于全面事实核查。', '', '| Condition | Complete | Partial | Incomplete | Cannot judge |', '|---|---:|---:|---:|---:|']
    for c in CONDITIONS: lines.append('| '+c+' | '+' | '.join(str(content[c][v]) for v in ('complete','partial','incomplete','cannot_judge'))+' |')
    lines += ['', '| Content label | Strict pass | Strict fail | No score |', '|---|---:|---:|---:|']
    for label in ('complete','partial','incomplete','cannot_judge'):
        block = cross[label]
        lines.append(f"| {label} | {block['strict_pass']} | {block['strict_fail']} | {block['no_score']} |")
    lines += ['', '## 报告可用结论', '', '- B2 与 B3 相对 B0 的 strict 配对提升在两个模型上均为正，95% 区间不含零；B1 区间包含零。', '- 探索性比较中 B2 优于 B1；B2 与 B3 的区间包含零，不能声称 B2 显著优于 B3。', '- B2 的修复率分别为 26.7% 和 32.7%，同时存在 5.1% 和 6.8% 的规则退化。', '- 全量内容审核中 2033/2059 条已判定回答为 complete；806 条内容完整但 strict 失败；7 条 partial 与 12 条 incomplete 仍 strict 通过。规则遵循与内容充分性应分别讨论。', '- 21 条无法判定。内容标签接近天花板，不能据此证明反馈不会损害内容质量。', '', '## 图注', '', '- 01_prompt_accuracy：strict 与 loose 提示级通过率；柱上标明通过数/可评分数，纵轴从零开始。', '- 02_paired_effects：配对 strict 差异与未校正 95% bootstrap 区间；下方两组对比为探索性。', '- 03_repair_regression：修复和退化的分子/分母；两种率的分母不同。', '- 04_content_review：内容标签计数和 strict 交叉表；2080 条覆盖全部 260 道测试题。', '', '## 来源与复现', '', '`results.json` 保存全部表格原数值，`source_manifest.json` 保存输入文件哈希。图表为英文，中文说明在本文件。生成过程不调用外部 API、不改写实验记录及评分。', '', '```bash', 'NLTK_DATA="$PWD/ifbench/.nltk_data" .venv/bin/python scripts/build_report_materials.py', 'python3 scripts/build_report_materials.py --plots', '```']
    (OUT/'RESULTS_TABLES.md').write_text('\n'.join(lines)+'\n')
    cases = [('repair','gpt-4.1','173','约束修复','B0 与 B2 均给出 x^n 的正确反例；B2 在保持数学结论的同时通过停用词比例约束。该案例只展示修复，不能推断每次压缩均有益。'),
             ('regression','gpt-4.1','159','修订退化','B3 返回 PASS 反馈清单，未完成祷文改写。区分验证器反馈文本与真正任务输出，展示内容与规则同时退化。'),
             ('content_gap','gpt-4o','247','规则通过但内容不足','B2 仅给出 Baudrillard 的一句概括，strict 通过但人工确认标签为 partial。内容不足判断依赖解释深度标准，应保留主观性。'),
             ('contrast','gpt-4o','125','B2 与 B3 不同表现','B2 和 B3 均概括日志关联检测；B2 通过嵌套引号规则，B3 未通过。此为个例，不证明 B2 普遍优于 B3。')]
    all_cases = []
    case_lines = ['# 典型案例素材', '', '按分析目的选取的示例，不用于估计发生率。以下为完整原文，未改写回答。内容标签来自全测试集审核。', '']
    record_paths = {}
    for source in [STORE, *map(Path,sources)]:
        for path in (source/'records').glob('*.json'):
            rr = json.loads(path.read_text()); record_paths.setdefault(rr.get('record_key'), []).append(str(path.relative_to(ROOT)))
    for slug,model,item,title,interpretation in cases:
        rr = [idx[(model,item,c)] for c in CONDITIONS]
        block = {'case':slug,'title':title,'model':model,'item_id':item,'interpretation':interpretation,'records':[]}
        case_lines += [f'## {title}：{model} / item {item}', '', interpretation, '', '### 原任务', '', rr[0]['messages'][0]['content'], '']
        for r in rr:
            label = label_by_identity.get((model,item,r['condition']))
            block['records'].append({'condition':r['condition'],'response':r['generation']['text'],'strict':r['score']['strict'],'feedback':r.get('feedback'),'human_confirmed_content_label':label,'source_paths':record_paths.get(r.get('record_key'),[])})
            case_lines += [f"### {r['condition']}：strict {'PASS' if r['score']['strict']['all_pass'] else 'FAIL'}", '', f"逐约束结果：{r['score']['strict']['per_constraint']}；内容标签：{label or 'cannot_judge'}。", '', '````text', r['generation']['text'], '````', '']
            if r.get('feedback'): case_lines += ['收到的反馈：', '', '````text', r['feedback'], '````', '']
            case_lines += ['来源：'+', '.join(record_paths.get(r.get('record_key'),[])), '']
        all_cases.append(block)
    write_json(OUT/'cases.json', all_cases)
    (OUT/'CASE_STUDIES.md').write_text('\n'.join(case_lines)+'\n')
    paths = [STORE/'freeze.json',STORE/'analysis.json', ROOT/'configs/feedback_eval/homology.json', *pack_paths]
    for source in [STORE,*map(Path,sources)]: paths += sorted((source/'records').glob('*.json'))
    write_json(OUT/'source_manifest.json', {'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}, 'replaced':replaced})
    print(f'Built {len(tables)} result rows, {len(contrasts)} contrasts and {len(cases)} full-text cases: {OUT}')


def plots():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    d = json.loads((OUT/'results.json').read_text())
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':220})
    colors = ['#245C8C','#4E9D91','#D69A34','#945B97']
    def save(fig,name):
        fig.savefig(OUT/(name+'.png'),bbox_inches='tight',facecolor='white')
        fig.savefig(OUT/(name+'.svg'),bbox_inches='tight',facecolor='white')
        plt.close(fig)
    models = sorted(d['summary']['by_model'])
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    for ax,m in zip(axes,models):
        ts=[t for t in d['tables'] if t['model']==m]; x=np.arange(4)
        for offset,metric,color in [(-.19,'strict',colors[0]),(.19,'loose',colors[1])]:
            vals=[100*t[metric+'_prompt']['value'] for t in ts]; bars=ax.bar(x+offset,vals,.36,label=metric.title(),color=color)
            ax.bar_label(bars,labels=[f"{t[metric+'_prompt']['n']}/{t['usable']}" for t in ts],padding=3,fontsize=8)
        ax.set(xticks=x,xticklabels=CONDITIONS,ylim=(0,100),title=m,ylabel='Prompt pass rate (%)'); ax.legend(frameon=False); ax.grid(axis='y',alpha=.15)
    fig.suptitle('Instruction following after one revision | merged results')
    save(fig,'01_prompt_accuracy')
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    for ax,m in zip(axes,models):
        cs=[c for c in d['contrasts'] if c['model']==m]; y=np.arange(len(cs))
        for i,c in enumerate(cs):
            ax.errorbar(c['point']*100,i,xerr=[[100*(c['point']-c['low'])],[100*(c['high']-c['point'])]],fmt='o',color=colors[3] if c['exploratory'] else colors[0],capsize=4)
            ax.text(18,i,f"n={c['n_pairs']}",va='center',fontsize=8)
        ax.set(yticks=y,yticklabels=[c['contrast']+(' *' if c['exploratory'] else '') for c in cs],xlim=(-4,22),title=m,xlabel='Paired strict difference (percentage points)'); ax.invert_yaxis(); ax.axvline(0,color='grey',ls='--'); ax.axhline(2.5,color='grey',alpha=.3)
    fig.suptitle('Paired effects | 95% cluster bootstrap intervals; * exploratory, unadjusted')
    save(fig,'02_paired_effects')
    fig,axes=plt.subplots(1,2,figsize=(12,4.8),layout='constrained')
    for ax,m in zip(axes,models):
        b=d['summary']['by_model'][m]; x=np.arange(3)
        for offset,metric,color in [(-.19,'repair',colors[1]),(.19,'regress',colors[3])]:
            rs=[b[metric][c] for c in CONDITIONS[1:]]; bars=ax.bar(x+offset,[100*r['value'] for r in rs],.36,label='Repair' if metric=='repair' else 'Regression',color=color); ax.bar_label(bars,labels=[f"{r['n']}/{r['d']}" for r in rs],padding=3,fontsize=9)
        ax.set(xticks=x,xticklabels=CONDITIONS[1:],ylim=(0,42),title=m,ylabel='Rate within eligible B0 subgroup (%)'); ax.legend(frameon=False); ax.grid(axis='y',alpha=.15)
    fig.suptitle('Repair of failed answers and regression of passing answers | strict')
    save(fig,'03_repair_regression')
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),layout='constrained'); ax=axes[0]; bottom=np.zeros(4)
    for label,color in [('complete',colors[1]),('partial',colors[2]),('incomplete',colors[3]),('cannot_judge','#7A7A7A')]:
        vals=np.array([d['content_by_condition'][c].get(label,0) for c in CONDITIONS]); bars=ax.bar(CONDITIONS,vals,bottom=bottom,label=label.replace('_',' ').title(),color=color)
        if label=='complete': ax.bar_label(bars,label_type='center',color='white')
        bottom+=vals
    ax.set(ylim=(0,560),ylabel='Reviewed responses (520 per condition)',title='Content completion'); ax.legend(loc='lower left',frameon=False)
    ax=axes[1]; labels=['complete','partial','incomplete','cannot_judge']; x=np.arange(4)
    for offset,key,color in [(-.19,'strict_pass',colors[0]),(.19,'strict_fail',colors[3])]:
        bars=ax.bar(x+offset,[d['content_vs_strict'].get(l,{}).get(key,0) for l in labels],.36,label=key.replace('_',' ').title(),color=color); ax.bar_label(bars,padding=3,fontsize=8)
    ax.set(xticks=x,xticklabels=[l.replace('_',' ').title() for l in labels],ylim=(0,2300),ylabel='Responses',title='Content and rule compliance'); ax.legend(frameon=False)
    fig.suptitle('Content labels | 260 test items × 2 models × 4 conditions')
    save(fig,'04_content_review')
    print('Rendered four figures in PNG and editable SVG.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--plots',action='store_true'); args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    plots() if args.plots else build()
