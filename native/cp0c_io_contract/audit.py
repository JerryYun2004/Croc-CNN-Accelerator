#!/usr/bin/env python3
"""Read-only I/O contract/model audit. Never changes design constraints."""
import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from base_support import require,sha,write_json,table
from model import groups,one_group,attr,read_table,read_model,compare_report

VERSION='1.0-io-contract'
CELLS=('sg13cmos5l_IOPadInOut30mA','sg13cmos5l_IOPadOut16mA')
CORNERS=('tt','ff','ss')

class Evidence:
    """Read only known regular members; never extract/execute an archive."""
    def __init__(self,path):
        self.path=Path(path).resolve(); self.before={}; self.tar=None
        if self.path.is_file():
            self.before[str(self.path)]=sha(self.path)
            self.tar=tarfile.open(self.path,'r:gz'); members=self.tar.getmembers()
            require(len(members)<2000 and sum(m.size for m in members)<32*1024**2,'Evidence archive is unexpectedly large')
            require(len({m.name for m in members})==len(members),'Duplicate archive member')
            roots=set()
            for m in members:
                p=Path(m.name); require(not p.is_absolute() and '..' not in p.parts and (m.isfile() or m.isdir()),'Unsafe archive member')
                roots.add(p.parts[0])
            require(len(roots)==1,'Evidence must have one root directory'); self.root=next(iter(roots))
        else:
            require(self.path.is_dir(),'Missing completed pad-model evidence')
    def read(self,rel):
        p=Path(rel);require(not p.is_absolute() and '..' not in p.parts,'Invalid evidence path')
        if self.tar:
            member=self.tar.getmember(self.root+'/'+rel);require(member.isfile(),'Evidence member is not a regular file')
            return self.tar.extractfile(member).read()
        file=self.path/rel;require(file.is_file() and not file.is_symlink(),'Missing or linked evidence file: '+rel)
        data=file.read_bytes();self.before[str(file)]=hashlib.sha256(data).hexdigest();return data
    def json(self,rel):return json.loads(self.read(rel))
    def changed(self):return [p for p,h in self.before.items() if not Path(p).is_file() or sha(p)!=h]
    def close(self):
        if self.tar:self.tar.close()

def library_cells(path):
    """Read both real output masters, without extrapolating any table."""
    lib=one_group(Path(path).read_text(),'library')
    require(attr(lib,'time_unit')=='1ns' and re.search(r'capacitive_load_unit\s*\(1,\s*pf\)',lib),'Unexpected library units')
    limit=float(attr(lib,'default_max_transition'));require(math.isfinite(limit) and limit>0,'Invalid slew limit')
    templates=dict(groups(lib,'lu_table_template'))
    for value in templates.values():
        require(attr(value,'variable_1')=='input_net_transition' and attr(value,'variable_2')=='total_output_net_capacitance','Wrong axes')
    result={}
    for name in CELLS:
        cell=one_group(lib,'cell',name);pad=one_group(cell,'pin','pad')
        cap=float(attr(pad,'max_capacitance'));require(math.isfinite(cap) and cap>0,'Invalid capacitance limit')
        require(not re.search(r'\bmax_transition\s*:',pad),'New explicit pad transition limit needs review')
        arcs={}
        for _,timing in groups(pad,'timing'):
            kind=attr(timing,'timing_type');require(kind not in arcs,'Duplicate timing group')
            arcs[kind]={k:read_table(timing,k,templates) for k in ['cell_rise','cell_fall','rise_transition','fall_transition']}
        expected={'combinational','three_state_enable','three_state_disable'} if 'InOut' in name else {'combinational'}
        require(set(arcs)==expected,'Unexpected pad timing groups')
        result[name]=dict(max_capacitance_pf=cap,max_transition_ns=limit,arcs=arcs)
    return result

def summarize(models):
    cells=[];tables=[]
    for corner in CORNERS:
        for name,m in models[corner].items():
            data=m['arcs']['combinational'];cap=m['max_capacitance_pf'];limit=m['max_transition_ns']
            cap_min=max(t['y'][0] for t in data.values());cap_max=min(t['y'][-1] for t in data.values())
            row=dict(corner=corner,cell=name,max_capacitance_pf=cap,max_transition_ns=limit,
                     data_table_total_cap_min_pf=cap_min,data_table_total_cap_max_pf=cap_max,
                     cap_limit_overlaps_data_table_domain=cap>=cap_min,
                     external_15pf_alone_exceeds_cap_limit=15>cap,
                     external_3pf_alone_exceeds_cap_limit=3>cap)
            for direction in ('rise','fall'):
                minimum=min(v for values in data[direction+'_transition']['values'] for v in values)
                row['min_data_'+direction+'_ns']=minimum
                row['data_'+direction+'_min_above_limit']=minimum>limit
            cells.append(row)
            for kind,ts in m['arcs'].items():
                for direction in ('rise','fall'):
                    t=ts[direction+'_transition'];v=[x for values in t['values'] for x in values]
                    tables.append(dict(corner=corner,cell=name,arc=kind,direction=direction,
                        min_transition_ns=min(v),max_transition_ns=max(v),limit_ns=limit,min_exceeds_limit=min(v)>limit,
                        transition_equals_delay_table=t==ts['cell_'+direction],
                        input_slew_min_ns=t['x'][0],input_slew_max_ns=t['x'][-1],
                        total_cap_min_pf=t['y'][0],total_cap_max_pf=t['y'][-1]))
    return cells,tables

def blockers(cells,tables):
    result=[]
    for r in cells:
        if not r['cap_limit_overlaps_data_table_domain']:
            result.append(dict(id='CAP_DOMAIN_EMPTY',corner=r['corner'],cell=r['cell'],
                detail=f"Maximum capacitance {r['max_capacitance_pf']} pF is below the data-table lower bound {r['data_table_total_cap_min_pf']} pF."))
        for direction in ('rise','fall'):
            if r['data_'+direction+'_min_above_limit']:
                result.append(dict(id='DATA_SLEW_DOMAIN_FAIL',corner=r['corner'],cell=r['cell'],
                    detail=f"Smallest tabulated data {direction} transition {r['min_data_'+direction+'_ns']} ns exceeds {r['max_transition_ns']} ns."))
    for r in tables:
        if r['arc']=='three_state_disable' and r['min_exceeds_limit']:
            result.append(dict(id='DISABLE_SLEW_DOMAIN_FAIL',corner=r['corner'],cell=r['cell'],
                detail=f"Smallest tabulated disable {r['direction']} transition {r['min_transition_ns']} ns exceeds {r['limit_ns']} ns."))
    result.append(dict(id='IO_CONTRACT_UNDEFINED',corner='all',cell='all',
        detail='Package/board loading, external input drivers, GPIO turnaround and required signoff corners are not specified.'))
    return result

def validate_draft(contract):
    require(contract['status']=='DRAFT_UNAPPROVED','This checkpoint cannot approve an I/O contract')
    require(contract['active_sdc_change'] is False and contract['model_change'] is False,'This checkpoint does not apply model or constraint changes')
    require(contract['intended_package'] is None and contract['intended_board'] is None,'Board assumptions must be reviewed before this draft is updated')
    require(contract['research_diagnostic']['approved_as_interface_spec'] is False,'Diagnostic settings are not an approved interface')
    require(contract['turnaround']['approved'] is False,'No physical turnaround proof is available')
    return True

def verify_native(evidence,pins,lib_paths):
    for rel,digest in pins['native_files'].items():
        require(hashlib.sha256(evidence.read(rel)).hexdigest()==digest,'Native evidence checksum differs: '+rel)
    con=evidence.json('reports/conclusion.json');controls=evidence.json('reports/controls.json')
    require(con['status']=='PAD_DISABLE_MODEL_REPRODUCED' and con['controls_verified'] and con['warning_count']==0,'Native prerequisite is incomplete')
    require(controls['verified'] and len(controls['tests'])==14 and all(controls['tests'].values()),'Native controls are incomplete')
    require(evidence.json('reports/preservation.json')=={'unchanged':True,'changed':[]},'Previous inputs changed')
    require(evidence.read('exit-status.txt').strip()==b'0','Previous stage exited unsuccessfully')
    checked=[]
    for corner in ('tt','ff'):
        model=read_model(lib_paths[corner])
        require(model==evidence.json('reports/'+corner+'-model.json'),'Native model differs from pinned source')
        for case in pins['native_dynamic_cases']:
            for kind,kinds in [('data',['data']),('enable',['enable','disable'])]:
                data=compare_report(model,evidence.read(f'cases/{case}/reports/arcs/{corner}-{kind}.rpt').decode(),kinds)
                require(all(r['within_characterized_domain'] for r in data),'Native oracle uses extrapolation')
                checked += [dict(case=case,corner=corner,**r) for r in data]
    require(len(checked)==60,'Incomplete native fixture coverage')
    return checked

def perform(here,source,out):
    pins=json.loads((here/'pins.json').read_text());preserve={}
    def package_file(rel,digest=None):
        p=here/rel;h=sha(p);require(digest is None or h==digest,'Bundled input differs: '+rel)
        preserve[str(p.resolve())]=h;return p
    libs={c:package_file('models/'+c+'.lib',pins['libraries'][c]['sha256']) for c in CORNERS}
    contract_path=package_file('io-contract.draft.json',pins['draft_sha256'])
    contract=json.loads(contract_path.read_text());validate_draft(contract)
    inventory=package_file('io-ports.tsv',pins['inventory_sha256'])
    with inventory.open() as f:ports=list(csv.DictReader(f,delimiter='\t'))
    require(len(ports)==39 and len({r['port'] for r in ports})==39,'Output port inventory differs')
    require(sum(r['pad_master']==CELLS[0] for r in ports)==32 and sum(r['pad_master']==CELLS[1] for r in ports)==7,'Pad master inventory differs')
    reports=out/'reports';reports.mkdir();evidence=Evidence(source)
    try:
        print('STAGE: verify completed native single-pad evidence',flush=True)
        native=verify_native(evidence,pins,libs);table(reports/'native-oracle.tsv',native)
        print('STAGE: audit TT/FF/SS models for both output masters',flush=True)
        models={c:library_cells(path) for c,path in libs.items()};cells,tables=summarize(models)
        table(reports/'cell-limits.tsv',cells);table(reports/'arc-bounds.tsv',tables)
        write_json(reports/'models.json',models)
        findings=blockers(cells,tables);write_json(reports/'blockers.json',findings)
        coverage=[]
        for port in ports:
            for c in CORNERS:
                limit=models[c][port['pad_master']]['max_capacitance_pf']
                coverage.append(dict(port=port['port'],master=port['pad_master'],corner=c,
                    max_total_capacitance_pf=limit,baseline_external_load_pf=15,
                    original_15pf_exceeds_cap_limit=15>limit,
                    diagnostic_3pf_exceeds_cap_limit=3>limit,
                    approved_external_load_pf='UNSPECIFIED'))
        table(reports/'port-load-audit.tsv',coverage)
        write_json(reports/'io-contract.draft.json',contract)
        for rel in ('review.md','characterization-plan.md','maintainer-reproducer.md','source-trace.json'):
            shutil.copy2(package_file(rel,pins['review_files'][rel]),reports/rel)
        write_json(out/'provenance.json',dict(version=VERSION,input=str(Path(source).resolve()),
            io_pdk_pin=pins['io_pdk_pin'],opensta_pin=pins['opensta_pin'],models=pins['libraries'],
            native_corners_verified=['tt','ff'],new_static_table_review=['ss'],
            new_openroad_processes=0,spice_processes=0,full_chip_sta_rerun=False,
            no_constraint_edits=True,no_model_edits=True,no_design_edits=True))
        changed=evidence.changed()+[p for p,h in preserve.items() if not Path(p).is_file() or sha(p)!=h]
        write_json(reports/'preservation.json',dict(unchanged=not changed,changed=changed));require(not changed,'Inputs changed during audit')
        write_json(reports/'conclusion.json',dict(status='IO_CONTRACT_AUDIT_COMPLETE',cp0c='PENDING',
            electrical_closure='BLOCKED_BY_MODEL_AND_INTERFACE_REQUIREMENTS',native_fixture_arcs_rechecked=len(native),
            audited_cells=2,audited_library_corners=3,output_ports=39,blockers=len(findings),
            native_slow_corner_measured=False,physical_disable_characterization_available=False,
            approved_external_load_pf=None,approved_turnaround_ns=None,
            approved_model_patch=False,approved_timing_exception=False,
            development_baseline_qualified=False,design_changed=False,
            next='Choose development-baseline versus fabrication-signoff scope; specify the interface and resolve the model characterization before claiming electrical closure.'))
        (out/'source.sha256').write_text(''.join(f'{h}  {p}\n' for p,h in sorted({**preserve,**evidence.before}.items())))
        (out/'status.txt').write_text('IO_CONTRACT_AUDIT_COMPLETE\nCP0C_PENDING\n')
    finally:evidence.close()

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('evidence',type=Path)
    ap.add_argument('--results',type=Path,required=True,help='Existing parent directory for a new isolated audit run')
    args=ap.parse_args();require(sys.version_info>=(3,11),'Python 3.11+ is required')
    require(args.results.is_dir(),'Results parent directory does not exist')
    source=args.evidence.resolve();parent=args.results.resolve()
    require(source!=parent and source not in parent.parents,'Do not create output inside source evidence')
    here=Path(__file__).resolve().parent
    out=Path(tempfile.mkdtemp(prefix='cp0c-io-contract-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-',dir=parent))
    print(f'Evidence: {out}\nVersion: {VERSION}\nRead-only Python audit; no EDA installation or full-chip run.',flush=True)
    code=1
    try:
        perform(here,source,out);code=0
    except (Exception,KeyboardInterrupt) as exc:
        (out/'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        (out/'status.txt').write_text('IO_CONTRACT_AUDIT_FAILED\nCP0C_PENDING\n')
        print('STOPPED: '+str(exc),file=sys.stderr)
    finally:
        (out/'exit-status.txt').write_text(str(code)+'\n')
        archive=Path(str(out)+'.tar.gz')
        with tarfile.open(archive,'w:gz') as t:
            for p in sorted(out.rglob('*')):
                if p.is_file():t.add(p,arcname=str(Path(out.name)/p.relative_to(out)),recursive=False)
        print((out/'status.txt').read_text(),flush=True)
        print(f'Upload: {archive}\nReview: {out}/reports/review.md',flush=True)
    return code

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:print(f'ERROR: {exc}',file=sys.stderr);sys.exit(1)
