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
from national_state import index_info,project_info,downloaded_source,read_database,index_busy,external_index_progress,project_busy
from regional_scope import PREFECTURES,REGIONS,SCOPE_KINDS,selection,default_project_name,export_filename

class NationalWindow:
    def __init__(self,app,base):
        self.app=app;self.base=Path(base);self.process=None;self.busy=False;self.events=queue.Queue();self.after_id=None
        self.window=tk.Toplevel(app.root);self.window.title('MapCraft — 全国・都道府県・地方のワールド出力')
        self.window.geometry('880x900');self.window.minsize(820,780);self.window.configure(bg='#192938');self.window.transient(app.root)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.source=tk.StringVar(value=str(downloaded_source(self.base) or ''));self.index=tk.StringVar(value=str(self.base/'national_data/japan.sqlite'))
        self.project=tk.StringVar(value=str(Path(app.output.get())/'MapCraftJapan'))
        self.project_parent=Path(app.output.get());self.auto_project=self.project.get()
        self.scope=tk.StringVar(value='日本全国');self.scale=tk.StringVar(value=app.scale.get())
        self.scope_name=tk.StringVar();self.scope_description=tk.StringVar();self.saved_project=tk.StringVar()
        self.message=tk.StringVar(value='全国モード：Windows統合版 / つながった1ワールド / 道路・建物＋平地')
        body=ttk.Frame(self.window,style='Card.TFrame',padding=18);body.pack(fill='both',expand=True)
        ttk.Label(body,text='全国・都道府県・地方を選んでワールド出力',font=('Yu Gothic UI',16,'bold')).pack(anchor='w')
        ttk.Label(body,text='選択した地域を、1つのつながったワールドにします。保存済み区画から再開できます。\n陸地と約500mの沿岸・島を含み、島の間は軽量な海で接続。地面の高さは一定です。',style='Muted.TLabel').pack(anchor='w',pady=(5,8))
        self.path_row(body,'日本の地図データ（.osm.pbf）',self.source,self.pick_source)
        ttk.Button(body,text='日本のOSMデータ配布ページを開く（Geofabrik・約2.4GB）',command=lambda:webbrowser.open('https://download.geofabrik.de/asia/japan.html')).pack(anchor='w',pady=(0,8))
        self.path_row(body,'索引の保存先（地図データごとに一度作成）',self.index,self.pick_index)
        self.index_button=ttk.Button(body,text='全国データの索引を作成',command=self.build_index);self.index_button.pack(fill='x',pady=(0,9))
        self.path_row(body,'作成用プロジェクトの保存先（地域ごとに別保存／再開時は同じフォルダー）',self.project,self.pick_project)
        settings=ttk.Frame(body,style='Card.TFrame');settings.pack(fill='x',pady=4)
        ttk.Label(settings,text='新規作成・拡張する範囲').grid(row=0,column=0,sticky='w')
        self.scope_combo=ttk.Combobox(settings,textvariable=self.scope,values=SCOPE_KINDS,state='readonly',width=20)
        self.scope_combo.grid(row=1,column=0,sticky='ew',padx=(0,10))
        ttk.Label(settings,text='都道府県・地方').grid(row=0,column=1,sticky='w')
        self.name_combo=ttk.Combobox(settings,textvariable=self.scope_name,state='disabled',width=18)
        self.name_combo.grid(row=1,column=1,sticky='ew',padx=(0,10))
        ttk.Label(settings,text='縮尺（作成後は固定）').grid(row=0,column=2,sticky='w')
        self.scale_combo=ttk.Combobox(settings,textvariable=self.scale,values=list(SCALES),state='readonly',width=28)
        self.scale_combo.grid(row=1,column=2,sticky='ew')
        self.scope.trace_add('write',self.scope_changed);self.scope_name.trace_add('write',self.scope_changed);self.scope_changed()
        ttk.Label(body,textvariable=self.scope_description,style='Muted.TLabel',wraplength=820).pack(anchor='w',pady=(2,0))
        ttk.Label(body,textvariable=self.saved_project,style='Muted.TLabel',wraplength=820).pack(anchor='w',pady=(2,0))
        self.build_button=ttk.Button(body,text='選択範囲を生成して .mcworld に出力 / 再開',command=self.build);self.build_button.pack(fill='x',pady=(8,5))
        setup=ttk.Frame(body,style='Card.TFrame');setup.pack(fill='x')
        self.create_button=ttk.Button(setup,text='新規プロジェクトを作成',command=self.create);self.create_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.extend_button=ttk.Button(setup,text='選択範囲へ拡張（既存区画を保持）',command=self.extend);self.extend_button.pack(side='left',fill='x',expand=True)
        actions=ttk.Frame(body,style='Card.TFrame');actions.pack(fill='x')
        self.sample_button=ttk.Button(actions,text='1区画を生成（初回は開始位置）',command=lambda:self.run(1));self.sample_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.run_button=ttk.Button(actions,text='残りを続けて生成 / 再開',command=lambda:self.run(0));self.run_button.pack(side='left',fill='x',expand=True)
        second=ttk.Frame(body,style='Card.TFrame');second.pack(fill='x',pady=5)
        self.pause_button=ttk.Button(second,text='現在の区画後に一時停止',command=self.pause,state='disabled');self.pause_button.pack(side='left',fill='x',expand=True,padx=(0,5))
        self.export_button=ttk.Button(second,text='このプロジェクトの生成済み範囲を出力',command=self.export);self.export_button.pack(side='left',fill='x',expand=True)
        self.status_button=ttk.Button(body,text='保存済みの進捗を確認',command=lambda:self.start('status',{}));self.status_button.pack(fill='x')
        ttk.Label(body,text='地域を選ぶと新規保存先を切り替えます。拡張時は「参照…」で元プロジェクトを選択してください。\n宝箱・ベッド・動物・村人・ミニマップ等は詳細設定を引き継ぎます。広域は時間と容量が必要です。\n出力対象は表示中のプロジェクトです。取り込み済みのプレイワールドへの追記ではありません。',style='Muted.TLabel',font=('Yu Gothic UI',9)).pack(anchor='w',pady=6)
        ttk.Label(body,textvariable=self.message,wraplength=740).pack(fill='x',pady=4)
        self.log=tk.Text(body,height=6,background='#0b141d',foreground='#9db2c2',font=('Consolas',9),state='disabled',wrap='word');self.log.pack(fill='both',expand=True)
        self.buttons=[self.index_button,self.create_button,self.extend_button,self.build_button,self.sample_button,self.run_button,self.export_button,self.status_button]
        self.readiness_at=0;self.readiness_state=None;self.refresh_readiness()
        self.after_id=self.window.after(150,self.poll)

    def scope_changed(self,*_):
        if getattr(self,'changing_scope',False):return
        self.changing_scope=True
        try:
            values=list(PREFECTURES.values()) if self.scope.get()=='都道府県' else list(REGIONS) if self.scope.get()=='地方' else []
            self.name_combo.configure(values=values,state='readonly' if values else 'disabled')
            if self.scope_name.get() not in values:self.scope_name.set(values[0] if values else '')
            item=selection(self.scope.get(),self.scope_name.get())
            if self.project.get()==self.auto_project:
                self.auto_project=str(self.project_parent/default_project_name(item));self.project.set(self.auto_project)
            members='・'.join(PREFECTURES[code] for code in item['codes']) if item['kind']=='region' else ''
            self.scope_description.set(('対象：'+item['label']+('（'+members+'）' if members else '')+'\n')+
                ('開始位置は東京駅付近です。' if item['kind']=='national' else '開始位置は選択範囲内へ自動調整します。'))
        finally:self.changing_scope=False

    def refresh_readiness(self):
        if self.busy:return
        if not self.source.get().strip():
            source=downloaded_source(self.base)
            if source:self.source.set(str(source))
        source=Path(self.source.get());source_ready=source.is_file() and source.name.endswith('.pbf') and source.stat().st_size>0
        building=index_busy(self.index.get())
        try:index_info(self.index.get());index_ready=True
        except ValueError:index_ready=False
        config={}
        try:config=project_info(self.project.get());project_ready=True
        except ValueError:project_ready=False
        in_use=project_busy(self.project.get());managed=bool(config.get('active_phase'))
        editable=not in_use and not managed
        self.saved_project.set('保存先の対象：'+config.get('scope_selection',{}).get('label',config.get('active_phase',{}).get('title','既存の全国／選択範囲')) if project_ready else '新規保存先：'+Path(self.project.get()).name)
        generated=False
        if project_ready:
            db=None
            try:
                db=read_database(Path(self.project.get())/'queue.sqlite')
                generated=db.execute("SELECT 1 FROM tiles WHERE state='done' LIMIT 1").fetchone() is not None
            except Exception:project_ready=False
            finally:
                if db is not None:db.close()
        for button,enabled in [(self.index_button,source_ready and not building),(self.create_button,index_ready and not building and not project_ready and editable),
                (self.build_button,index_ready and not building and editable),(self.extend_button,project_ready and bool(config.get('scope_selection',{}).get('codes')) and editable),
                (self.sample_button,project_ready and editable),(self.run_button,project_ready and editable),
                (self.export_button,generated and editable),(self.status_button,project_ready and not in_use)]:
            button.configure(state='normal' if enabled else 'disabled')
        state=(source_ready,index_ready,project_ready,generated,building,in_use,managed)
        if state!=self.readiness_state:
            self.readiness_state=state
            if in_use or managed:self.message.set('この保存先は別の生成処理・自動拡張で使用中です。地域を選ぶと別の新規保存先へ切り替わります。')
            elif building:self.message.set('全国データの索引を作成中です。完了すると「新規プロジェクトを作成」が使えるようになります。')
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

    def creation_data(self):
        item=selection(self.scope.get(),self.scope_name.get());bbox=None
        if item['kind']=='map':
            if self.app.bbox is None:raise ValueError('メイン画面で範囲を選択してください。')
            bbox=list(self.app.bbox)
        point=self.app.search_place
        return {'index':self.index.get(),'poly':str(self.base/'national_data/japan.poly'),
            'scale':SCALES[self.scale.get()],'selection':item,'bbox':bbox,'spawn':[point['lat'],point['lon']] if point else list(self.app.center),
            'treasure':self.app.treasure_options(),'beds':self.app.bed_options(),'villagers':self.app.villager_options(),
            'mobs':self.app.mob_options(),'bathymetry':{'enabled':self.app.bathymetry_enabled.get(),'max_depth':60},
            'include_navigation':self.app.include_navigation.get(),'geology':{'enabled':self.app.geology_enabled.get(),'backend':'auto' if self.app.gpu_enabled.get() else 'cpu'}}

    def create(self):
        try:
            self.start('create',self.creation_data())
        except Exception as error:messagebox.showerror('全国プロジェクト',str(error),parent=self.window)

    def extend(self):self.start('extend',{'selection':selection(self.scope.get(),self.scope_name.get())})

    def build(self):
        try:
            data=self.creation_data()
            destination=filedialog.asksaveasfilename(parent=self.window,title='選択範囲の完成ワールドを保存',initialdir=str(self.base/'exports'),
                initialfile=export_filename({'scope_selection':data['selection'],'scale':data['scale']}),defaultextension='.mcworld',filetypes=[('Minecraft world','*.mcworld')])
            if destination:self.start('build',{**data,'destination':destination})
        except Exception as error:messagebox.showerror('地域ワールド',str(error),parent=self.window)

    def run(self,limit):
        point=self.app.search_place
        near=[point['lat'],point['lon']] if point else list(self.app.center)
        self.start('run',{'limit':limit,'near':near})

    def export(self):
        config=project_info(self.project.get())
        destination=filedialog.asksaveasfilename(parent=self.window,title='このプロジェクトの生成済み範囲を出力',initialdir=self.project.get(),initialfile=export_filename(config),defaultextension='.mcworld',filetypes=[('Minecraft world','*.mcworld')])
        if destination:self.start('export',{'destination':destination})

    def start(self,action,data):
        if self.busy:return
        try:
            if action in ('create','build'):index_info(data['index'])
            elif action in ('run','status','export','extend'):project_info(self.project.get())
            if project_busy(self.project.get()):raise ValueError('この保存先は別の処理が使用中です。現在の生成はそのまま継続します。')
        except ValueError as error:
            self.message.set(str(error));self.write(str(error));self.refresh_readiness();return
        runtime=self.base/'engine/national-runtime/python.exe'
        if not runtime.exists():messagebox.showerror('全国モード','全国生成用ランタイムがありません。配布フォルダー全体を使用してください。',parent=self.window);return
        self.active_project=Path(self.project.get()).resolve()
        self.pause_file=self.active_project/'pause.request'
        data={'project':str(self.active_project),'gui':True,**data}
        if action=='build':Path(data['destination']).parent.mkdir(parents=True,exist_ok=True)
        requests=self.base/'national_data/requests';requests.mkdir(parents=True,exist_ok=True)
        request=requests/(str(uuid.uuid4())+'.json');request.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        self.busy=True;self.action=action;self.last_export=None
        for button in self.buttons:button.configure(state='disabled')
        for combo in (self.scope_combo,self.name_combo,self.scale_combo):combo.configure(state='disabled')
        self.pause_button.configure(state='normal' if action in ('run','index','create','extend','build') else 'disabled',text='現在の区画後に一時停止' if action in ('run','build') else '処理を中断');self.message.set('処理中… ログに進捗を表示します。')
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
        self.pause_file.parent.mkdir(parents=True,exist_ok=True)
        try:
            with self.pause_file.open('x',encoding='ascii') as stream:stream.write('MapCraft GUI pause: '+str(uuid.uuid4()))
        except FileExistsError:pass
        self.message.set('現在の区画の保存後に停止します。' if self.action in ('run','build') else '処理を中断します。索引・計画の作成は最初から再実行になります。');self.pause_button.configure(state='disabled')

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
                elif kind=='extended':self.message.set(f"{item['label']}へ拡張：追加 {item['added']:,} / 合計 {item['tiles']:,} 区画。続けて生成できます。");self.write(self.message.get())
                elif kind=='tile':self.write(f"生成中 {item['tx']}, {item['tz']}  ({item['completed']:,}/{item['total']:,})")
                elif kind=='index':self.message.set(f"索引を作成中：{item['features']:,} 件 / 市町村境界 {item['towns']:,} 件")
                elif kind=='indexed':
                    self.message.set(f"索引を保存しました：{item['features']:,} 件 / 市町村 {item['towns']:,} 件")
                    if item.get('incomplete_towns'):self.write('境界不足で到着通知を省いた自治体：'+', '.join(item['incomplete_towns']))
                elif kind=='planning':self.message.set(f"全国の区画を計画中：{item['tiles']:,} 区画")
                elif kind=='exported':self.last_export=item['path'];self.message.set('ワールドを出力しました：'+item['path']);self.write(self.message.get())
                elif kind in ('log','error'):
                    self.write(item['text'])
                    if kind=='error':self.message.set('処理を完了できませんでした：'+item['text'])
                elif kind=='finished':
                    self.busy=False;self.process=None
                    self.scope_combo.configure(state='readonly');self.scale_combo.configure(state='readonly');self.scope_changed()
                    self.pause_button.configure(state='disabled')
                    if item['code']==0:
                        next_step={'index':'索引作成が完了しました。次に「新規プロジェクトを作成」を押してください。',
                            'create':'プロジェクトを作成しました。次に「1区画を生成」を押してください。',
                            'run':'区画を保存しました。続けて生成、または .mcworld へ出力できます。'}
                        if self.action in next_step:self.message.set(next_step[self.action])
                        self.write(self.message.get())
                    self.refresh_readiness()
                    if item['code']==0 and self.last_export:self.message.set('ワールドを出力しました：'+self.last_export)
        except queue.Empty:pass
        if not self.busy and time.monotonic()>=self.readiness_at:
            self.readiness_at=time.monotonic()+1;self.refresh_readiness()
        self.after_id=self.window.after(150,self.poll)

    def close(self):
        if self.busy:
            if self.action in ('run','index','create','extend','build'):self.pause()
            messagebox.showinfo('処理中です','処理が停止するまで、この画面を開いたままお待ちください。',parent=self.window);return
        if self.after_id:self.window.after_cancel(self.after_id)
        self.app.national_window=None;self.window.destroy()
