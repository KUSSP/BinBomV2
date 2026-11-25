import argparse, collections, hashlib, json, pickle, shutil, struct, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'py-bsim'))
import py_bsim

GHIDRA = ROOT / 'tools/ghidra/ghidra_11.2.1_PUBLIC'
WEIGHTS = GHIDRA / 'Ghidra/Features/BSim/data/lshweights_64.xml'
REFERENCES = ROOT / 'binaryDB/db_input'
DB_SIG = ROOT / 'binaryDB/db_sig'
DATABASE = ROOT / 'binaryDB/db_index.pkl'
META_SUFFIX = '.meta.json'

SIMILARITY_MIN = 0.75
CONFIDENCE_MIN = 10
FEATURE_RECORDS_MAX = 100  # skip features that are too common
DB_FORMAT = 2  # bump when what a built database holds changes

# crt startup/teardown and PLT stubs come from the toolchain, not the component.
TOOLCHAIN_FUNCTIONS = {
    '_init', '_fini', '_start', '_dl_relocate_static_pie', 'deregister_tm_clones',
    'register_tm_clones', '__do_global_dtors_aux', 'frame_dummy', '__libc_csu_init', '__libc_csu_fini',
}
GHIDRA_IMAGE_BASES = (0, 0x10000, 0x100000)  # where Ghidra may load an ELF image

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

def elf_sections(binary):
    """(name, address, size, executable) of each ELF section; [] for other formats."""
    data=binary.read_bytes()
    if data[:4]!=b'\x7fELF' or data[4] not in (1,2) or data[5] not in (1,2):return []
    wide=data[4]==2;end='<' if data[5]==1 else '>';word=end+('Q' if wide else 'I')
    try:
        shoff,=struct.unpack_from(word,data,0x28 if wide else 0x20)
        shentsize,shnum,shstrndx=struct.unpack_from(end+'HHH',data,0x3A if wide else 0x2E)
        def header(i):
            off=shoff+i*shentsize
            name,=struct.unpack_from(end+'I',data,off)
            if wide:flags,addr,offset,size=struct.unpack_from(end+'QQQQ',data,off+8)
            else:flags,addr,offset,size=struct.unpack_from(end+'IIII',data,off+8)
            return name,flags,addr,offset,size
        strtab=header(shstrndx)[3]
        sections=[]
        for i in range(shnum):
            name,flags,addr,_,size=header(i)
            label=data[strtab+name:data.index(b'\0',strtab+name)].decode(errors='replace')
            sections.append((label,addr,size,bool(flags&4)))
        return sections
    except (struct.error,ValueError,IndexError):return []

def toolchain_addresses(binary, dump):
    """Ghidra addresses that fall in a PLT section of the binary."""
    sections=elf_sections(binary)
    code=[(a,a+s) for _,a,s,x in sections if x and s]
    plt=[(a,a+s) for n,a,s,_ in sections if n.startswith('.plt') and s]
    addrs=[d.addr for d in dump.as_combined_function_dict().values() if d.addr is not None]
    if not plt or not addrs:return lambda addr:False
    base=max(GHIDRA_IMAGE_BASES,key=lambda b:sum(1 for x in addrs if any(lo<=x-b<hi for lo,hi in code)))
    return lambda addr:addr is not None and any(lo<=addr-base<hi for lo,hi in plt)

def vectors(path, weights, binary=None):
    """Function vectors of a signature. Given the binary it was made from, the
    toolchain's own functions are left out (see TOOLCHAIN_FUNCTIONS)."""
    dump=py_bsim.BSimSignatureDump.load_from_path(path)
    in_plt=toolchain_addresses(binary,dump) if binary else (lambda addr:False)
    for name,desc in dump.as_combined_function_dict().items():
        if binary and (name in TOOLCHAIN_FUNCTIONS or in_plt(desc.addr)):continue
        vec=desc.lshcosine_hashes
        if vec and py_bsim.lshcosine_self_significance(vec,weights)>=CONFIDENCE_MIN: yield name,vec

def calls(path):
    # (caller, callee) function-name pairs from the signature's call records
    dump=py_bsim.BSimSignatureDump.load_from_path(path)
    return {(name,call.dest) for name,desc in dump.as_combined_function_dict().items() for call in (desc.calls or []) if call.dest}

def metadata_path(path):
    return path.with_name(path.name+META_SUFFIX)

def component(path):
    # The component id must match the component DB (osscollector/repo_src/<owner@@repo>).
    meta=metadata_path(path)
    try:value=json.loads(meta.read_text()).get('component')
    except (OSError,ValueError) as e:raise ValueError(f'cannot read {meta.name} for reference {path.name}: {e}') from e
    if not isinstance(value,str) or '@@' not in value:raise ValueError(f'{meta.name}: "component" must be "owner@@repo", got {value!r}')
    return value

def references():
    return sorted(x for x in REFERENCES.iterdir() if x.is_file() and not x.name.startswith('.') and not x.name.endswith(META_SUFFIX))

def fingerprint(refs):
    return {x.name:[x.stat().st_size,component(x)] for x in refs}

def load():
    with DATABASE.open('rb') as f:db=pickle.load(f)
    try:current=fingerprint(references())
    except ValueError:current=None  # metadata missing or broken; binaryDB_build.py reports it
    stale=db['references']!=current or db.get('confidence_min')!=CONFIDENCE_MIN or db.get('format')!=DB_FORMAT
    return db['records'],db['index'],db.get('calls',{}),stale

def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    refs=references()
    if not refs:sys.exit(f'no reference binaries in {REFERENCES}')
    try:references_fingerprint=fingerprint(refs)
    except ValueError as e:sys.exit(f'[!] {e}')
    weights=py_bsim.BSimWeights.load_from_path(WEIGHTS); index=collections.defaultdict(list); records=[];seen=set()
    edges=collections.defaultdict(set)  # component -> reference call edges
    for number,ref in enumerate(refs,1):
        c=references_fingerprint[ref.name][1]
        print(f'[{number}/{len(refs)}] Reference: {ref.name}', flush=True)
        sig=signature(ref,DB_SIG);edges[c]|=calls(sig)
        for name,v in vectors(sig,weights,ref):
            key=(c,tuple(sorted(v.items())))
            if key in seen:continue
            seen.add(key);i=len(records);records.append((c,name,v))
            for h in v:index[h].append(i)
    index={h:v for h,v in index.items() if len(v)<=FEATURE_RECORDS_MAX}
    temporary=DATABASE.with_suffix('.tmp')
    with temporary.open('wb') as f:
        pickle.dump({'format':DB_FORMAT,'confidence_min':CONFIDENCE_MIN,'references':references_fingerprint,'records':records,'index':index,'calls':dict(edges)},f,pickle.HIGHEST_PROTOCOL)
    temporary.replace(DATABASE)
    print(f'{DATABASE}: {len(records)} functions, {len({r[0] for r in records})} components')
if __name__=='__main__':main()
