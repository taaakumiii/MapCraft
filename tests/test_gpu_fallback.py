from pathlib import Path
import os
import struct
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import numpy as np
    import amulet_nbt as nbt
    import leveldb
    import geology_backend as g
    NATIVE=True
except ImportError:NATIVE=False


@unittest.skipUnless(NATIVE,'Run with bundled native Python')
class GPUFallback(unittest.TestCase):
    def rows(self,count):
        palette=[nbt.NamedTag(nbt.CompoundTag({'name':nbt.StringTag(name),'states':nbt.CompoundTag({}),'version':nbt.IntTag(18168865)}))
                 for name in ('minecraft:stone','minecraft:diamond_ore','minecraft:air')]
        indices=np.zeros(4096,dtype=np.uint32);indices[::11]=1;indices[::13]=2
        blobs=[g.encode(bytes((9,1,section)),[(indices.copy(),list(palette))]) for section in range(4)]
        rocks=[*g.HOSTS,'unknown']
        return [(struct.pack('<iiBb',i,-1,47,i%4),blobs[i%4],i%31-15,i//31-20,i%4,rocks[i%len(rocks)],(-430000,75000)) for i in range(count)]

    def test_missing_gpu_automatically_uses_cpu(self):
        import gpu_driver
        rows=self.rows(48);expected=list(g.decorated_records(iter(rows),2,backend='cpu'));state={};messages=[]
        with patch.dict(g._AUTO_BACKENDS,{},clear=True),patch.object(g,'_GPU',None),patch.object(gpu_driver.C,'WinDLL',side_effect=OSError('GPU driver unavailable'),create=True):
            actual=list(g.decorated_records(iter(rows),2,backend='auto',notify=messages.append,report=state))
        self.assertEqual(actual,expected);self.assertEqual(state['backend'],'cpu');self.assertIn('fallback_reason',state)
        self.assertTrue(any('CPU' in s for s in messages))

    def test_gpu_error_midway_recomputes_batch_on_cpu_without_lost_rows(self):
        class FakeGPU:
            name='Test GPU';closed=False
            def close(self):self.closed=True
        fake=FakeGPU();rows=self.rows(1400);expected=list(g.decorated_records(iter(rows),2,backend='cpu'));state={};calls=0
        def batch(items,gpu,codec_pool=None):
            nonlocal calls
            calls+=1
            if calls>1:raise RuntimeError('simulated device error')
            return g.decorate_batch(items)
        with patch.dict(g._AUTO_BACKENDS,{},clear=True),patch.object(g,'_GPU',fake),patch.object(g,'gpu_session',return_value=fake),patch.object(g,'gpu_batch',side_effect=batch):
            actual=list(g.decorated_records(iter(rows),2,backend='gpu',report=state))
        self.assertEqual(actual,expected);self.assertEqual(len(actual),1400);self.assertEqual(state['backend'],'cpu');self.assertTrue(fake.closed)

    @unittest.skipUnless(os.environ.get('MAPCRAFT_TEST_GPU')=='1','Optional real GPU test')
    def test_real_gpu_native_bytes_equal_cpu(self):
        from gpu_driver import MaterialsGPU,GPUUnavailable
        try:
            with MaterialsGPU():pass
        except GPUUnavailable as error:self.skipTest(str(error))
        rows=self.rows(144);expected=list(g.decorated_records(iter(rows),2,backend='cpu'));state={}
        actual=list(g.decorated_records(iter(rows),2,backend='gpu',report=state))
        self.assertEqual(state['backend'],'gpu',state);self.assertEqual(actual,expected)


if __name__=='__main__':unittest.main()
