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
  behavior: string | null;
}
interface MemoryObject {
  key: string;
  x: number;
  y: number;
  last_seen_tick: number;
  unit_type: string | null;
}
interface TacticalMemory {
  obstacles: number[][];
  resources: MemoryObject[];
  enemies: MemoryObject[];
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
  memory: TacticalMemory | null;
}
interface Receipt {
  command_id: string;
  tick: number;
  status: string;
  reason: string;
  affected_count: number;
  generated_at: string;
}

type Layer = "resources" | "obstacles" | "enemies" | "routes" | "memory";
const cellSize = 22;
const layerLabels: Record<Layer, string> = { resources: "资源", obstacles: "障碍", enemies: "敌军", routes: "路线", memory: "记忆" };
const layerOrder: Layer[] = ["resources", "obstacles", "enemies", "routes", "memory"];

const unitColors = { WORKER: "#56b884", VANGUARD: "#e4ad5e", RANGER: "#668fbe" };
// 阻塞类行为：卡片状态下这些单位无法动弹，是交付/资源卡死的元凶。
const BLOCKED_BEHAVIORS = new Set([
  "RETURN_BLOCKED",
  "RESOURCE_BLOCKED",
  "CLEAR_CORE_BLOCKED",
  "SCOUT_RETURN_BLOCKED",
  "DELIVERY_CHAIN_CLEAR",
  "DELIVERY_CHAIN_CARGO",
  "EVADE",
  "EVADE_CARGO",
]);
const behaviorLabels: Record<string, string> = {
  DEPOSIT: "交付",
  HARVEST: "采集",
  RETURN: "返航",
  MINE: "开采",
  SCOUT: "侦察",
  SCOUT_BLOCKED: "侦察堵",
  SCOUT_RETURN: "侦察返",
  SCOUT_RETURN_BLOCKED: "侦察返堵",
  RETURN_BLOCKED: "返航堵",
  RESOURCE_BLOCKED: "矿点堵",
  CLEAR_CORE: "清核",
  CLEAR_CORE_BLOCKED: "清核堵",
  DELIVERY_CHAIN_CLEAR: "链清堵",
  DELIVERY_CHAIN_CARGO: "链货堵",
  EVADE: "规避",
  EVADE_CARGO: "货规避",
  COMBAT_RALLY: "战斗集",
  COMBAT_HOLD: "战斗待",
  STAND_AND_FIGHT: "坚守",
  WANDER: "游荡",
  IDLE: "闲置",
  UNKNOWN: "未知",
};
// 记忆置信度：距离上次观测越久越淡。64 tick 内 90%->40%，之后衰减到 18% 保留。
function memoryConfidence(age: number): number {
  if (age <= 0) return 0.92;
  if (age >= 256) return 0.18;
  return 0.92 - (0.74 * age) / 256;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: "no-store", ...init });
  if (!response.ok) throw new Error(String(response.status));
  return response.json() as Promise<T>;
}

function UnitPanel({ state, selected, onSelect, onFocus, onClose }: {
  state: TacticalState;
  selected: Set<string>;
  onSelect: (id: string) => void;
  onFocus: (id: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"all" | "alert" | "worker" | "combat">("all");
  const listRef = useRef<HTMLDivElement>(null);
  const abnormal = (unit: TacticalUnit) =>
    (unit.behavior && BLOCKED_BEHAVIORS.has(unit.behavior))
    || unit.hp <= (unit.unit_type === "VANGUARD" ? 2 : 1)
    || (unit.unit_type === "WORKER" && unit.cargo > 0 && unit.behavior === "UNKNOWN");
  const visible = state.units
    .filter((unit) => tab === "all" || (tab === "alert" ? abnormal(unit) : tab === "worker" ? unit.unit_type === "WORKER" : unit.unit_type !== "WORKER"))
    .slice()
    .sort((a, b) => (abnormal(b) ? 1 : 0) - (abnormal(a) ? 1 : 0) || a.y - b.y || a.x - b.x);
  const alertCount = state.units.filter(abnormal).length;
  const selectedRef = useRef<Set<string>>(selected);
  selectedRef.current = selected;
  // 地图点选后同步滚动到该行
  useEffect(() => {
    const items = listRef.current?.querySelectorAll<HTMLElement>("[data-unit]");
    if (!items) return;
    items.forEach((node) => { if (selected.has(node.dataset.unit ?? "")) node.scrollIntoView({ block: "nearest" }); });
  }, [selected]);
  return <aside className="unit-panel">
    <div className="unit-panel-header">
      <span className="eyebrow">单位状态 <b>{state.units.length}</b></span>
      <button className="icon-button" title="收起状态栏" onClick={onClose}><ChevronLeft size={14} /></button>
    </div>
    <div className="unit-tabs">
      <button className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>全部</button>
      <button className={`${tab === "alert" ? "active" : ""}${alertCount ? " has-alert" : ""}`} onClick={() => setTab("alert")}>异常{alertCount ? `·${alertCount}` : ""}</button>
      <button className={tab === "worker" ? "active" : ""} onClick={() => setTab("worker")}>Worker</button>
      <button className={tab === "combat" ? "active" : ""} onClick={() => setTab("combat")}>战斗</button>
    </div>
    <div className="unit-list" ref={listRef}>
      {visible.length ? visible.map((unit) => (
        <div key={unit.id} data-unit={unit.id}
          className={`unit-row${selected.has(unit.id) ? " selected" : ""}${abnormal(unit) ? " abnormal" : ""}`}
          onClick={() => { onSelect(unit.id); onFocus(unit.id); }}>
          <i style={{ background: unitColors[unit.unit_type] }} />
          <div className="unit-row-main">
            <b>{unit.unit_type[0]}{unit.id.slice(0, 4)}</b>
            <span>{behaviorLabels[unit.behavior ?? ""] ?? unit.behavior ?? "—"}</span>
          </div>
          <div className="unit-row-hp">
            <i style={{ width: `${Math.max(10, (unit.hp / (unit.unit_type === "VANGUARD" ? 4 : 2)) * 100)}%`, background: unit.hp <= (unit.unit_type === "VANGUARD" ? 2 : 1) ? "#c66370" : "#76b889" }} />
            <b>{unit.hp}</b>
          </div>
          <span className="unit-row-meta">{unit.cargo ? `货${unit.cargo}` : ""}{unit.target_x != null ? `${unit.x},${unit.y}→${unit.target_x},${unit.target_y}` : `${unit.x},${unit.y}`}</span>
        </div>
      )) : <div className="unit-empty"><Users size={18} /><span>无匹配单位</span></div>}
    </div>
  </aside>;
}

function TacticalMap({ state, selected, onSelect, onTarget, focus }: {
  state: TacticalState;
  selected: Set<string>;
  onSelect: (id: string, additive: boolean) => void;
  onTarget: (x: number, y: number) => void;
  focus: { id: string; version: number } | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [layers, setLayers] = useState<Set<Layer>>(new Set(["resources", "obstacles", "enemies", "routes", "memory"]));
  const [hover, setHover] = useState<{ x: number; y: number; title: string; detail: string } | null>(null);
  const [drawVersion, setDrawVersion] = useState(0);
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const centered = useRef(false);
  const hoverRef = useRef<typeof hover>(null);
  const viewRef = useRef({ scale, offset });
  viewRef.current = { scale, offset };
  const core = state.objects.find((item) => item.kind === "CORE");

  // 缩放锚定：把锚点（光标 / 画布中心）保持在原屏幕位置，
  // offset 必须随 scale 同步变化，否则绕左上角缩放、视角漂移。
  const zoomBy = useCallback((factor: number, anchor: { x: number; y: number }) => {
    const { scale: current, offset: currentOffset } = viewRef.current;
    const next = Math.min(2.5, Math.max(0.4, current * factor));
    if (next === current) return;
    const ratio = next / current;
    setScale(next);
    setOffset({
      x: anchor.x - (anchor.x - currentOffset.x) * ratio,
      y: anchor.y - (anchor.y - currentOffset.y) * ratio,
    });
  }, []);

  const centerCore = useCallback(() => {
    if (!core || !canvasRef.current) return;
    const canvas = canvasRef.current;
    const { scale: current } = viewRef.current;
    setOffset({ x: canvas.clientWidth / 2 - core.x * cellSize * current, y: canvas.clientHeight / 2 - core.y * cellSize * current });
  }, [core]);

  // 外部聚焦（侧栏点选）：把镜头居中到该单位保持当前缩放。
  useEffect(() => {
    if (!focus || !canvasRef.current) return;
    const unit = state.units.find((item) => item.id === focus.id);
    if (!unit) return;
    const canvas = canvasRef.current;
    const { scale: current } = viewRef.current;
    setOffset({ x: canvas.clientWidth / 2 - unit.x * cellSize * current, y: canvas.clientHeight / 2 - unit.y * cellSize * current });
  }, [focus, state]);

  // 仅在地图首次拿到 Core 时定位一次取景；Tick 轮询更新不再重置用户
  // 缩放/平移后的视角（修复“每 5 秒自动回中”）。
  useEffect(() => {
    if (core && !centered.current) {
      centered.current = true;
      centerCore();
    }
  }, [core, centerCore]);

  // React 的 onWheel 走被动监听、preventDefault 无效，改挂原生非被动监听。
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      zoomBy(event.deltaY > 0 ? 0.9 : 1.1, { x: event.clientX - rect.left, y: event.clientY - rect.top });
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [zoomBy]);

  // 容器尺寸变化（窗口缩放、侧栏展开）时强制重绘，避免画布模糊。
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => setDrawVersion((version) => version + 1));
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

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
    ctx.fillStyle = "#050607";
    ctx.fillRect(0, 0, rect.width, rect.height);
    const step = cellSize * scale;
    ctx.strokeStyle = "rgba(255,255,255,0.045)";
    ctx.lineWidth = 1;
    for (let x = offset.x % step; x < rect.width; x += step) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, rect.height); ctx.stroke(); }
    for (let y = offset.y % step; y < rect.height; y += step) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(rect.width, y); ctx.stroke(); }
    const point = (x: number, y: number) => ({ x: offset.x + x * step, y: offset.y + y * step });
    // —— 记忆层（持久已知地图）：障碍永久、资源/敌军按置信度淡出 ——
    if (layers.has("memory") && state.memory) {
      const mem = state.memory;
      const seen = new Set<string>();
      state.objects.forEach((item) => item.kind === "OBSTACLE" && mem.obstacles.some((pair) => pair[0] === item.x && pair[1] === item.y) && seen.add(`o:${item.x},${item.y}`));
      mem.obstacles.forEach(([x, y]) => {
        const p = point(x, y);
        if (seen.has(`o:${x},${y}`)) return; // 当前可见的障碍由 objects 层绘制
        ctx.fillStyle = "rgba(255,255,255,0.05)";
        ctx.fillRect(p.x - step * .4, p.y - step * .4, step * .8, step * .8);
      });
      const tick = state.tick;
      mem.resources.forEach((entry) => {
        const age = tick - entry.last_seen_tick;
        if (age > 700) return;
        const confidence = memoryConfidence(age);
        const p = point(entry.x, entry.y);
        ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(3, step * .22), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(118,184,137,${(0.14 + 0.5 * confidence).toFixed(3)})`;
        ctx.fill();
      });
      mem.enemies.forEach((entry) => {
        const age = tick - entry.last_seen_tick;
        const confidence = memoryConfidence(age);
        const p = point(entry.x, entry.y);
        ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(4, step * .3), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(198,99,112,${(0.10 + 0.45 * confidence).toFixed(3)})`;
        ctx.fill();
        // 虚线圆表示位置不确定
        ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(6, step * .45), 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(198,99,112,${(0.25 * confidence).toFixed(3)})`;
        ctx.setLineDash([3, 4]); ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
      });
    }
    if (layers.has("routes")) {
      ctx.strokeStyle = "rgba(139,183,212,0.6)";
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
      if (item.kind === "OBSTACLE") { ctx.fillStyle = "#2a2a2e"; ctx.fillRect(p.x - step * .42, p.y - step * .42, step * .84, step * .84); return; }
      ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(4, step * .3), 0, Math.PI * 2);
      ctx.fillStyle = item.kind === "CORE" ? "#f4f4f5" : item.kind === "BEACON" ? "#d9a62e" : item.kind === "RESOURCE" ? "#76b889" : "#c66370";
      ctx.fill();
      if (item.kind === "CORE" || item.kind === "ENEMY_CORE") { ctx.strokeStyle = item.kind === "CORE" ? "#76b889" : "#ef9da8"; ctx.lineWidth = 3; ctx.stroke(); }
    });
    state.units.forEach((unit) => {
      const p = point(unit.x, unit.y), radius = Math.max(5, step * .28);
      ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = unitColors[unit.unit_type]; ctx.fill();
      if (selected.has(unit.id)) { ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke(); }
      // 行为编码：阻塞->红环，低血->暗点半透明，载货->金色内点
      if (unit.behavior && BLOCKED_BEHAVIORS.has(unit.behavior)) {
        ctx.beginPath(); ctx.arc(p.x, p.y, radius + 3, 0, Math.PI * 2);
        ctx.strokeStyle = "#c66370"; ctx.lineWidth = 2; ctx.stroke();
      } else if (unit.hp <= (unit.unit_type === "VANGUARD" ? 2 : 1)) {
        ctx.globalAlpha = 0.55;
        ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2); ctx.fill();
        ctx.globalAlpha = 1;
      }
      if (unit.cargo > 0) {
        ctx.beginPath(); ctx.arc(p.x, p.y - radius - 3, 3, 0, Math.PI * 2);
        ctx.fillStyle = "#e1b64e"; ctx.fill();
      }
      ctx.fillStyle = "#050607"; ctx.font = `${Math.max(8, step * .28)}px sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(unit.unit_type[0], p.x, p.y);
    });
    // 图例
    if (scale >= 0.55) {
      const legend = [
        ["己方", "#56b884"],
        ["阻塞", "#c66370"],
        ["载货", "#e1b64e"],
        ["记忆·低置信", "rgba(118,184,137,0.25)"],
        ["敌方·记忆", "rgba(198,99,112,0.4)"],
      ] as const;
      const box = 10, gap = 14, pad = 9;
      const width = legend.reduce((total, [label]) => total + box + 6 + label.length * 6.4, pad * 2 + (legend.length - 1) * gap);
      const y = rect.height - 34;
      ctx.fillStyle = "rgba(5,6,7,0.72)";
      ctx.strokeStyle = "rgba(255,255,255,0.08)";
      ctx.beginPath(); ctx.roundRect(10, y - 14, width, 26, 6); ctx.fill(); ctx.stroke();
      let x = 10 + pad;
      ctx.font = "10px sans-serif"; ctx.textBaseline = "middle";
      legend.forEach(([label, color]) => {
        ctx.fillStyle = color; ctx.fillRect(x, y - 4, box, box);
        ctx.fillStyle = "#a1a1aa"; ctx.fillText(label, x + box + 6, y + 1);
        x += box + 6 + label.length * 6.4 + gap;
      });
    }
  }, [state, selected, scale, offset, layers, core, drawVersion]);

  const mapPoint = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: Math.round((event.clientX - rect.left - offset.x) / (cellSize * scale)), y: Math.round((event.clientY - rect.top - offset.y) / (cellSize * scale)) };
  };
  // 悬停信息：命中单位/对象时显示 HP、模式、目标等工具提示；
  // 未命中当前可见时回落到记忆层（显示置信度）。
  const updateHover = (clientX: number, clientY: number) => {
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const target = mapPoint({ clientX, clientY, currentTarget: canvasRef.current } as React.MouseEvent<HTMLCanvasElement>);
    const unit = state.units.find((item) => item.x === target.x && item.y === target.y);
    const object = !unit && state.objects.find((item) => item.x === target.x && item.y === target.y);
    const kindLabels: Record<string, string> = { CORE: "Core", ENEMY_CORE: "敌方 Core", RESOURCE: "资源", OBSTACLE: "障碍", BEACON: "信标", ENEMY_UNIT: "敌方单位" };
    const next = unit
      ? { x: clientX - rect.left, y: clientY - rect.top, title: `${unit.unit_type} · HP ${unit.hp}`, detail: `${unit.mode}${unit.cargo ? ` · 载货 ${unit.cargo}` : ""}${unit.behavior ? ` · ${behaviorLabels[unit.behavior] ?? unit.behavior}` : ""}${unit.target_x != null ? ` → ${unit.target_x},${unit.target_y}` : ""}` }
      : object
        ? { x: clientX - rect.left, y: clientY - rect.top, title: kindLabels[object.kind] ?? object.kind, detail: [object.unit_type, object.hp != null ? `HP ${object.hp}` : null, object.shield != null ? `SH ${object.shield}` : null].filter(Boolean).join(" · ") + (object.last_seen_tick ? ` · 末见 T${object.last_seen_tick}` : "") }
        : (() => {
          const mem = state.memory;
          if (!mem) return null;
          const age = (entry: MemoryObject) => state.tick - entry.last_seen_tick;
          const entry = mem.resources.find((item) => item.x === target.x && item.y === target.y && age(item) <= 700)
            ?? mem.enemies.find((item) => item.x === target.x && item.y === target.y);
          if (!entry) return null;
          const isEnemy = mem.enemies.includes(entry);
          return {
            x: clientX - rect.left, y: clientY - rect.top,
            title: isEnemy ? (entry.unit_type ? `敌方 ${entry.unit_type}` : "敌方单位") : "资源点",
            detail: `记忆 ${Math.round(memoryConfidence(age(entry)) * 100)}% · 末见 T${entry.last_seen_tick}`,
          };
        })();
    if (hoverRef.current?.title !== next?.title || hoverRef.current?.x !== next?.x || hoverRef.current?.y !== next?.y || (hoverRef.current === null) !== (next === null)) {
      hoverRef.current = next;
      setHover(next);
    }
  };
  return <div className="tactical-map-wrap">
    <canvas ref={canvasRef}
      onMouseDown={(event) => { drag.current = { x: event.clientX, y: event.clientY, ox: offset.x, oy: offset.y }; }}
      onMouseMove={(event) => { if (drag.current) setOffset({ x: drag.current.ox + event.clientX - drag.current.x, y: drag.current.oy + event.clientY - drag.current.y }); else updateHover(event.clientX, event.clientY); }}
      onMouseUp={(event) => {
        const start = drag.current; drag.current = null;
        if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4) {
          const target = mapPoint(event);
          const unit = state.units.find((item) => item.x === target.x && item.y === target.y);
          if (unit) onSelect(unit.id, event.shiftKey); else onTarget(target.x, target.y);
        }
      }}
      onMouseLeave={() => { drag.current = null; hoverRef.current = null; setHover(null); }}
      onMouseOut={() => { drag.current = null; }}
    />
    {hover && <div className="map-tooltip" style={{ left: hover.x, top: hover.y }}><b>{hover.title}</b><span>{hover.detail}</span></div>}
    <div className="map-actions">
      <button title="缩小" onClick={() => { const rect = canvasRef.current?.getBoundingClientRect(); zoomBy(0.8, rect ? { x: rect.width / 2, y: rect.height / 2 } : { x: 0, y: 0 }); }}><Minus size={16}/></button>
      <button title="放大" onClick={() => { const rect = canvasRef.current?.getBoundingClientRect(); zoomBy(1.25, rect ? { x: rect.width / 2, y: rect.height / 2 } : { x: 0, y: 0 }); }}><Plus size={16}/></button>
      <button title="定位 Core" onClick={centerCore}><LocateFixed size={16}/></button>
      <div className="layer-menu"><Layers3 size={15}/>{layerOrder.map((layer) => <label key={layer}><input type="checkbox" checked={layers.has(layer)} onChange={() => setLayers((value) => { const next=new Set(value); next.has(layer)?next.delete(layer):next.add(layer); return next; })}/>{layerLabels[layer]}</label>)}</div>
    </div>
  </div>;
}

export default function TacticalConsole({ active = true, jumpToTick, onJumpHandled }: {
  active?: boolean;
  jumpToTick?: number;
  onJumpHandled?: () => void;
}) {
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
  const [weightsTouched, setWeightsTouched] = useState(false);
  const [message, setMessage] = useState("");
  const [panelOpen, setPanelOpen] = useState(true);
  const [focusUnit, setFocusUnit] = useState<{ id: string; version: number } | null>(null);

  // csrf 独立拉取一次即可，避免作为 load 依赖导致每次 setCsrf 后 effect 重跑、
  // 产生“双重加载”与重复轮询（配 tab 切换时状态重置问题一并修复）。
  useEffect(() => {
    let cancelled = false;
    requestJson<{csrf_token:string}>("/api/v1/tactical/csrf")
      .then((value) => { if (!cancelled) setCsrf(value.csrf_token); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    try {
      const [next, audit] = await Promise.all([
        requestJson<TacticalState>("/api/v1/tactical/state"),
        requestJson<{items:Receipt[]}>("/api/v1/tactical/receipts?limit=40"),
      ]);
      setState(next); setReceipts(audit.items);
    } catch { setMessage("战术状态暂时不可用"); }
  }, []);
  // 后台标签页暂停轮询，回到前台立即补一次，避免双视图 + 后台重复请求。
  useEffect(() => {
    if (!active) return;
    void load();
    const onVisible=() => { if(!document.hidden && live) void load(); };
    const timer=window.setInterval(() => { if(live && !document.hidden) void load(); }, 5000);
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [load, live, active]);
  const view = live ? state : history[historyIndex] ?? state;
  const selectedUnits = useMemo(() => view?.units.filter((unit) => selected.has(unit.id)) ?? [], [view, selected]);

  // 生产权重从后端快照同步（WORKER/VANGUARD/RANGER 大写键）。用户拖动滑块后
  // 以其输入为准（weightsTouched）；应用成功后后端回显一致值，解除锁定继续同步。
  useEffect(() => {
    if (!state) return;
    const raw = state.production_weights ?? {};
    const synced = {
      worker: raw.WORKER ?? weights.worker,
      vanguard: raw.VANGUARD ?? weights.vanguard,
      ranger: raw.RANGER ?? weights.ranger,
    };
    if (weightsTouched) {
      const equal = synced.worker === weights.worker && synced.vanguard === weights.vanguard && synced.ranger === weights.ranger;
      if (equal) setWeightsTouched(false);
      return;
    }
    setWeights(synced);
  }, [state]);

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
  // 事件流跳转：载入回放并定位到目标 tick（取不晚于它的最近一帧）。
  useEffect(() => {
    if (jumpToTick == null) return;
    let cancelled = false;
    requestJson<{ items: TacticalState[] }>("/api/v1/tactical/history?limit=240")
      .then((value) => {
        if (cancelled) return;
        const frames = value.items.slice().reverse();
        setHistory(frames);
        let index = frames.length - 1;
        for (let i = 0; i < frames.length; i++) {
          if (frames[i].tick >= jumpToTick) { index = i; break; }
        }
        setHistoryIndex(Math.max(0, index));
        setLive(false);
        onJumpHandled?.();
      })
      .catch(() => setMessage("回放跳转失败：历史不可用"));
    return () => { cancelled = true; };
  }, [jumpToTick]);
  if (!view) return <div className="tactical-loading" hidden={!active} style={{display: active ? undefined : "none"}}>等待私有战术状态</div>;
  return <div className="tactical-console" hidden={!active} style={{display: active ? undefined : "none"}}>
    <div className="tactical-toolbar">
      <div className="mode-cluster"><span className={`control-mode mode-${view.control_mode.toLowerCase()}`}>{view.control_mode}</span><b>Tick {view.tick.toLocaleString()}</b>{view.emergency_reason && <em><ShieldAlert size={14}/>{view.emergency_reason}</em>}</div>
      <div className="replay-controls">
        <button title="上一 Tick" disabled={live||historyIndex===0} onClick={()=>setHistoryIndex(v=>v-1)}><ChevronLeft size={16}/></button>
        <button title={live?"载入回放":"返回实时"} onClick={()=>live?void loadHistory():(setLive(true),void load())}>{live?<Pause size={15}/>:<Play size={15}/>}</button>
        {!live && <input type="range" min="0" max={Math.max(0,history.length-1)} value={historyIndex} onChange={e=>setHistoryIndex(Number(e.target.value))}/>}        <button title="下一 Tick" disabled={live||historyIndex>=history.length-1} onClick={()=>setHistoryIndex(v=>v+1)}><ChevronRight size={16}/></button>
      </div>
    </div>
    <div className={`tactical-workspace${panelOpen ? "" : " panel-collapsed"}`}>
      {panelOpen && <UnitPanel
        state={view}
        selected={selected}
        onSelect={(id) => setSelected((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; })}
        onFocus={(id) => setFocusUnit((value) => ({ id, version: (value?.version ?? 0) + 1 }))}
        onClose={() => setPanelOpen(false)}
      />}
      {!panelOpen && <button className="unit-panel-restore" title="展开单位状态" onClick={() => setPanelOpen(true)}><ChevronRight size={15} /><span>单位</span></button>}
      <TacticalMap state={view} selected={selected} onSelect={(id,add)=>setSelected((current)=>{const next=add?new Set(current):new Set<string>();next.has(id)?next.delete(id):next.add(id);return next;})} onTarget={(x,y)=>setTarget({x,y})} focus={focusUnit}/>
      <aside className="command-panel">
        <section><div className="command-title"><Target size={16}/><h2>坐标派遣</h2></div><div className="coordinate-readout"><span>已选 {selected.size}</span><b>{target?`${target.x}, ${target.y}`:"在地图选择目标"}</b></div><label>TTL <input type="number" min="1" max="64" value={ttl} onChange={e=>setTtl(Number(e.target.value))}/></label><div className="command-buttons"><button disabled={!target||!selected.size||!live} onClick={()=>void send({kind:"MOVE_UNITS",unit_ids:[...selected],target_x:target?.x,target_y:target?.y,ttl_ticks:ttl})}><Send size={15}/>派遣单位</button><button disabled={!target||!live} onClick={()=>void send({kind:"MOVE_CORE",target_x:target?.x,target_y:target?.y,ttl_ticks:ttl})}><Crosshair size={15}/>移动 Core</button></div>{selectedUnits.map(unit=><div className="selected-unit" key={unit.id}><i style={{background:unitColors[unit.unit_type]}}/><span>{unit.unit_type} · {unit.x},{unit.y}</span><b>{unit.mode}</b><button title="取消控制" onClick={()=>setSelected(s=>{const n=new Set(s);n.delete(unit.id);return n})}><X size={13}/></button></div>)}</section>
        <section><div className="command-title"><Flag size={16}/><h2>远征队</h2></div><div className="expedition-create"><input id="expedition-name" placeholder="远征名称" defaultValue="远征队 1"/><input id="expedition-v" type="number" min="0" max="32" defaultValue="2" title="Vanguard"/><input id="expedition-r" type="number" min="0" max="32" defaultValue="2" title="Ranger"/><button disabled={!target||!live} onClick={()=>{const name=(document.querySelector('#expedition-name') as HTMLInputElement).value;const v=Number((document.querySelector('#expedition-v') as HTMLInputElement).value);const r=Number((document.querySelector('#expedition-r') as HTMLInputElement).value);void send({kind:"SET_EXPEDITION",expedition_id:`exp-${Date.now()}`,name,target_x:target?.x,target_y:target?.y,vanguard_count:v,ranger_count:r,ttl_ticks:ttl});}}><Plus size={14}/></button></div>{view.expeditions.map(item=><div className="expedition-row" key={item.id}><div><b>{item.name}</b><span>{item.vanguard_count}V · {item.ranger_count}R → {item.target_x},{item.target_y}</span></div><button title="删除远征" onClick={()=>void send({kind:"DELETE_EXPEDITION",expedition_id:item.id})}><Trash2 size={14}/></button></div>)}</section>
        <section><div className="command-title"><SlidersHorizontal size={16}/><h2>生产权重</h2></div>{([['worker','Worker'],['vanguard','Vanguard'],['ranger','Ranger']] as const).map(([key,label])=><label className="weight-control" key={key}><span>{label}</span><input type="range" min="0" max="10" value={weights[key]} onChange={e=>{setWeightsTouched(true); setWeights({...weights,[key]:Number(e.target.value)})}}/><b>{weights[key]}</b></label>)}<button className="full-command" disabled={!live||!Object.values(weights).some(Boolean)} onClick={()=>void send({kind:"SET_PRODUCTION_WEIGHTS",worker_weight:weights.worker,vanguard_weight:weights.vanguard,ranger_weight:weights.ranger,ttl_ticks:ttl})}>应用生产权重</button></section>
        <section className="audit-section"><div className="command-title"><Users size={16}/><h2>命令与审计</h2></div>{view.active_commands.map(item=><div className="audit-row active" key={`${item.command_id}-${item.unit_id}`}><div><b>{item.mode}</b><span>→ {item.target_x},{item.target_y} · 到期 {item.expires_tick}</span></div>{!item.command_id.startsWith('expedition:')&&<button title="取消命令" onClick={()=>void send({},"DELETE",`/api/v1/tactical/commands/${item.command_id}`)}><Ban size={14}/></button>}</div>)}{receipts.slice(0,12).map(item=><div className="audit-row" key={`${item.command_id}-${item.tick}`}><span className={`receipt receipt-${item.status.toLowerCase()}`}>{item.status}</span><div><b>{item.reason}</b><span>Tick {item.tick} · {item.affected_count} 对象</span></div></div>)}</section>
        {message&&<div className="command-message">{message}</div>}
      </aside>
    </div>
  </div>;
}
