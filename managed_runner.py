"""Persistent status adapter for ordinary GUI national/region actions."""
import argparse,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import managed_control as control


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);parser.add_argument('--job',required=True);args=parser.parse_args()
    base=Path(args.base);record=control.read(control.manifest_path(base,args.job));task=record['tasks'][0]
    state={'status':'running','pid':os.getpid(),'project':record['project'],'destination':control.read(record['request'],{}).get('destination')}
    os.environ.update(MAPCRAFT_CONTROL_BASE=str(base),MAPCRAFT_JOB_ID=args.job)
    import national_backend as n
    original=n.emit
    def emit(kind,**values):
        state.update(updated=control.now(),last_event={'event':kind,**values})
        if kind in ('progress','status'):state['progress']=values
        if kind=='exported':state['output']=values['path']
        control.write(base/'自動生成'/task['state_file'],state);original(kind,**values)
    n.emit=emit
    try:
        control.checkpoint(record['project']);sys.argv=['national_backend.py',record['action'],'--request',record['request']]
        n.main()
        paused=Path(record['project'],'pause.request').exists() or control.pause_path(base,args.job).exists()
        state.update(status='paused' if paused else 'complete',updated=control.now())
    except Exception as exc:
        state.update(status='paused' if isinstance(exc,InterruptedError) else 'failed',error=str(exc),updated=control.now())
        if not isinstance(exc,InterruptedError):
            import traceback;traceback.print_exc()
        original('log' if isinstance(exc,InterruptedError) else 'error',text=str(exc))
    finally:control.write(base/'自動生成'/task['state_file'],state)
    if state['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
