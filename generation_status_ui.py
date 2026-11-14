"""Read and control saved generation jobs without keeping the GUI open."""
from concurrent.futures import ThreadPoolExecutor
import queue
import tkinter as tk
from tkinter import ttk,messagebox
import managed_control as control

LABELS={'idle':'未登録','running':'実行中','starting':'起動中','pausing':'保存後に一時停止中','paused':'一時停止',
        'complete':'完了','interrupted':'停止中（再開可能）','waiting':'前の工程の完了待ち','failed':'エラー'}


class GenerationStatusWindow:
    def __init__(self,app,base):
        self.app=app;self.base=base;self.closed=False;self.busy=False;self.pending=False;self.operation_error=None
        self.pool=ThreadPoolExecutor(max_workers=1);self.events=queue.Queue();self.job='current'
        self.window=tk.Toplevel(app.root);self.window.title('MapCraft — 生成状況・一時停止・再開')
        self.window.geometry('830x660');self.window.minsize(750,580);self.window.configure(bg='#192938')
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        body=ttk.Frame(self.window,style='Card.TFrame',padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='保存済みの生成状況',font=('Yu Gothic UI',17,'bold')).pack(anchor='w')
        ttk.Label(body,text='PCを再起動した後も、この画面の「再開」で保存済みデータから続行できます。',style='Muted.TLabel').pack(anchor='w',pady=(4,10))
        self.choice=tk.StringVar();self.combo=ttk.Combobox(body,textvariable=self.choice,state='readonly');self.combo.pack(fill='x')
        self.combo.bind('<<ComboboxSelected>>',self.choose)
        self.title=tk.StringVar(value='確認中…');ttk.Label(body,textvariable=self.title,font=('Yu Gothic UI',13,'bold')).pack(anchor='w',pady=(12,5))
        self.safety=tk.StringVar();tk.Label(body,textvariable=self.safety,bg='#192938',fg='#72dfb6',anchor='w',justify='left',wraplength=770).pack(fill='x',pady=(0,8))
        self.details=tk.Text(body,height=16,bg='#0b141d',fg='#d9e6ed',insertbackground='white',wrap='word',font=('Yu Gothic UI',10),state='disabled')
        self.details.pack(fill='both',expand=True)
        actions=ttk.Frame(body,style='Card.TFrame');actions.pack(fill='x',pady=(12,8))
        self.refresh_button=ttk.Button(actions,text='状況を問い合わせる',command=self.refresh);self.refresh_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.pause_button=ttk.Button(actions,text='保存して一時停止',command=lambda:self.action('pause'),state='disabled');self.pause_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.resume_button=ttk.Button(actions,text='再開',command=lambda:self.action('resume'),state='disabled');self.resume_button.pack(side='left',fill='x',expand=True)
        ttk.Label(body,text='一時停止は安全な保存単位の完了を待ちます。「シャットダウンできます」の表示後に終了してください。\nMapCraft画面を閉じても、再開したバックグラウンドの生成は続行します。',style='Muted.TLabel',wraplength=770).pack(anchor='w')
        ttk.Button(body,text='閉じる',command=self.close).pack(anchor='e',pady=(8,0))
        self.refresh();self.timer=self.window.after(200,self.poll)

    def choose(self,event=None):
        index=self.combo.current()
        if 0<=index<len(self.jobs):self.job=self.jobs[index]['id'];self.operation_error=None;self.refresh()

    def refresh(self):
        if self.closed or self.pending or self.busy:return
        self.pending=True
        def work():
            try:self.events.put(('status',(control.list_jobs(self.base),control.snapshot(self.base,self.job))))
            except Exception as exc:self.events.put(('error',str(exc)))
        self.pool.submit(work)

    def action(self,action):
        if self.busy:return
        self.busy=True;self.operation_error=None
        for button in (self.pause_button,self.resume_button):button.configure(state='disabled')
        self.safety.set('保存後の停止を要求しています…' if action=='pause' else '保存済み設定で再開しています…')
        def work():
            try:
                result=getattr(control,action)(self.base,self.job);self.events.put(('action',result))
            except Exception as exc:self.events.put(('error',str(exc)))
        self.pool.submit(work)

    def show(self,result):
        self.last_result=result
        status=result['status'];self.title.set(result['title']+' — '+LABELS.get(status,status))
        self.safety.set('書き込み処理は終了しています。PCをシャットダウンできます。' if result.get('safe_to_shutdown') else
                        '保存単位の完了・処理終了を待っています。' if status=='pausing' else
                        '生成はバックグラウンドで実行中です。' if status=='running' else '停止した処理は「再開」で続けられます。')
        if self.operation_error:self.safety.set('操作できませんでした：'+self.operation_error)
        lines=[]
        for key,label in (('terrain','地形'),('navigation','実地図ミニマップ')):
            progress=result.get(key)
            if progress:
                done=progress.get('completed',0);total=progress.get('total',0)
                lines.append(f'{label}：{done:,} / {total:,} 区画'+(f'（{done/total*100:.1f}%）' if total else ''))
        lines+=['','現在／停止直前の工程：',result.get('current_step','') or '記録待ち','',
                '最終更新：'+str(result.get('updated','')),'作成用保存先：'+result.get('project','')]
        if result.get('output'):lines+=['完成ファイルの予定先：'+result['output']]
        for task in result['tasks']:
            lines+=['',task['title']+'：'+('実行中' if task['alive'] else LABELS.get(task['status'],task['status']))]
            if task.get('error'):lines+=['記録されたエラー：'+task['error']]
            if task.get('stop_reason'):lines+=['停止理由：'+task['stop_reason']]
        if self.operation_error or result.get('control_error'):lines+=['','直前の操作エラー：'+(self.operation_error or result['control_error'])]
        lines+=['','地形100%は全工程の完了を意味しません。仕上げ・圧縮出力・完成版の別保存が続きます。']
        self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('1.0','\n'.join(lines));self.details.configure(state='disabled')
        self.pause_button.configure(state='normal' if status in ('running','starting') else 'disabled')
        self.resume_button.configure(state='normal' if result.get('can_resume') else 'disabled')

    def poll(self):
        if self.closed:return
        try:
            while True:
                kind,value=self.events.get_nowait();self.pending=False
                if kind=='status':
                    self.jobs,result=value;values=[j['title']+' ['+j['id']+']' for j in self.jobs];self.combo.configure(values=values)
                    for i,item in enumerate(self.jobs):
                        if item['id']==self.job:self.combo.current(i)
                    self.show(result)
                elif kind=='action':self.busy=False;self.show(value)
                elif kind=='error':
                    self.busy=False;self.operation_error=value
                    if hasattr(self,'last_result'):self.show(self.last_result)
                    else:self.safety.set(value)
        except queue.Empty:pass
        self.refresh();self.timer=self.window.after(2000,self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo('操作中です','一時停止・再開の操作完了までお待ちください。',parent=self.window);return
        self.closed=True;self.window.after_cancel(self.timer);self.pool.shutdown(wait=False,cancel_futures=True)
        self.app.status_window=None;self.window.destroy()
