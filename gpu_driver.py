"""Optional NVIDIA driver-only compute. Missing/unsupported GPUs are ordinary CPU fallback.

Uses the installed system driver, not a bundled CUDA DLL or Python GPU package.
The caller owns fallback and never writes world data before results are ready.
"""
import ctypes as C
import os
from pathlib import Path
import numpy as np

class GPUUnavailable(RuntimeError):pass

class MaterialsGPU:
    def __init__(self):
        self.ctx=C.c_void_p();self.module=C.c_void_p();self.buffers=[C.c_uint64(),C.c_uint64()];self.capacity=[0,0]
        if os.name!='nt':raise GPUUnavailable('対応GPUドライバーを利用できません')
        try:
            self.lib=C.WinDLL(str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/nvcuda.dll'))
            self.bind('cuInit',[C.c_uint]);self.bind('cuDeviceGetCount',[C.POINTER(C.c_int)])
            self.bind('cuDeviceGet',[C.POINTER(C.c_int),C.c_int]);self.bind('cuDeviceGetName',[C.c_void_p,C.c_int,C.c_int])
            self.bind('cuCtxCreate_v2',[C.POINTER(C.c_void_p),C.c_uint,C.c_int]);self.bind('cuCtxDestroy_v2',[C.c_void_p])
            self.bind('cuModuleLoadData',[C.POINTER(C.c_void_p),C.c_void_p]);self.bind('cuModuleGetFunction',[C.POINTER(C.c_void_p),C.c_void_p,C.c_char_p])
            self.bind('cuMemAlloc_v2',[C.POINTER(C.c_uint64),C.c_size_t]);self.bind('cuMemFree_v2',[C.c_uint64])
            self.bind('cuMemcpyHtoD_v2',[C.c_uint64,C.c_void_p,C.c_size_t]);self.bind('cuMemcpyDtoH_v2',[C.c_void_p,C.c_uint64,C.c_size_t])
            self.bind('cuLaunchKernel',[C.c_void_p,*([C.c_uint]*7),C.c_void_p,C.POINTER(C.c_void_p),C.c_void_p])
            self.call('cuInit',0);count=C.c_int();self.call('cuDeviceGetCount',C.byref(count))
            if count.value<1:raise GPUUnavailable('対応GPUがありません')
            device=C.c_int();self.call('cuDeviceGet',C.byref(device),0)
            name=C.create_string_buffer(256);self.call('cuDeviceGetName',name,256,device)
            self.name=name.value.decode('utf-8',errors='replace')
            self.call('cuCtxCreate_v2',C.byref(self.ctx),4,device)
            ptx=C.create_string_buffer((Path(__file__).parent/'geology_gpu.ptx').read_bytes())
            self.call('cuModuleLoadData',C.byref(self.module),ptx)
            self.function=C.c_void_p();self.call('cuModuleGetFunction',C.byref(self.function),self.module,b'geology_materials')
        except Exception as error:
            self.close();raise GPUUnavailable(str(error)) from error
    def bind(self,name,args):
        fn=getattr(self.lib,name);fn.argtypes=args;fn.restype=C.c_int
    def call(self,name,*args):
        result=getattr(self.lib,name)(*args)
        if result:raise GPUUnavailable(f'{name}: CUDA {result}')
    def reserve(self,index,size):
        if size<=self.capacity[index]:return
        if self.buffers[index].value:self.call('cuMemFree_v2',self.buffers[index]);self.buffers[index]=C.c_uint64()
        self.call('cuMemAlloc_v2',C.byref(self.buffers[index]),size);self.capacity[index]=size
    def materials(self,descriptors):
        descriptors=np.ascontiguousarray(descriptors,dtype=np.int32)
        rows=len(descriptors)
        if descriptors.shape!=(rows,9) or not 1<=rows<=1024:raise ValueError('Invalid GPU material batch')
        result=np.empty((rows,4096),dtype=np.uint8)
        self.reserve(0,descriptors.nbytes);self.reserve(1,result.nbytes)
        self.call('cuMemcpyHtoD_v2',self.buffers[0],C.c_void_p(descriptors.ctypes.data),descriptors.nbytes)
        row_count=C.c_uint(rows)
        params=(C.c_void_p*3)(C.cast(C.byref(self.buffers[0]),C.c_void_p),C.cast(C.byref(self.buffers[1]),C.c_void_p),C.cast(C.byref(row_count),C.c_void_p))
        self.call('cuLaunchKernel',self.function,(rows*4096+255)//256,1,1,256,1,1,0,None,params,None)
        self.call('cuMemcpyDtoH_v2',C.c_void_p(result.ctypes.data),self.buffers[1],result.nbytes)
        return result
    def close(self):
        if self.ctx.value:
            self.lib.cuCtxDestroy_v2(self.ctx);self.ctx=C.c_void_p()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
