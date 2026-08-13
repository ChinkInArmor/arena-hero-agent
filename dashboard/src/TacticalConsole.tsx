import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  ChevronLeft,
  ChevronRight,
  Crosshair,
  Flag,
  Layers3,
  LocateFixed,
  Minus,
  Pause,
  Play,
  Plus,
  Send,
  ShieldAlert,
  SlidersHorizontal,
  Target,
  Trash2,
  Users,
  X,
} from "lucide-react";

interface TacticalUnit {
  id: string;
  unit_type: "WORKER" | "VANGUARD" | "RANGER";
  x: number;
  y: number;
  hp: number;
  cargo: number;
  mode: "AUTO" | "MANUAL" | "EXPEDITION" | "EMERGENCY";
  target_x: number | null;
  target_y: number | null;
}
interface TacticalObject {
  kind: "CORE" | "ENEMY_CORE" | "ENEMY_UNIT" | "RESOURCE" | "OBSTACLE" | "BEACON";
  id: string | null;
  x: number;
  y: number;
  unit_type: string | null;
  hp: number | null;
  shield: number | null;
  last_seen_tick: number;
}
interface ActiveCommand {
  command_id: string;
  unit_id: string | null;
  target_x: number;
  target_y: number;
  expires_tick: number;
  mode: string;
}
interface Expedition {
  id: string;
  name: string;
  target_x: number;
  target_y: number;
  vanguard_count: number;
  ranger_count: number;
  expires_tick: number;
}
interface TacticalState {
  generated_at: string;
  tick: number;
  control_mode: "AUTO" | "MANUAL" | "EXPEDITION" | "EMERGENCY";
  emergency_reason: string | null;
  production_weights: Record<string, number>;
  units: TacticalUnit[];
  objects: TacticalObject[];
  active_commands: ActiveCommand[];
  expeditions: Expedition[];
}
interface Receipt {
  command_id: string;
  tick: number;
  status: string;
  reason: string;
  affected_count: number;
  generated_at: string;
}

type Layer = "resources" | "obstacles" | "enemies" | "routes";
const cellSize = 22;
const unitColors = { WORKER: "#56b884", VANGUARD: "#e4ad5e", RANGER: "#668fbe" };

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: "no-store", ...init });
  if (!response.ok) throw new Error(String(response.status));
  return response.json() as Promise<T>;
}

function TacticalMap({ state, selected, onSelect, onTarget }: {
  state: TacticalState;
  selected: Set<string>;
  onSelect: (id: string, additive: boolean) => void;
  onTarget: (x: number, y: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [layers, setLayers] = useState<Set<Layer>>(new Set(["resources", "obstacles", "enemies", "routes"]));
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const core = state.objects.find((item) => item.kind === "CORE");

  const centerCore = useCallback(() => {
    if (!core || !canvasRef.current) return;
    const canvas = canvasRef.current;
    setOffset({ x: canvas.clientWidth / 2 - core.x * cellSize * scale, y: canvas.clientHeight / 2 - core.y * cellSize * scale });
  }, [core, scale]);

  useEffect(() => { centerCore(); }, [state.tick]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ratio = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * ratio;
    canvas.height = rect.height * ratio;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.fillStyle = "#0d1215";
    ctx.fillRect(0, 0, rect.width, rect.height);
    const step = cellSize * scale;
    ctx.strokeStyle = "#1b252b";
    ctx.lineWidth = 1;
    for (let x = offset.x % step; x < rect.width; x += step) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rect.height); ctx.stroke(); }
    for (let y = offset.y % step; y < rect.height; y += step) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(rect.width, y); ctx.stroke(); }
    const point = (x: number, y: number) => ({ x: offset.x + x * step, y: offset.y + y * step });
    if (layers.has("routes")) {
      ctx.strokeStyle = "rgba(102,143,190,.75)";
      ctx.setLineDash([5, 4]);
      state.active_commands.forEach((order) => {
        const unit = order.unit_id ? state.units.find((item) => item.id === order.unit_id) : core;
        if (!unit) return;
        const a = point(unit.x, unit.y), b = point(order.target_x, order.target_y);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      });
      ctx.setLineDash([]);
    }
    state.objects.forEach((item) => {
      if (item.kind === "RESOURCE" && !layers.has("resources")) return;
      if (item.kind === "OBSTACLE" && !layers.has("obstacles")) return;
      if (item.kind.startsWith("ENEMY") && !layers.has("enemies")) return;
      const p = point(item.x, item.y);
      if (item.kind === "OBSTACLE") { ctx.fillStyle = "#465159"; ctx.fillRect(p.x - step * .42, p.y - step * .42, step * .84, step * .84); return; }
      ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(4, step * .3), 0, Math.PI * 2);
      ctx.fillStyle = item.kind === "CORE" ? "#e8edf0" : item.kind === "BEACON" ? "#e4ad5e" : item.kind === "RESOURCE" ? "#56b884" : "#da6f68";
      ctx.fill();
      if (item.kind === "CORE" || item.kind === "ENEMY_CORE") { ctx.strokeStyle = item.kind === "CORE" ? "#56b884" : "#ff8b82"; ctx.lineWidth = 3; ctx.stroke(); }
    });
    state.units.forEach((unit) => {
      const p = point(unit.x, unit.y), radius = Math.max(5, step * .28);
      ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = unitColors[unit.unit_type]; ctx.fill();
      if (selected.has(unit.id)) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke(); }
      ctx.fillStyle = "#0d1215"; ctx.font = `${Math.max(8, step * .28)}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(unit.unit_type[0], p.x, p.y);
    });
  }, [state, selected, scale, offset, layers, core]);

  const mapPoint = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: Math.round((event.clientX - rect.left - offset.x) / (cellSize * scale)), y: Math.round((event.clientY - rect.top - offset.y) / (cellSize * scale)) };
  };
  return <div className="tactical-map-wrap">
    <canvas ref={canvasRef}
      onMouseDown={(event) => { drag.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y }; }}
      onMouseMove={(event) => { if (drag.current) setOffset({ x: drag.current.ox + event.clientX - drag.current.x, y: drag.current.oy + event.clientY - drag.current.y }); }}
      onMouseUp={(event) => {
        const start = drag.current; drag.current = null;
        if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4) {
          const target = mapPoint(event);
          const unit = state.units.find((item) => item.x === target.x && item.y === target.y);
          if (unit) onSelect(unit.id, event.shiftKey); else onTarget(target.x, target.y);
        }
      }}
      onMouseLeave={() => { drag.current = null; }}
      onWheel={(event) => { event.preventDefault(); setScale((value) => Math.min(2.5, Math.max(.4, value * (event.deltaY > 0 ? .9 : 1.1)))); }}
    />
    <div className="map-actions">
      <button title="缩小" onClick={() => setScale((v) => Math.max(.4, v - .2))}><Minus size={16}/></button>
      <button title="放大" onClick={() => setScale((v) => Math.min(2.5, v + .2))}><Plus size={16}/></button>
      <button title="定位 Core" onClick={centerCore}><LocateFixed size={16}/></button>
      <div className="layer-menu"><Layers3 size={15}/>{(["resources","obstacles","enemies","routes"] as Layer[]).map((layer) => <label key={layer}><input type="checkbox" checked={layers.has(layer)} onChange={() => setLayers((value) => { const next=new Set(value); next.has(layer)?next.delete(layer):next.add(layer); return next; })}/>{layer}</label>)}</div>
    </div>
  </div>;
}

export default function TacticalConsole() {
  const [state, setState] = useState<TacticalState | null>(null);
  const [history, setHistory] = useState<TacticalState[]>([]);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [target, setTarget] = useState<{x:number;y:number}|null>(null);
  const [live, setLive] = useState(true);
  const [historyIndex, setHistoryIndex] = useState(0);
  const [csrf, setCsrf] = useState("");
  const [ttl, setTtl] = useState(32);
  const [weights, setWeights] = useState({worker:4,vanguard:1,ranger:1});
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [next, audit] = await Promise.all([
        requestJson<TacticalState>("/api/v1/tactical/state"),
        requestJson<{items:Receipt[]}>("/api/v1/tactical/receipts?limit=40"),
      ]);
      setState(next); setReceipts(audit.items);
      if (!csrf) setCsrf((await requestJson<{csrf_token:string}>("/api/v1/tactical/csrf")).csrf_token);
    } catch { setMessage("战术状态暂时不可用"); }
  }, [csrf]);
  useEffect(() => { void load(); const timer=window.setInterval(() => { if(live) void load(); }, 5000); return () => clearInterval(timer); }, [load, live]);
  const view = live ? state : history[historyIndex] ?? state;
  const selectedUnits = useMemo(() => view?.units.filter((unit) => selected.has(unit.id)) ?? [], [view, selected]);

  async function send(payload: Record<string, unknown>, method="POST", path="/api/v1/tactical/commands") {
    try {
      const result=await requestJson<{command_id:string}>(path,{method,headers:{"Content-Type":"application/json","X-Arena-CSRF":csrf},body:method==="DELETE"?undefined:JSON.stringify(payload)});
      setMessage(`命令已入队 ${result.command_id?.slice(0,8) ?? ""}`); setTarget(null); void load();
    } catch (error) { setMessage(`命令被拒绝 ${String(error)}`); }
  }
  async function loadHistory() {
    const value=await requestJson<{items:TacticalState[]}>("/api/v1/tactical/history?limit=240");
    setHistory(value.items.reverse()); setHistoryIndex(Math.max(0,value.items.length-1)); setLive(false);
  }
  if (!view) return <div className="tactical-loading">等待私有战术状态</div>;
  return <div className="tactical-console">
    <div className="tactical-toolbar">
      <div className="mode-cluster"><span className={`control-mode mode-${view.control_mode.toLowerCase()}`}>{view.control_mode}</span><b>Tick {view.tick.toLocaleString()}</b>{view.emergency_reason && <em><ShieldAlert size={14}/>{view.emergency_reason}</em>}</div>
      <div className="replay-controls">
        <button title="上一 Tick" disabled={live||historyIndex===0} onClick={()=>setHistoryIndex(v=>v-1)}><ChevronLeft size={16}/></button>
        <button title={live?"载入回放":"返回实时"} onClick={()=>live?void loadHistory():(setLive(true),void load())}>{live?<Pause size={15}/>:<Play size={15}/>}</button>
        {!live && <input type="range" min="0" max={Math.max(0,history.length-1)} value={historyIndex} onChange={e=>setHistoryIndex(Number(e.target.value))}/>}        <button title="下一 Tick" disabled={live||historyIndex>=history.length-1} onClick={()=>setHistoryIndex(v=>v+1)}><ChevronRight size={16}/></button>
      </div>
    </div>
    <div className="tactical-workspace">
      <TacticalMap state={view} selected={selected} onSelect={(id,add)=>setSelected((current)=>{const next=add?new Set(current):new Set<string>();next.has(id)?next.delete(id):next.add(id);return next;})} onTarget={(x,y)=>setTarget({x,y})}/>
      <aside className="command-panel">
        <section><div className="command-title"><Target size={16}/><h2>坐标派遣</h2></div><div className="coordinate-readout"><span>已选 {selected.size}</span><b>{target?`${target.x}, ${target.y}`:"在地图选择目标"}</b></div><label>TTL <input type="number" min="1" max="64" value={ttl} onChange={e=>setTtl(Number(e.target.value))}/></label><div className="command-buttons"><button disabled={!target||!selected.size||!live} onClick={()=>void send({kind:"MOVE_UNITS",unit_ids:[...selected],target_x:target?.x,target_y:target?.y,ttl_ticks:ttl})}><Send size={15}/>派遣单位</button><button disabled={!target||!live} onClick={()=>void send({kind:"MOVE_CORE",target_x:target?.x,target_y:target?.y,ttl_ticks:ttl})}><Crosshair size={15}/>移动 Core</button></div>{selectedUnits.map(unit=><div className="selected-unit" key={unit.id}><i style={{background:unitColors[unit.unit_type]}}/><span>{unit.unit_type} · {unit.x},{unit.y}</span><b>{unit.mode}</b><button title="取消控制" onClick={()=>setSelected(s=>{const n=new Set(s);n.delete(unit.id);return n})}><X size={13}/></button></div>)}</section>
        <section><div className="command-title"><Flag size={16}/><h2>远征队</h2></div><div className="expedition-create"><input id="expedition-name" placeholder="远征名称" defaultValue="远征队 1"/><input id="expedition-v" type="number" min="0" max="32" defaultValue="2" title="Vanguard"/><input id="expedition-r" type="number" min="0" max="32" defaultValue="2" title="Ranger"/><button disabled={!target||!live} onClick={()=>{const name=(document.querySelector('#expedition-name') as HTMLInputElement).value;const v=Number((document.querySelector('#expedition-v') as HTMLInputElement).value);const r=Number((document.querySelector('#expedition-r') as HTMLInputElement).value);void send({kind:"SET_EXPEDITION",expedition_id:`exp-${Date.now()}`,name,target_x:target?.x,target_y:target?.y,vanguard_count:v,ranger_count:r,ttl_ticks:ttl});}}><Plus size={14}/></button></div>{view.expeditions.map(item=><div className="expedition-row" key={item.id}><div><b>{item.name}</b><span>{item.vanguard_count}V · {item.ranger_count}R → {item.target_x},{item.target_y}</span></div><button title="删除远征" onClick={()=>void send({kind:"DELETE_EXPEDITION",expedition_id:item.id})}><Trash2 size={14}/></button></div>)}</section>
        <section><div className="command-title"><SlidersHorizontal size={16}/><h2>生产权重</h2></div>{([['worker','Worker'],['vanguard','Vanguard'],['ranger','Ranger']] as const).map(([key,label])=><label className="weight-control" key={key}><span>{label}</span><input type="range" min="0" max="10" value={weights[key]} onChange={e=>setWeights({...weights,[key]:Number(e.target.value)})}/><b>{weights[key]}</b></label>)}<button className="full-command" disabled={!live||!Object.values(weights).some(Boolean)} onClick={()=>void send({kind:"SET_PRODUCTION_WEIGHTS",worker_weight:weights.worker,vanguard_weight:weights.vanguard,ranger_weight:weights.ranger,ttl_ticks:ttl})}>应用生产权重</button></section>
        <section className="audit-section"><div className="command-title"><Users size={16}/><h2>命令与审计</h2></div>{view.active_commands.map(item=><div className="audit-row active" key={`${item.command_id}-${item.unit_id}`}><div><b>{item.mode}</b><span>→ {item.target_x},{item.target_y} · 到期 {item.expires_tick}</span></div>{!item.command_id.startsWith('expedition:')&&<button title="取消命令" onClick={()=>void send({},"DELETE",`/api/v1/tactical/commands/${item.command_id}`)}><Ban size={14}/></button>}</div>)}{receipts.slice(0,12).map(item=><div className="audit-row" key={`${item.command_id}-${item.tick}`}><span className={`receipt receipt-${item.status.toLowerCase()}`}>{item.status}</span><div><b>{item.reason}</b><span>Tick {item.tick} · {item.affected_count} 对象</span></div></div>)}</section>
        {message&&<div className="command-message">{message}</div>}
      </aside>
    </div>
  </div>;
}
