import json, re, struct, subprocess
from pathlib import Path
from common import command,require,put,sha

def symbols(nm,elf):
    result={}
    for line in subprocess.check_output([nm,'-n',str(elf)],text=True).splitlines():
        fields=line.split()
        if len(fields)==3:result[fields[2]]=int(fields[0],16)
    return result

def hex_image(path,blob):
    require(len(blob)==4096,'Expected complete 4 KiB firmware SRAM image')
    Path(path).write_text('@10000000\n'+'\n'.join(' '.join(f'{x:02x}' for x in blob[i:i+16]) for i in range(0,4096,16))+'\n')

def prepare(root,repo,evidence,out,gcc,binutils):
    fw=out/'firmware';fw.mkdir()
    # One linked firmware image; only its data section changes for each fixture.
    # A temporary binary avoids compiler folding of expected outputs into code.
    fixture=fw/'fixture.bin';fixture.write_bytes(bytes(2568))
    assembly=fw/'fixture.S'
    require('"' not in str(fixture) and '\n' not in str(fixture),'Unsupported workspace path')
    assembly.write_text('.section .fixture,"a",@progbits\n.balign 4\n.global input\ninput:\n.incbin "'+str(fixture)+'"\n')
    elf=fw/'firmware.elf'
    command([gcc,'-march=rv32i_zicsr','-mabi=ilp32','-Os','-g','-ffreestanding','-fno-builtin','-fno-stack-protector',
        '-fdata-sections','-ffunction-sections','-msmall-data-limit=0','-nostdlib','-Wall','-Wextra','-Werror',
        '-I',repo/'cnn/cp2b/sw',root/'sw/main.c',repo/'sw/crt0.S',assembly,'-T',root/'sw/link.ld',
        '-Wl,--gc-sections,-Map='+str(fw/'firmware.map'),'-o',elf],out/'logs/firmware-build.log')
    s=symbols(binutils['nm'],elf)
    require(s['__used_end']<=s['__stack_bottom']==0x10000f00,'Firmware SRAM budget exceeded')
    require(s['__stack_pointer$']==0x10001000,'Stack outside firmware SRAM')
    require(s['input']%4==0 and s['report']>=s['input']+2568,'Fixture/report layout differs')
    command([binutils['objcopy'],'-O','binary',elf,fw/'firmware.bin'],out/'logs/objcopy.log')
    command([binutils['objdump'],'-d',elf],fw/'firmware.disasm')
    command([binutils['readelf'],'-h','-A','-l',elf],fw/'firmware.attributes')
    attrs=(fw/'firmware.attributes').read_text()
    require('ELF32' in attrs and 'RISC-V' in attrs,'Not a RISC-V ELF32 firmware')
    blob=(fw/'firmware.bin').read_bytes();require(len(blob)<=3840,'Load image overlaps stack')
    blob+=bytes([0xa5])*(4096-len(blob))
    index=json.loads((evidence/'fixtures/index.json').read_text());require(len(index)==55,'Fixture count differs')
    for row in index:
        src=evidence/'fixtures'/row['file'];require(sha(src)==row['sha256'],'Fixture checksum differs')
        f=src.read_bytes();require(len(f)==4282,'Fixture format differs')
        payload=f[:2380]+f[4096:]+bytes(2);require(len(payload)==2568,'Payload length differs')
        image=bytearray(blob);off=s['input']-0x10000000;image[off:off+2568]=payload
        # Explicitly zero the report, and fill the entire unused stack region with canaries.
        ro=s['report']-0x10000000;image[ro:ro+64]=bytes(64)
        hex_image(fw/(Path(row['file']).stem+'.hex'),image)
    layout=dict(symbols=s,code_and_data_bytes=len((fw/'firmware.bin').read_bytes()),
        total_static_bytes=s['__used_end']-0x10000000,stack_reserved_bytes=256,
        static_to_stack_gap_bytes=s['__stack_bottom']-s['__used_end'],fixture_bytes=2568,
        isa='rv32i_zicsr',firmware_sram_bytes=4096,accelerator_sram_bytes=4096)
    put(out/'reports/firmware.json',layout)
    return layout,index,blob

def parse_run(path,prediction):
    text=Path(path).read_text()
    require('CP2C_FIRMWARE_FAIL' not in text and 'CP2C timeout' not in text and '%Error' not in text,'Firmware or simulator failed')
    m=re.findall(r'CP2C_SOC_PASS fetches=(\d+) cnn_reads=(\d+) cnn_writes=(\d+)',text)
    require(len(m)==1,'Missing/duplicate SoC completion marker')
    fetch,reads,writes=map(int,m[0]);require(fetch>1000 and reads>92 and writes==794,'CPU OBI transaction counts differ')
    rows=re.findall(r'CP2C_MEASUREMENT (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)',text)
    require(len(rows)==2,'Missing measurements')
    result=[]
    for i,row in enumerate(rows):
        run,model,image,wait,verify,total,engine,pred=map(int,row)
        require(run==i and engine==302451 and pred==prediction,'Inference result/cycle counter differs')
        require(total==model+image+wait+verify and total>engine and image>0 and verify>0,'Invalid cycle accounting')
        require(wait>=engine,'CPU wait shorter than engine execution')
        result.append(dict(model_copy_cycles=model,image_copy_cycles=image,start_wait_cycles=wait,
            verify_cycles=verify,total_cycles=total,engine_cycles=engine,prediction=pred))
    require(result[0]['model_copy_cycles']>result[1]['model_copy_cycles'],'Model reuse not observed')
    return dict(cpu_fetches=fetch,cnn_reads=reads,cnn_writes=writes,cold=result[0],warm=result[1])
