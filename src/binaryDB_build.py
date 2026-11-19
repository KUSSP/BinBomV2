import argparse, collections, hashlib, pickle, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'py-bsim'))
import py_bsim

GHIDRA = ROOT / 'tools/ghidra/ghidra_11.2.1_PUBLIC'
WEIGHTS = GHIDRA / 'Ghidra/Features/BSim/data/lshweights_64.xml'
REFERENCES = ROOT / 'binaryDB/db_input'
DB_SIG = ROOT / 'binaryDB/db_sig'
DATABASE = ROOT / 'binaryDB/db_index.pkl'

SIMILARITY_MIN = 0.75
CONFIDENCE_MIN = 10
FEATURE_RECORDS_MAX = 100  # skip features that are too common

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024*1024), b''): h.update(part)
    return h.hexdigest()

def signature(binary, cache):
    target = cache / (digest(binary) + '.xml')
    if target.is_file(): return target
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='bindet-') as tmp:
        root = Path(tmp); project = root/'project'; out = root/'signatures'
        project.mkdir(); out.mkdir()
        commands = [
            [str(GHIDRA/'support/analyzeHeadless'), str(project), 'project', '-import', str(binary.resolve()), '-max-cpu', '2'],
            [str(GHIDRA/'support/bsim'), 'generatesigs', f'ghidra:{project}/project', str(out), '--config', 'medium_64', '--overwrite'],
        ]
        for command in commands:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if result.returncode:
                raise RuntimeError(f'Signature generation failed for {binary} (exit {result.returncode}):\n{result.stdout[-6000:]}')
        # Ghidra writes XML as sigs_<hash>, without an .xml extension.
        produced=[path for path in out.iterdir() if path.is_file()]
        if len(produced)!=1:
            raise RuntimeError(f'Expected one signature for {binary}, got {len(produced)}. Ghidra output:\n{result.stdout[-6000:]}')
        temporary = target.with_suffix('.tmp')
        try:
            shutil.copyfile(produced[0], temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target

def vectors(path, weights):
    dump=py_bsim.BSimSignatureDump.load_from_path(path)
    for name,desc in dump.as_combined_function_dict().items():
        vec=desc.lshcosine_hashes
        if vec and py_bsim.lshcosine_self_significance(vec,weights)>=CONFIDENCE_MIN: yield name,vec

def component(path):
    parts=path.name.rsplit('__',1)
    if len(parts)!=2: raise ValueError(f'Reference name must be owner__repo__binary: {path.name}')
    return parts[0].replace('__','@@').lower()

def references():
    return sorted(x for x in REFERENCES.iterdir() if x.is_file() and not x.name.startswith('.'))

def load():
    with DATABASE.open('rb') as f:db=pickle.load(f)
    stale=db['references']!={x.name:x.stat().st_size for x in references()} or db.get('confidence_min')!=CONFIDENCE_MIN
    return db['records'],db['index'],stale

def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    refs=references()
    if not refs:sys.exit(f'no reference binaries in {REFERENCES}')
    weights=py_bsim.BSimWeights.load_from_path(WEIGHTS); index=collections.defaultdict(list); records=[];seen=set()
    for number,ref in enumerate(refs,1):
        c=component(ref)
        print(f'[{number}/{len(refs)}] Reference: {ref.name}', flush=True)
        for name,v in vectors(signature(ref,DB_SIG),weights):
            key=(c,tuple(sorted(v.items())))
            if key in seen:continue
            seen.add(key);i=len(records);records.append((c,name,v))
            for h in v:index[h].append(i)
    index={h:v for h,v in index.items() if len(v)<=FEATURE_RECORDS_MAX}
    temporary=DATABASE.with_suffix('.tmp')
    with temporary.open('wb') as f:
        pickle.dump({'confidence_min':CONFIDENCE_MIN,'references':{x.name:x.stat().st_size for x in refs},'records':records,'index':index},f,pickle.HIGHEST_PROTOCOL)
    temporary.replace(DATABASE)
    print(f'{DATABASE}: {len(records)} functions, {len({r[0] for r in records})} components')
if __name__=='__main__':main()
