"""Tk front end for the isolated nationwide Bedrock builder runtime."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import uuid
import webbrowser
from core import SCALES
from national_state import index_info,project_info,downloaded_source,read_database,index_busy,external_index_progress

class NationalWindow:
    def __init__(self,app,base):
        self.app=app;self.base=Path(base);self.process=None;self.busy=False;self.events=queue.Queue();self.after_id=None
        self.window=tk.Toplevel(app.root);self.window.title('MapCraft — 日本全国を1つのワールドへ')
        self.window.geometry('800x800');self.window.minsize(760,750);self.window.configure(bg='#192938');self.window.transient(app.root)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.source=tk.StringVar(value=str(downloaded_source(self.base) or ''));self.index=tk.StringVar(value=str(self.base/'national_data/japan.sqlite'))
        self.project=tk.StringVar(value=str(Path(app.output.get())/'MapCraftJapan'))
        self.scope=tk.StringVar(value='日本全国');self.scale=tk.StringVar(value=app.scale.get())
        self.message=tk.StringVar(value='全国モード：Windows統合版 / つながった1ワールド / 道路・建物＋平地')
        body=ttk.Frame(self.window,style='Card.TFrame',padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='日本全国を、1つのつながったワールドへ',font=('Yu Gothic UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text='共通座標で区画を追加します。生成済みの区画を記録し、中断後も再開できます。\n全国の道路・建物を対象に、地面の高さは全域で揃えます。',style='Muted.TLabel').pack(anchor='w',pady=(5,12))
        self.path_row(body,'日本の地図データ（.osm.pbf）',self.source,self.pick_source)
        ttk.Button(body,text='日本のOSMデータ配布ページを開く（Geofabrik・約2.4GB）',command=lambda:webbrowser.open('https://download.geofabrik.de/asia/japan.html')).pack(anchor='w',pady=(0,8))
        self.path_row(body,'索引の保存先（地図データごとに一度作成）',self.index,self.pick_index)
        self.index_button=ttk.Button(body,text='全国データの索引を作成',command=self.build_index);self.index_button.pack(fill='x',pady=(0,9))
        self.path_row(body,'全国プロジェクトの保存先（再開時も同じフォルダー）',self.project,self.pick_project)
        settings=ttk.Frame(body,style='Card.TFrame');settings.pack(fill='x',pady=4)
        ttk.Label(settings,text='生成範囲').grid(row=0,column=0,sticky='w')
        ttk.Combobox(settings,textvariable=self.scope,values=['日本全国','いま選択した範囲'],state='readonly',width=23).grid(row=1,column=0,sticky='ew',padx=(0,14))
        ttk.Label(settings,text='縮尺（プロジェクト作成後は固定）').grid(row=0,column=1,sticky='w')
        ttk.Combobox(settings,textvariable=self.scale,values=list(SCALES),state='readonly',width=30).grid(row=1,column=1,sticky='ew')
        spawn_hint=tk.StringVar()
        def scope_changed(*_):spawn_hint.set('全国の開始位置：東京駅西側付近。最初にその区画を生成します。' if self.scope.get()=='日本全国' else '開始位置：選択範囲内の検索位置。範囲外の場合は範囲内へ調整します。')
        self.scope.trace_add('write',scope_changed);scope_changed()
        ttk.Label(body,textvariable=spawn_hint,style='Muted.TLabel').pack(anchor='w',pady=(2,0))
        self.create_button=ttk.Button(body,text='新規プロジェクトを作成',command=self.create);self.create_button.pack(fill='x',pady=(9,5))
        actions=ttk.Frame(body,style='Card.TFrame');actions.pack(fill='x')
        self.sample_button=ttk.Button(actions,text='1区画を生成（初回は開始位置）',command=lambda:self.run(1));self.sample_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.run_button=ttk.Button(actions,text='残りを続けて生成 / 再開',command=lambda:self.run(0));self.run_button.pack(side='left',fill='x',expand=True)
        second=ttk.Frame(body,style='Card.TFrame');second.pack(fill='x',pady=5)
        self.pause_button=ttk.Button(second,text='現在の区画後に一時停止',command=self.pause,state='disabled');self.pause_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.export_button=ttk.Button(second,text='生成済みの範囲を .mcworld に出力',command=self.export);self.export_button.pack(side='left',fill='x',expand=True)
        self.status_button=ttk.Button(body,text='保存済みの進捗を確認',command=lambda:self.start('status',{}));self.status_button.pack(fill='x')
        ttk.Label(body,text='全国生成は大規模処理です。最初の区画から容量・時間の目安を表示します。\n実地図・到着通知・宝箱・ベッドを同梱。配置設定は詳細設定から作成時に引き継ぎます。\n.mcworld は生成済み範囲のスナップショットです。取り込み済みのプレイワールドへ追記はしません。',style='Muted.TLabel',font=('Yu Gothic UI',9)).pack(anchor='w',pady=8)
        ttk.Label(body,textvariable=self.message,wraplength=740).pack(fill='x',pady=4)
        self.log=tk.Text(body,height=6,background='#0b141d',foreground='#9db2c2',font=('Consolas',9),state='disabled',wrap='word');self.log.pack(fill='both',expand=True)
        self.buttons=[self.index_button,self.create_button,self.sample_button,self.run_button,self.export_button,self.status_button]
        self.readiness_at=0;self.readiness_state=None;self.refresh_readiness()
        self.after_id=self.window.after(150,self.poll)

    def refresh_readiness(self):
        if self.busy:return
        if not self.source.get().strip():
            source=downloaded_source(self.base)
            if source:self.source.set(str(source))
        source=Path(self.source.get());source_ready=source.is_file() and source.name.endswith('.pbf') and source.stat().st_size>0
        building=index_busy(self.index.get())
        try:index_info(self.index.get());index_ready=True
        except ValueError:index_ready=False
        try:project_info(self.project.get());project_ready=True
        except ValueError:project_ready=False
        generated=False
        if project_ready:
            db=None
            try:
                db=read_database(Path(self.project.get())/'queue.sqlite')
                generated=db.execute("SELECT 1 FROM tiles WHERE state='done' LIMIT 1").fetchone() is not None
            except Exception:project_ready=False
            finally:
                if db is not None:db.close()
        for button,enabled in [(self.index_button,source_ready and not building),(self.create_button,index_ready and not building),
                (self.sample_button,project_ready),(self.run_button,project_ready),
                (self.export_button,generated),(self.status_button,project_ready)]:
            button.configure(state='normal' if enabled else 'disabled')
        state=(source_ready,index_ready,project_ready,generated,building)
        if state!=self.readiness_state:
            self.readiness_state=state
            if building:self.message.set('全国データの索引を作成中です。完了すると「新規プロジェクトを作成」が使えるようになります。')
            elif project_ready:self.message.set('プロジェクト作成済みです。「1区画を生成」または「残りを続けて生成 / 再開」を使用できます。')
            elif index_ready:self.message.set('索引作成済みです。次に「新規プロジェクトを作成」を押してください。')
            elif source_ready:self.message.set('日本のOSMデータを選択しました。最初に「全国データの索引を作成」を押してください。')
            else:self.message.set('保存が完了した日本の .osm.pbf ファイルを選択してください。こちらで保存中のデータは完了後に自動選択します。')
        if building:
            progress=external_index_progress(self.base,self.index.get())
            if progress and progress['event']=='index':
                self.message.set(f"全国データの索引を作成中：{progress['features']:,} 件 / 市町村境界 {progress['towns']:,} 件。完了までお待ちください。")

    def path_row(self,parent,label,var,choose):
        ttk.Label(parent,text=label).pack(anchor='w')
        row=ttk.Frame(parent,style='Card.TFrame');row.pack(fill='x',pady=(2,5))
        ttk.Entry(row,textvariable=var).pack(side='left',fill='x',expand=True)
        ttk.Button(row,text='参照…',command=choose).pack(side='right',padx=(5,0))

    def pick_source(self):
        value=filedialog.askopenfilename(parent=self.window,title='日本の .osm.pbf ファイル',filetypes=[('OSM PBF','*.pbf')])
        if value:self.source.set(value)

    def pick_index(self):
        value=filedialog.asksaveasfilename(parent=self.window,title='索引の保存先',defaultextension='.sqlite',filetypes=[('MapCraft 索引','*.sqlite')])
        if value:self.index.set(value)

    def pick_project(self):
        value=filedialog.askdirectory(parent=self.window,title='全国プロジェクトの保存先')
        if value:self.project.set(value)

    def build_index(self):
        if not Path(self.source.get()).is_file():messagebox.showerror('地図データ','日本の .osm.pbf ファイルを選択してください。',parent=self.window);return
        self.start('index',{'source':self.source.get(),'index':self.index.get()})

    def create(self):
        try:
            options=self.app.treasure_options()
            beds=self.app.bed_options()
            bbox=None
            if self.scope.get()=='いま選択した範囲':
                if self.app.bbox is None:raise ValueError('メイン画面で範囲を選択してください。')
                bbox=list(self.app.bbox)
            point=self.app.search_place
            spawn=[point['lat'],point['lon']] if point else list(self.app.center)
            self.start('create',{'index':self.index.get(),'poly':str(self.base/'national_data/japan.poly'),
                'scale':SCALES[self.scale.get()],'bbox':bbox,'spawn':spawn,'treasure':options,'beds':beds,'villagers':self.app.villager_options(),'geology':{'enabled':self.app.geology_enabled.get(),'backend':'auto' if self.app.gpu_enabled.get() else 'cpu'}})
        except Exception as error:messagebox.showerror('全国プロジェクト',str(error),parent=self.window)

    def run(self,limit):
        point=self.app.search_place
        near=[point['lat'],point['lon']] if point else list(self.app.center)
        self.start('run',{'limit':limit,'near':near})

    def export(self):
        destination=filedialog.asksaveasfilename(parent=self.window,title='1つのワールドとして出力',initialdir=self.project.get(),initialfile='MapCraftJapan.mcworld',defaultextension='.mcworld',filetypes=[('Minecraft world','*.mcworld')])
        if destination:self.start('export',{'destination':destination})

    def start(self,action,data):
        if self.busy:return
        try:
            if action=='create':index_info(data['index'])
            elif action in ('run','status','export'):project_info(self.project.get())
        except ValueError as error:
            self.message.set(str(error));self.write(str(error));self.refresh_readiness();return
        runtime=self.base/'engine/national-runtime/python.exe'
        if not runtime.exists():messagebox.showerror('全国モード','全国生成用ランタイムがありません。配布フォルダー全体を使用してください。',parent=self.window);return
        self.active_project=Path(self.project.get()).resolve()
        self.pause_file=self.active_project/'pause.request'
        if self.pause_file.exists():self.pause_file.unlink()
        data={'project':str(self.active_project),**data}
        requests=self.base/'national_data/requests';requests.mkdir(parents=True,exist_ok=True)
        request=requests/(str(uuid.uuid4())+'.json');request.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.busy=True;self.action=action
        for button in self.buttons:button.configure(state='disabled')
        self.pause_button.configure(state='normal' if action in ('run','index','create') else 'disabled',text='現在の区画後に一時停止' if action=='run' else '処理を中断');self.message.set('処理中… ログに進捗を表示します。')
        def worker():
            try:
                args=[str(runtime),str(self.base/'national_backend.py'),action,'--request',str(request)]
                self.process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),cwd=str(self.base))
                for raw in self.process.stdout:
                    line=raw.decode('utf-8',errors='replace')
                    try:event=json.loads(line)
                    except ValueError:event={'event':'log','text':line.rstrip()}
                    self.events.put(event)
                self.events.put({'event':'finished','code':self.process.wait()})
            except Exception as error:self.events.put({'event':'error','text':str(error)});self.events.put({'event':'finished','code':1})
            finally:
                if request.exists():request.unlink()
        threading.Thread(target=worker,daemon=True).start()

    def pause(self):
        if not self.busy:return
        self.pause_file.parent.mkdir(parents=True,exist_ok=True);self.pause_file.write_text('pause',encoding='ascii')
        self.message.set('現在の区画の保存後に停止します。' if self.action=='run' else '処理を中断します。索引・計画の作成は最初から再実行になります。');self.pause_button.configure(state='disabled')

    def write(self,text):
        self.log.configure(state='normal');self.log.insert('end',text+'\n');self.log.see('end')
        if int(self.log.index('end-1c').split('.')[0])>600:self.log.delete('1.0','100.0')
        self.log.configure(state='disabled')

    def poll(self):
        try:
            for _ in range(100):
                item=self.events.get_nowait();kind=item['event']
                if kind in ('progress','status'):
                    text=f"{item['completed']:,} / {item['total']:,} 区画 · {item['chunks']:,} チャンク · {item['bytes']/1024**3:.2f} GB"
                    if item.get('estimated_bytes'):
                        text+=f"\n全体目安：{item['estimated_bytes']/1024**4:.2f} TB / {item['estimated_seconds']/86400:.1f} 日（生成実績からの概算）"
                    self.message.set(text);self.write(text)
                elif kind=='planned':self.message.set(f"計画を保存しました：{item['tiles']:,} 区画 / {item['chunks']:,} チャンク");self.write(self.message.get())
                elif kind=='tile':self.write(f"生成中 {item['tx']}, {item['tz']}  ({item['completed']:,}/{item['total']:,})")
                elif kind=='index':self.message.set(f"索引を作成中：{item['features']:,} 件 / 市町村境界 {item['towns']:,} 件")
                elif kind=='indexed':
                    self.message.set(f"索引を保存しました：{item['features']:,} 件 / 市町村 {item['towns']:,} 件")
                    if item.get('incomplete_towns'):self.write('境界不足で到着通知を省いた自治体：'+', '.join(item['incomplete_towns']))
                elif kind=='planning':self.message.set(f"全国の区画を計画中：{item['tiles']:,} 区画")
                elif kind=='exported':self.message.set('ワールドを出力しました：'+item['path']);self.write(self.message.get())
                elif kind in ('log','error'):
                    self.write(item['text'])
                    if kind=='error':self.message.set('処理を完了できませんでした：'+item['text'])
                elif kind=='finished':
                    self.busy=False;self.process=None
                    self.pause_button.configure(state='disabled')
                    if item['code']==0:
                        next_step={'index':'索引作成が完了しました。次に「新規プロジェクトを作成」を押してください。',
                            'create':'プロジェクトを作成しました。次に「1区画を生成」を押してください。',
                            'run':'区画を保存しました。続けて生成、または .mcworld へ出力できます。'}
                        if self.action in next_step:self.message.set(next_step[self.action])
                        self.write(self.message.get())
                    self.refresh_readiness()
        except queue.Empty:pass
        if not self.busy and time.monotonic()>=self.readiness_at:
            self.readiness_at=time.monotonic()+1;self.refresh_readiness()
        self.after_id=self.window.after(150,self.poll)

    def close(self):
        if self.busy:
            if self.action in ('run','index','create'):self.pause()
            messagebox.showinfo('処理中です','処理が停止するまで、この画面を開いたままお待ちください。',parent=self.window);return
        if self.after_id:self.window.after_cancel(self.after_id)
        self.app.national_window=None;self.window.destroy()
