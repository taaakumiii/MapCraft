"""A topmost, north-up real map driven by the Bedrock GPS content log."""
import base64
import math
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser
from core import pixel
from tracking import ContentLogReader

class Minimap:
    def __init__(self,app,reference,pack_path,source):
        self.app=app;self.reference=reference;self.pack_path=Path(pack_path)
        self.closed=False;self.stopped=threading.Event();self.events=queue.Queue()
        self.reader=ContentLogReader(reference);self.players={};self.pid=None;self.images={}
        self.zoom=16;self.center=None;self.view=None;self.last_path=None
        self.window=tk.Toplevel(app.root);self.window.title('MapCraft 現在地ミニマップ')
        self.window.configure(bg='#101b26');self.window.geometry('370x530+40+60');self.window.minsize(330,470)
        self.window.attributes('-topmost',True);self.window.protocol('WM_DELETE_WINDOW',self.close)
        tools=ttk.Frame(self.window,style='Card.TFrame',padding=7);tools.pack(fill='x')
        self.topmost=tk.BooleanVar(value=True)
        ttk.Checkbutton(tools,text='最前面',variable=self.topmost,command=lambda:self.window.attributes('-topmost',self.topmost.get())).pack(side='left')
        ttk.Button(tools,text='＋',width=3,command=lambda:self.change_zoom(1)).pack(side='right',padx=2)
        ttk.Button(tools,text='−',width=3,command=lambda:self.change_zoom(-1)).pack(side='right',padx=2)
        ttk.Button(tools,text='ワールド…',command=lambda:app.open_navigation(True)).pack(side='right',padx=3)
        self.player_choice=tk.StringVar()
        self.player_combo=ttk.Combobox(self.window,textvariable=self.player_choice,state='disabled')
        self.player_combo.pack(fill='x',padx=7,pady=5)
        self.player_combo.bind('<<ComboboxSelected>>',lambda e:self.select_player())
        self.canvas=tk.Canvas(self.window,bg='#233946',highlightthickness=0)
        self.canvas.pack(fill='both',expand=True,padx=7);self.canvas.bind('<Configure>',lambda e:self.render())
        tk.Label(self.window,text='© OpenStreetMap contributors · ODbL',bg='#101b26',fg='#9db2c2',font=('Yu Gothic UI',8)).pack(anchor='w',padx=7)
        self.status=tk.StringVar(value='未接続：GPSアドオンを有効にして、対象ワールドを開いてください。')
        self.status_label=tk.Label(self.window,textvariable=self.status,bg='#101b26',fg='#72dfb6',justify='left',anchor='w',wraplength=345,font=('Yu Gothic UI',9))
        self.status_label.pack(fill='x',padx=8,pady=5)
        self.google_button=ttk.Button(self.window,text='現在地をGoogleマップで開く',command=self.open_google,state='disabled')
        self.google_button.pack(fill='x',padx=7,pady=3)
        row=ttk.Frame(self.window,style='Card.TFrame',padding=4);row.pack(fill='x',padx=7,pady=(0,6))
        ttk.Button(row,text='GPSアドオンを取り込む',command=lambda:os.startfile(str(self.pack_path))).pack(side='left',expand=True,fill='x')
        ttk.Button(row,text='設定手順',command=self.instructions).pack(side='right',padx=(4,0))
        foot=ttk.Frame(self.window);foot.pack(fill='x',padx=7,pady=(0,6))
        ttk.Button(foot,text='ログを手動指定…',command=self.choose_log).pack(side='left')
        tk.Label(foot,text='GPS '+reference.map_id[:8],bg='#101b26',fg='#9db2c2',font=('Consolas',9)).pack(side='right')
        self.render();threading.Thread(target=self.follow,daemon=True).start();self.window.after(250,self.poll)

    def follow(self):
        while not self.stopped.is_set():
            try:
                packets=self.reader.poll()
                if packets:self.events.put(('packets',packets))
                if self.reader.path!=self.last_path:
                    self.last_path=self.reader.path;self.events.put(('path',str(self.last_path or '')))
            except Exception as exc:self.events.put(('error',str(exc)))
            self.stopped.wait(.25)

    def select_player(self):
        index=self.player_combo.current()
        if 0<=index<len(self.players):self.pid=list(self.players)[index];self.render()

    def fresh(self):
        packet=self.players.get(self.pid)
        return packet if packet and time.monotonic()-packet['_received']<5 else None

    def poll(self):
        if self.closed:return
        changed=False
        try:
            for _ in range(40):
                kind,value=self.events.get_nowait()
                if kind=='packets':
                    for packet in value:
                        packet['_received']=time.monotonic();self.players[packet['pid']]=packet
                        if self.pid is None:self.pid=packet['pid']
                    ids=list(self.players)
                    self.player_combo.configure(values=[p['player'] for p in self.players.values()],state='readonly')
                    self.player_combo.current(ids.index(self.pid));changed=True
                elif kind=='path':self.app.write('現在地ナビのログ: '+value+'\n')
                elif kind=='error':self.app.write('現在地ナビ: '+value+'\n')
        except queue.Empty:pass
        packet=self.fresh()
        if packet:
            self.center=(packet['lat'],packet['lon'])
            suffix='生成範囲内' if packet['inside'] else '生成範囲外'
            self.status.set(f'接続中 · {suffix}\n北緯 {packet["lat"]:.5f} / 東経 {packet["lon"]:.5f}\nX {packet["x"]:.1f} / Z {packet["z"]:.1f}')
            self.google_button.configure(state='normal')
        else:
            self.status.set('現在地を待機中。対象ワールド・GPSアドオン・コンテンツログファイルを確認してください。')
            self.google_button.configure(state='disabled')
        # Movement changes the viewport; stale records must never look live.
        if changed or self.view is None or bool(packet)!=getattr(self,'was_fresh',None):self.render()
        self.was_fresh=bool(packet);self.window.after(250,self.poll)

    def change_zoom(self,amount):
        self.zoom=max(12,min(18,self.zoom+amount));self.render()

    def render(self):
        if self.closed:return
        self.canvas.delete('all');self.images={}
        cw,ch=max(1,self.canvas.winfo_width()),max(1,self.canvas.winfo_height())
        if self.center is None:
            self.canvas.create_text(cw/2,ch/2,text='現在地データを待っています\n\nGPSアドオンを有効にして\nゲーム内を移動してください',fill='#ecf4f8',justify='center',font=('Yu Gothic UI',10))
            self.view=None;return
        cx,cy=pixel(*self.center,self.zoom);ox,oy=cx-cw/2,cy-ch/2
        self.view=(self.zoom,ox,oy,cw,ch)
        for x in range(math.floor(ox/256),math.floor((ox+cw)/256)+1):
            for y in range(math.floor(oy/256),math.floor((oy+ch)/256)+1):
                if not (0<=x<2**self.zoom and 0<=y<2**self.zoom):continue
                key=(self.zoom,x,y)
                if key in self.app.tiles:
                    image=tk.PhotoImage(data=base64.b64encode(self.app.tiles[key]));self.images[key]=image
                    self.canvas.create_image(x*256-ox,y*256-oy,image=image,anchor='nw')
                elif key not in self.app.pending and time.time()-self.app.failed.get(key,0)>60:
                    self.app.pending.add(key);self.app.pool.submit(self.app.load_tile,key)
        s,w,n,e=self.reference.bbox
        ax,ay=pixel(n,w,self.zoom);bx,by=pixel(s,e,self.zoom)
        self.canvas.create_rectangle(ax-ox,ay-oy,bx-ox,by-oy,outline='#72dfb6',width=2)
        self.canvas.create_text(cw-15,15,text='N ↑',fill='#102b25',font=('Yu Gothic UI',10,'bold'),anchor='ne')
        packet=self.fresh()
        if packet:
            angle=math.radians(packet['yaw']);dx,dy=-math.sin(angle),math.cos(angle)
            x,y=cw/2,ch/2
            points=[x+dx*13,y+dy*13,x-dx*7+dy*6,y-dy*7-dx*6,x-dx*7-dy*6,y-dy*7+dx*6]
            self.canvas.create_polygon(*points,fill='#ff5b5b',outline='#ffffff',width=2)
        else:
            self.canvas.create_text(cw/2,ch/2,text='通信待機中',fill='#ffffff',font=('Yu Gothic UI',12,'bold'))

    def on_tile(self,key):
        if self.closed or not self.view or key[0]!=self.zoom:return
        _,ox,oy,cw,ch=self.view
        if key[1]*256<ox+cw and (key[1]+1)*256>ox and key[2]*256<oy+ch and (key[2]+1)*256>oy:self.render()

    def open_google(self):
        packet=self.fresh()
        if packet:webbrowser.open(f'https://www.google.com/maps/search/?api=1&query={packet["lat"]:.7f},{packet["lon"]:.7f}')

    def choose_log(self):
        path=filedialog.askopenfilename(parent=self.window,title='MinecraftのContentLogファイルを選択',filetypes=[('ログ','*.txt *.log'),('すべて','*.*')])
        if path:self.reader=ContentLogReader(self.reference,path)

    def instructions(self):
        messagebox.showinfo('Windows統合版の現在地連携',
            '1.「GPSアドオンを取り込む」を押してMinecraftへ追加します。\n'
            '2. 対象ワールドを閉じ、編集 → ビヘイビアーパック → マイパックから、'
            '「MapCraft GPS '+self.reference.map_id[:8]+'」を有効にします。\n'
            '   新しくMapCraftで生成するワールドには組み込み済みです。\n'
            '3. Minecraftの設定 → クリエイターで「コンテンツログファイル」を有効にします。'
            'GUI表示はオフのままで構いません。\n'
            '4. 対象ワールドを開きます。約0.5秒ごとに現在地を更新します。\n'
            '5. Minecraftをウィンドウ表示にし、この地図を画面の隅へ移動してください。\n\n'
            '地図はOpenStreetMapです。「現在地をGoogleマップで開く」で同じ場所を確認できます。'
            'Googleマップ側の表示は、ボタンを押した時点の場所です。',parent=self.window)

    def close(self):
        self.closed=True;self.stopped.set();self.window.destroy()
        if self.app.navigator is self:self.app.navigator=None
