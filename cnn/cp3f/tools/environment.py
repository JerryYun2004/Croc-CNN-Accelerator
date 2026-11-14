import os
def environment(repo):
    env=dict(os.environ,CROC_PDK='sg13cmos5l',PROJ_NAME='croc',TOP_DESIGN='croc_chip',OMP_NUM_THREADS='2')
    for n in ['CELLS','SRAMS','IOS']:env['PDK_DIR_LIB_'+n]=str(repo/'ihp13/pdk/lib')
    for n in ['TECH','CELLS','SRAMS','IOS','BOND']:env['PDK_DIR_LEF_'+n]=str(repo/'ihp13/pdk/lef')
    env.update(PDK_STDCELL_LIB_TT_FILE='sg13g2_stdcell_typ_1p20V_25C.lib',
        PDK_STDCELL_LIB_FF_FILE='sg13g2_stdcell_fast_1p32V_m40C.lib',
        PDK_IO_LIB_TT_FILE='sg13cmos5l_io_typ_1p2V_3p3V_25C.lib',PDK_IO_LIB_FF_FILE='sg13cmos5l_io_fast_1p32V_3p6V_m40C.lib',
        PDK_TECH_LEF_FILE='sg13cmos5l_tech.lef',PDK_STDCELL_LEF_FILE='sg13g2_stdcell.lef',
        PDK_IO_LEF_FILE='sg13cmos5l_io.lef',PDK_BONDPAD_LEF='bondpad5l_70x70.lef')
    return env

