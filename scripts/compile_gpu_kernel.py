"""Developer-only PTX rebuild with an explicitly installed NVRTC compiler.

The normal distribution uses the checked-in PTX and needs no CUDA Toolkit.
"""
import argparse
import ctypes as C
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--nvrtc-bin',required=True,type=Path);args=p.parse_args()
    directory=args.nvrtc_bin.resolve();cookie=os.add_dll_directory(str(directory))
    os.environ['PATH']=str(directory)+os.pathsep+os.environ.get('PATH','')
    builtin=C.WinDLL(str(next(directory.glob('nvrtc-builtins*.dll'))))
    library=C.WinDLL(str(next(directory.glob('nvrtc64_*.dll'))))
    library.nvrtcCreateProgram.argtypes=[C.POINTER(C.c_void_p),C.c_char_p,C.c_char_p,C.c_int,C.c_void_p,C.c_void_p]
    library.nvrtcCompileProgram.argtypes=[C.c_void_p,C.c_int,C.POINTER(C.c_char_p)]
    for name in ('nvrtcGetProgramLogSize','nvrtcGetPTXSize'):getattr(library,name).argtypes=[C.c_void_p,C.POINTER(C.c_size_t)]
    for name in ('nvrtcGetProgramLog','nvrtcGetPTX'):getattr(library,name).argtypes=[C.c_void_p,C.c_void_p]
    program=C.c_void_p()
    if library.nvrtcCreateProgram(C.byref(program),(ROOT/'geology_gpu.cu').read_bytes(),b'geology_gpu.cu',0,None,None):raise RuntimeError('NVRTC program creation failed')
    options=(C.c_char_p*3)(b'--gpu-architecture=compute_52',b'--std=c++11',b'--fmad=false')
    code=library.nvrtcCompileProgram(program,3,options);size=C.c_size_t();library.nvrtcGetProgramLogSize(program,C.byref(size))
    log=C.create_string_buffer(size.value);library.nvrtcGetProgramLog(program,log)
    if code:raise RuntimeError(log.value.decode('utf-8',errors='replace'))
    library.nvrtcGetPTXSize(program,C.byref(size));ptx=C.create_string_buffer(size.value)
    if library.nvrtcGetPTX(program,ptx):raise RuntimeError('PTX export failed')
    (ROOT/'geology_gpu.ptx').write_bytes(ptx.value);print('Generated',ROOT/'geology_gpu.ptx')
