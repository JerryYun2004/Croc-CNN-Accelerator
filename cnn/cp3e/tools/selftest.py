import json,re,shutil,tempfile
from pathlib import Path
from common import require,manifest,sha
from power import records,section,VIA
from logic import audit,graph,library
from checks import constraints,pdn,routing,timing,cell_supplies,independent
from electrical import inventory,progress,unescape
from flow import audit_selection

def controls(out,repo,evidence):
    d=out/'cnn';before=evidence/'cnn/global.def';libpath=repo/'ihp13/pdk/lib/sg13g2_stdcell_typ_1p20V_25C.lib'
    lib=library(libpath);g=graph(before,lib);text=(d/'global.def').read_text();tests=[]
    def rejects(name,fn):
        try:fn()
        except RuntimeError as e:tests.append(dict(name=name,status='REJECTED_AS_REQUIRED',reason=str(e)));return
        raise RuntimeError('Corruption control unexpectedly passed: '+name)
    with tempfile.TemporaryDirectory(prefix='cp3e-controls-') as tmp:
        t=Path(tmp);bad=t/'bad.def'
        require('sg13g2_nand2_1' in text,'Missing logic fixture gate')
        bad.write_text(text.replace('sg13g2_nand2_1','sg13g2_nor2_1',1));rejects('changed_leaf_function',lambda:audit(before,bad,libpath))
        targets=[p for p in g['anchors'] if g['cells'].get(p[0])=='RM_IHPSG13_1P_512x32_c2_bm_bist' and p[1].startswith('A_ADDR')]
        require(len(targets)>=2,'Missing SRAM address anchors');p0=targets[0];p1=next(p for p in targets[1:] if g['anchors'][p]!=g['anchors'][p0])
        a='( '+' '.join(p0)+' )';b='( '+' '.join(p1)+' )';require(a in text and b in text,'Missing pin-swap fixture')
        bad.write_text(text.replace(a,'@CP3E_PIN_SWAP@',1).replace(b,a,1).replace('@CP3E_PIN_SWAP@',b,1));rejects('swapped_sram_address_pins',lambda:audit(before,bad,libpath))
        moved=text.replace('+ FIXED ( 350240 352640 ) FS','+ FIXED ( 350245 352640 ) FS',1);require(moved!=text,'Missing macro fixture');bad.write_text(moved)
        rejects('moved_mirrored_sram',lambda:audit(before,bad,libpath))
        cells=records(text,'COMPONENTS');name=next(n for n in cells if n.startswith('cp3d_fanout_buf_'))
        final=graph(d/'global.def',lib)
        pg={n:set(v) for n,v in final['power'].items()}
        pg['VDD'].discard(('*','VDD'));pg['VDD'].discard((name,'VDD'))
        pg['VDD'].update((inst,'VDD') for inst,m in final['cells'].items() if m in lib and inst!=name)
        rejects('unpowered_inserted_buffer',lambda:cell_supplies(final['cells'],pg,lib))
        bad.write_text(text.replace(VIA,'BROKEN_SUPPLY_VIA',1));rejects('changed_pdn_via',lambda:pdn(before,bad,libpath))
        sdc=(d/'propagated.sdc').read_text();badsdc=t/'bad.sdc';badsdc.write_text(re.sub(r'^set_propagated_clock.*\n','',sdc,count=1,flags=re.M))
        rejects('missing_propagated_clock',lambda:constraints(d/'original.sdc',badsdc))
        changed=sdc.replace('create_clock -name clk_sys -period 10.0000','create_clock -name clk_sys -period 20.0000',1);require(changed!=sdc,'Missing clock-period fixture');badsdc.write_text(changed)
        rejects('relaxed_clock_period',lambda:constraints(d/'original.sdc',badsdc))
        for n in ['global.def','global.guide','cleanup.log','power.log']:shutil.copyfile(d/n,t/n)
        guide=(t/'global.guide').read_text();(t/'global.guide').write_text(guide[guide.index(')\n')+2:]);rejects('missing_route_guide',lambda:routing(t));(t/'global.guide').write_text(guide)
        log=(t/'cleanup.log').read_text();i=log.rindex('Final congestion report:');tail=log[i:]
        changed=re.sub(r'(Total\s+\d+\s+\d+\s+[\d.]+%\s+0 /\s*0 /\s*)0',r'\g<1>1',tail);require(changed!=tail,'Missing overflow fixture');(t/'cleanup.log').write_text(log[:i]+changed)
        rejects('nonzero_final_overflow',lambda:routing(t))
        shutil.copytree(d/'reports',t/'reports');(t/'reports/final-worst-max.rpt').write_text('worst slack max NaN\n');rejects('invalid_timing_metric',lambda:timing(t))
        rt=routing(d);tm=timing(d)
        shutil.copyfile(d/'independent.guide',t/'independent.guide')
        verified=(d/'independent-route.log').read_text();(t/'independent-route.log').write_text(verified+'\n[WARNING EST-0026] Missing route to pin fixture in net fixture.\n')
        rejects('independent_parasitics_warning',lambda:independent(t,rt,tm))
        changed=re.sub(r'worst slack max\s+[-+\d.eE]+','worst slack max 999.0',verified,count=1);require(changed!=verified,'Missing independent timing fixture');(t/'independent-route.log').write_text(changed)
        rejects('independent_timing_mismatch',lambda:independent(t,rt,tm))
        f=t/'input';f.write_text('accepted\n');(t/'SHA256SUMS').write_text(sha(f)+'  input\n');f.write_text('modified\n');rejects('changed_prerequisite_file',lambda:manifest(t))
        old=json.loads((d/'summary.json').read_text())['reference_timing']
        new=timing(d)['final'];ea=json.loads((d/'electrical-before.json').read_text());eb=inventory(d/'global.def',d/'reports');progress(old,new,ea,eb)
        unchanged=dict(new,worst_max_ns=old['worst_max_ns']);rejects('no_setup_improvement',lambda:progress(old,unchanged,ea,eb))
        negative=dict(new,worst_min_ns=-0.01);rejects('new_hold_violation',lambda:progress(old,negative,ea,eb))
        regression=json.loads(json.dumps(eb));regression['tt']['summary']['max slew']['internal']=ea['tt']['summary']['max slew']['internal']+1
        rejects('internal_electrical_regression',lambda:progress(old,new,ea,regression))
        severity=json.loads(json.dumps(eb));severity['tt']['summary']['max slew']['internal_worst_slack']=ea['tt']['summary']['max slew']['internal_worst_slack']-0.1
        rejects('internal_electrical_severity_regression',lambda:progress(old,new,ea,severity))
        fixture=t/'electrical';fixture.mkdir()
        pin=unescape(next(n for n,m in final['cells'].items() if m=='sg13g2_buf_8'))+'/X'
        for corner in ['tt','ff']:(fixture/f'final-{corner}-electrical.rpt').write_text('max fanout\n\n'+pin+'   8   9   (VIOLATED)\n')
        parsed=inventory(d/'global.def',fixture)
        require(all(parsed[c]['summary']['max fanout']['total']==1 and parsed[c]['violations'][0]['slack']==-1 for c in ['tt','ff']),'Blank fanout slack was dropped')
        (fixture/'final-tt-electrical.rpt').write_text('max slew\n'+pin+'   NaN  9  (VIOLATED)\n')
        rejects('malformed_electrical_violation',lambda:inventory(d/'global.def',fixture))
        submargin=dict(new,worst_min_ns=0.099999);rejects('hold_margin_shortfall',lambda:progress(old,submargin,ea,eb))
        for label,master,a,b in [('a221oi_cross_term_swap','sg13g2_a221oi_1','A1','C1'),('nand2b_polarity_swap','sg13g2_nand2b_1','A_N','B'),('mux_select_data_swap','sg13g2_mux2_1','S','A0')]:
            inst=next(n for n,m in final['cells'].items() if m==master and (n,a) in final['anchors'] and (n,b) in final['anchors'] and final['anchors'][n,a]!=final['anchors'][n,b])
            x='( '+inst+' '+a+' )';y='( '+inst+' '+b+' )';require(x in text and y in text,'Missing symmetry corruption fixture')
            bad.write_text(text.replace(x,'@CP3E_SWAP@',1).replace(y,x,1).replace('@CP3E_SWAP@',y,1));rejects(label,lambda:audit(before,bad,libpath))
        for master,a,b in [('sg13g2_nand4_1','A','B'),('sg13g2_a221oi_1','A1','A2')]:
            inst=next(n for n,m in final['cells'].items() if m==master and (n,a) in final['anchors'] and (n,b) in final['anchors'] and final['anchors'][n,a]!=final['anchors'][n,b])
            x='( '+inst+' '+a+' )';y='( '+inst+' '+b+' )'
            bad.write_text(text.replace(x,'@CP3E_POSITIVE_SWAP@',1).replace(y,x,1).replace('@CP3E_POSITIVE_SWAP@',y,1))
            audit(before,bad,libpath)
        require(lib==library(repo/'ihp13/pdk/lib/sg13g2_stdcell_fast_1p32V_m40C.lib'),'TT/FF logical functions differ')
        (t/'candidates').symlink_to(d/'candidates',target_is_directory=True)
        selection=json.loads((d/'selection.json').read_text());selection['selected']='initial';(t/'selection.json').write_text(json.dumps(selection))
        rejects('inadmissible_candidate_selected',lambda:audit_selection(t))
        selection=json.loads((d/'selection.json').read_text());selection['candidates'][0]['eligible']=not selection['candidates'][0]['eligible'];(t/'selection.json').write_text(json.dumps(selection))
        rejects('candidate_eligibility_changed',lambda:audit_selection(t))
    require(len(tests)==24,'Incomplete corruption controls')
    return dict(status='ALL_CORRUPTION_CONTROLS_PASSED',count=len(tests),tests=tests,parser_positive_control='Blank fanout slack parsed and reconstructed as limit minus actual',boolean_positive_controls=['nand4_input_swap','a221oi_within_product_swap'],tt_ff_logical_functions_equal=True)
