import argparse, collections, json, multiprocessing, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import binaryDB_build
from binaryDB_build import ROOT, WEIGHTS, SIMILARITY_MIN, CONFIDENCE_MIN, signature, vectors, py_bsim

TARGETS = ROOT / 'testset/bin'
SOURCE_RESULTS = ROOT / 'output'
TARGET_SIG = ROOT / 'binaryDB/target_sig'
WORKERS_MAX = 8

CANDIDATES_MAX = 80  # components compared per function
CONFIRM_MIN = 2      # matched functions needed to confirm a component

def match(functions, records, index, weights):
    byref={}
    for name,v in functions:
        counts=collections.Counter()
        for h in v:counts.update(index.get(h,()))
        matched={}
        for i,_ in counts.most_common():
            c,_,rv=records[i]
            if c in matched:continue
            if len(matched)>=CANDIDATES_MAX:break
            sim=py_bsim.lshcosine_similarity(v,rv,weights)
            conf=py_bsim.lshcosine_confidence(v,rv,weights) if sim>=SIMILARITY_MIN else 0
            matched[c]=(sim,conf,i) if conf>=CONFIDENCE_MIN else None
        matched={c:m for c,m in matched.items() if m}
        if len(matched)!=1:continue  # shared by several components
        (c,(sim,conf,i)),=matched.items()
        if (c,i) not in byref or (sim,conf)>byref[c,i][:2]:byref[c,i]=(sim,conf,name)
    votes=collections.defaultdict(dict)
    for (c,i),(sim,conf,name) in byref.items():votes[c][name]=(sim,conf,records[i][1])
    return votes

def compute_votes(target):
    return target.name,match(vectors(signature(target,TARGET_SIG),_weights),_records,_index,_weights)

def all_votes(targets, records, index):
    global _records,_index,_weights
    _records,_index,_weights=records,index,py_bsim.BSimWeights.load_from_path(WEIGHTS)
    workers=min(WORKERS_MAX,os.cpu_count() or 1,len(targets));found={}
    with multiprocessing.get_context('fork').Pool(workers) as pool:
        for number,(name,votes) in enumerate(pool.imap_unordered(compute_votes,targets),1):
            print(f'[{number}/{len(targets)}] Matched: {name}',flush=True);found[name]=votes
    return found

def load_source(name):
    path=SOURCE_RESULTS/name/(name+'.components.json')
    if not path.is_file():return None
    grouped=collections.defaultdict(list)
    for entry in json.loads(path.read_text()):grouped[entry['name'].lower()].append(entry)
    return grouped

def result_path(name):
    return SOURCE_RESULTS/name/(name+'.binaryDB.components.json')

def sibling_of(name):
    if name.endswith('O0'):return name[:-2]+'O2'
    if name.endswith('O2'):return name[:-2]+'O0'
    return None

def tagged(entry, value):
    entry=dict(entry)
    entry['properties']=list(entry.get('properties',[]))+[{'name':'binaryDBModule:evidence','value':value}]
    return entry

def resolve(mine, other, votes, tails):
    def support(name):
        return max((len(votes.get(c,())) for c in tails.get(name,())),default=0)
    components=[];decisions={}
    for name,entries in mine.items():
        if other is None or name in other:
            components.extend(entries);continue
        count=support(name)
        if name not in tails:decision='kept: disputed, not in binary DB'
        elif count>=CONFIRM_MIN:decision=f'kept: disputed, {count} reference functions matched'
        else:decision=f'removed: disputed, {count} reference functions matched'
        decisions[name]=decision
        if not decision.startswith('removed'):components.extend(entries)
    for name,entries in (other or {}).items():
        count=support(name)
        if name in mine or count<CONFIRM_MIN:continue
        decisions[name]=f'added from sibling: {count} reference functions matched'
        components.extend(tagged(x,'sibling source result confirmed by BSim') for x in entries)
    return components,decisions

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('binaries', nargs='*', help='Target binary names (default: all in testset/bin)')
    a=p.parse_args(argv)
    if a.binaries:
        if any(Path(name).name != name for name in a.binaries): p.error('binary names must not contain paths')
        targets=[TARGETS/name for name in a.binaries]
        missing=[str(path) for path in targets if not path.is_file()]
        if missing: p.error('target binaries not found: '+', '.join(missing))
    else:
        targets=sorted(x for x in TARGETS.iterdir() if x.is_file())
    if not targets: p.error(f'no target binaries in {TARGETS}')
    sources={t.name:load_source(t.name) for t in targets}
    for name in [name for name,source in sources.items() if source is None]:
        print(f'{name}: no source SCA result, skipped',file=sys.stderr)
    targets=[t for t in targets if sources[t.name] is not None]
    if not binaryDB_build.DATABASE.is_file(): p.error('reference database not found; run src/binaryDB_build.py first')
    records,index,stale=binaryDB_build.load()
    if stale: p.error('binaryDB/db_input/ or CONFIDENCE_MIN changed since the reference database was built; rerun src/binaryDB_build.py')
    found=all_votes(targets,records,index)
    tails=collections.defaultdict(list)
    for c in sorted({r[0] for r in records}):tails[c.split('@@')[-1]].append(c)
    for target in targets:
        name=target.name
        votes=found[name]
        sibling=sibling_of(name)
        other=load_source(sibling) if sibling else None
        components,decisions=resolve(sources[name],other,votes,tails)
        print(f'{name}: {len(components)} components',flush=True)
        for subject,decision in decisions.items():print(f'    {subject}: {decision}',flush=True)
        result_path(name).write_text(json.dumps(components,indent=4))
    return {t.name for t in targets}
if __name__=='__main__':main()
