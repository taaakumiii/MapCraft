from pathlib import Path
from contextlib import closing
import json
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from finish_phase import arm_phase,phase_counts


class PhaseSafety(unittest.TestCase):
    def test_gate_preserves_completed_running_and_future_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            config={'format':1,'map_id':'test','projection':'national_mercator','origin':[35.,135.],
                    'scale':1.,'tile':1024,'spawn':[35.681236,139.7658]}
            (root/'project.json').write_text(json.dumps(config),encoding='utf-8')
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:
                db.execute('CREATE TABLE tiles(tx INTEGER,tz INTEGER,state TEXT,PRIMARY KEY(tx,tz))')
                db.executemany('INSERT INTO tiles VALUES(?,0,?)',[(0,'done'),(1,'pending'),(2,'done'),(3,'running'),(4,'pending')])
                db.commit()
            plan={k:config[k] for k in ('map_id','projection','origin','scale','tile')}
            plan.update(total=2,tiles=[[0,0],[1,0]])
            arm_phase(root,plan,'first');arm_phase(root,plan,'first')
            self.assertEqual(phase_counts(root,'first')['remaining'],1)
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:
                self.assertEqual(db.execute('SELECT tx,state FROM tiles ORDER BY tx').fetchall(),
                                 [(0,'done'),(1,'pending'),(2,'done'),(3,'running'),(4,'deferred_phase')])
                before=db.execute('SELECT * FROM phase_tiles').fetchall()
            with self.assertRaises(ValueError):arm_phase(root,{**plan,'scale':.5},'first')
            with self.assertRaises(ValueError):arm_phase(root,{**plan,'tiles':[[0,0],[4,0]]},'first')
            with closing(sqlite3.connect(root/'queue.sqlite')) as db:self.assertEqual(db.execute('SELECT * FROM phase_tiles').fetchall(),before)


if __name__=='__main__':unittest.main()
