import argparse, collections, json, multiprocessing, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import binaryDB_build, detector
from binaryDB_build import ROOT, WEIGHTS, SIMILARITY_MIN, CONFIDENCE_MIN, signature, vectors, calls, py_bsim

TARGETS = ROOT / 'testset/bin'
SOURCE_RESULTS = ROOT / 'output'
TARGET_SIG = ROOT / 'binaryDB/target_sig'
WORKERS_MAX = 8

CANDIDATES_MAX = 80  # components compared per function
COMPONENT_PROPERTY = detector.COMPONENT_PROPERTY

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
    sig=signature(target,TARGET_SIG)
    return target.name,(match(vectors(sig,_weights),_records,_index,_weights),calls(sig))

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
    for entry in json.loads(path.read_text()):
        ids=[x['value'] for x in entry.get('properties',[]) if x.get('name')==COMPONENT_PROPERTY]
        if not ids:raise ValueError(f'{path}: component {entry.get("name")!r} has no {COMPONENT_PROPERTY}; rerun src/detector.py')
        grouped[ids[0]].append(entry)
    return grouped

def result_path(name):
    return SOURCE_RESULTS/name/(name+'.binaryDB.components.json')

def preserved_calls(matched, reference_calls, target_calls):
    """Reference calls between two matched functions that the target makes too."""
    pairs=[(t,r) for t,(_,_,r) in matched.items()]
    return sum(1 for t1,r1 in pairs for t2,r2 in pairs if (r1,r2) in reference_calls and (t1,t2) in target_calls)

def resolve(mine, votes, references, reference_calls, target_calls):
    """Never removes a source result: no match may only mean no matching reference.
    Adds a missed component only when two matched functions call each other in
    both the reference and the target."""
    components=[entry for entries in mine.values() for entry in entries];decisions={}
    for c in mine:
        count=len(votes.get(c,()))
        decisions[c]='kept: not in binary DB' if c not in references else f'kept: {count} of {references[c]} reference functions matched'
    for c,matched in sorted(votes.items()):
        if c in mine:continue
        found=f'{len(matched)} of {references[c]} reference functions matched'
        preserved=preserved_calls(matched,reference_calls.get(c,set()),target_calls)
        if not preserved:decisions[c]=f'not added: {found}, but no call between them is in the target';continue
        entry=detector.component_entry(c)
        if entry is None:decisions[c]=f'not added: {found}, {preserved} calls preserved, but no release metadata';continue
        evidence=f'BSim: {found}, {preserved} reference calls between them preserved'
        entry['properties'].append({'name':'binaryDBModule:evidence','value':evidence})
        decisions[c]=f'added: {found}, {preserved} calls preserved'
        components.append(entry)
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
    try:sources={t.name:load_source(t.name) for t in targets}
    except ValueError as e:p.error(str(e))
    for name in [name for name,source in sources.items() if source is None]:
        print(f'{name}: no source SCA result, skipped',file=sys.stderr)
    targets=[t for t in targets if sources[t.name] is not None]
    if not binaryDB_build.DATABASE.is_file(): p.error('reference database not found; run src/binaryDB_build.py first')
    records,index,reference_calls,stale=binaryDB_build.load()
    if stale: p.error('the reference database is out of date (binaryDB/db_input/, CONFIDENCE_MIN or its format changed); rerun src/binaryDB_build.py')
    found=all_votes(targets,records,index)
    references=collections.Counter(r[0] for r in records)
    for target in targets:
        name=target.name
        votes,target_calls=found[name]
        components,decisions=resolve(sources[name],votes,references,reference_calls,target_calls)
        print(f'{name}: {len(components)} components',flush=True)
        for subject,decision in decisions.items():print(f'    {subject}: {decision}',flush=True)
        result_path(name).write_text(json.dumps(components,indent=4))
    return {t.name for t in targets}
if __name__=='__main__':main()
