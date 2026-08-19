import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  Box,
  BrainCircuit,
  ChevronRight,
  CircleAlert,
  Crosshair,
  Database,
  Gauge,
  HardDrive,
  Map,
  RefreshCw,
  Shield,
  Swords,
  Users,
  Wheat,
} from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import TacticalConsole from "./TacticalConsole";

interface Observation {
  generated_at: string;
  tick: number;
  agent: { core_alive: boolean; compatibility_hold: boolean; recovery: boolean };
  economy: {
    resources: number;
    capacity: number;
    cargo: number;
    visible_resources: number;
    known_resources: number;
    delivery_blocked: number;
    resource_blocked: number;
  };
  population: { total: number; workers: number; vanguards: number; rangers: number };
  core: { alive: boolean; hp: number | null; shield: number | null; state: string };
  battlefield: {
    visible_enemies: number;
    danger_cells: number;
    combat_pressure: boolean;
    projected_core_damage: number;
    core_survival_margin: number;
    scout_chunks: number;
    dedicated_scouts: number;
    beacon_runner_active: boolean;
    combat_patrol_units: number;
  };
  strategy: {
    phase: string;
    posture: string;
    source: string;
    reason: string;
    valid_until_tick: number;
    state: string;
    population_health: string;
    beacon_mode: string;
    state_entered_tick: number;
    state_dwell_ticks: number;
    worker_target: number;
    vanguard_target: number;
    ranger_target: number;
    economic_target: number;
    military_target: number;
    committed_population: number;
    production_ceiling: number;
    population_limit: number;
    migration_shadow: {
      enabled: boolean;
      evaluated: boolean;
      status: "NOT_EVALUATED" | "BLOCKED" | "READY";
      reason: string;
      candidate_count: number;
      reserve_sufficient: boolean;
      escort_sufficient: boolean;
      cargo_safe: boolean;
      abort_available: boolean;
      restricted_ticks_per_cell: number;
      score: number;
      authoritative_rechecks: number;
    };
    economy_weight: number;
    territory_weight: number;
    combat_weight: number;
    safety_weight: number;
    beacon_priority: number;
    scout_percent: number;
    force_stage: "ESTABLISH" | "MOBILIZE" | "CONTROL" | "OVERWHELM";
    force_stage_index: number;
    force_target_population: number;
    force_target_workers: number;
    force_target_vanguards: number;
    force_target_rangers: number;
    force_worker_deficit: number;
    force_vanguard_deficit: number;
    force_ranger_deficit: number;
  };
  adviser: {
    enabled: boolean;
    provider: string | null;
    model: string | null;
    outcome: string;
    requests: number;
    applied: number;
    failures: number;
    next_request_tick: number | null;
    ttl_remaining_ticks: number | null;
    overridden: boolean;
  };
  actions: Record<string, number>;
  worker_modes: Record<string, number>;
}

interface Overview {
  generated_at: string;
  status: "healthy" | "stale" | "unavailable";
  stale_after_seconds: number;
  age_seconds: number | null;
  deployment: {
    release: string | null;
    version: string | null;
    commit: string | null;
    service_active: boolean | null;
    restarts: number | null;
    active_since: string | null;
  };
  observation: Observation | null;
}

interface HistoryPoint {
  observed_at?: string;
  hour_start?: string;
  tick?: number;
  resources?: number;
  resources_avg?: number;
  capacity?: number;
  capacity_max?: number;
  population?: number;
  population_max?: number;
  workers?: number;
  workers_max?: number;
  vanguards?: number;
  vanguards_max?: number;
  rangers?: number;
  rangers_max?: number;
  delivery_blocked?: number;
  delivery_blocked_avg?: number;
  resource_blocked?: number;
  resource_blocked_avg?: number;
}

interface EventItem {
  event_id: string;
  observed_at: string;
  tick: number;
  category: string;
  event_type: string;
  reason_code: string | null;
  values: Record<string, number>;
}

const ranges = ["1h", "6h", "24h", "7d", "30d", "90d"] as const;
const categoryNames: Record<string, string> = {
  BEACON: "信标",
  SPAWN: "生产",
  ECONOMY: "经济",
  COMBAT: "战斗",
  UNIT: "单位",
  SYSTEM: "系统",
};
const forceStageNames: Record<string, string> = {
  ESTABLISH: "建立据点",
  MOBILIZE: "全面动员",
  CONTROL: "区域控制",
  OVERWHELM: "军团压制",
};
const reasonNames: Record<string, string> = {
  emergency_safety_override: "紧急安全覆盖",
  visible_enemy_core_opportunity: "发现敌方 Core 机会",
  beacon_contest_enabled: "信标争夺已启用",
  storage_saturated: "仓储饱和扩张",
  sustained_economic_evidence: "持续经济证据",
  validated_model_advice: "已验证模型建议",
  deterministic_baseline: "确定性基线",
};

function formatTime(value?: string | null) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function shortCommit(value?: string | null) {
  return value ? value.slice(0, 12) : "--";
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json() as Promise<T>;
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
  tone = "neutral",
}: {
  icon: typeof Activity;
  label: string;
  value: string | number;
  detail: string;
  tone?: "neutral" | "green" | "amber" | "red" | "blue";
}) {
  return (
    <article className={`metric metric-${tone}`}>
      <div className="metric-label"><Icon size={16} />{label}</div>
      <strong>{value}</strong>
      <span>{detail}</span>
    </article>
  );
}

function Weight({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="weight-row">
      <span>{label}</span>
      <div className="weight-track"><i style={{ width: `${value * 10}%`, background: color }} /></div>
      <b>{value}</b>
    </div>
  );
}

function App() {
  const [view, setView] = useState<"tactical" | "operations">("tactical");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [events, setEvents] = useState<EventItem[]>([]);
  const [range, setRange] = useState<(typeof ranges)[number]>("24h");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [jumpTick, setJumpTick] = useState<number | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const [nextOverview, nextEvents] = await Promise.all([
        getJson<Overview>("/api/v1/overview"),
        getJson<{ items: EventItem[] }>("/api/v1/events?limit=80"),
      ]);
      setOverview(nextOverview);
      setEvents(nextEvents.items);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  // 仅在运维视图且页面可见时轮询 overview/events；战术视图有自己的轮询，
  // 后台标签页暂停，避免双视图重复请求（约 7 请求/5 秒）。
  useEffect(() => {
    if (view !== "operations") return;
    void loadOverview();
    const timer = window.setInterval(() => { if (!document.hidden) void loadOverview(); }, 5000);
    const onVisible = () => { if (document.visibilityState === "visible") void loadOverview(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [loadOverview, view]);

  // 趋势图只在 range 变化时拉取一次，不再随 tick 轮询全量重拉。
  useEffect(() => {
    if (view !== "operations") return;
    let cancelled = false;
    getJson<{ points: HistoryPoint[] }>(`/api/v1/history?range=${range}`)
      .then((value) => { if (!cancelled) setHistory(value.points); })
      .catch(() => { if (!cancelled) setHistory([]); });
    return () => { cancelled = true; };
  }, [range, view]);

  const chartData = useMemo(() => history.map((point) => ({
    time: formatTime(point.observed_at ?? point.hour_start),
    resources: Math.round(point.resources ?? point.resources_avg ?? 0),
    capacity: point.capacity ?? point.capacity_max ?? 0,
    population: point.population ?? point.population_max ?? 0,
    workers: point.workers ?? point.workers_max ?? 0,
    military: (point.vanguards ?? point.vanguards_max ?? 0) + (point.rangers ?? point.rangers_max ?? 0),
    blocked: Math.round(
      (point.delivery_blocked ?? point.delivery_blocked_avg ?? 0) +
      (point.resource_blocked ?? point.resource_blocked_avg ?? 0),
    ),
  })), [history]);

  const observation = overview?.observation;
  const forceProgress = observation
    ? Math.min(100, Math.round(observation.population.total / Math.max(1, observation.strategy.force_target_population) * 100))
    : 0;
  const status = error ? "unavailable" : overview?.status ?? "unavailable";
  const statusLabel = status === "healthy" ? "在线" : status === "stale" ? "数据陈旧" : "不可用";

  return (
    <main>
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Crosshair size={20} /></div>
          <div><h1>Arena Hero</h1><span>战术指挥台</span></div>
        </div>
        <nav className="primary-tabs" aria-label="主视图">
          <button className={view === "tactical" ? "active" : ""} onClick={() => setView("tactical")}><Map size={15}/>战术地图</button>
          <button className={view === "operations" ? "active" : ""} onClick={() => setView("operations")}><Activity size={15}/>运维状态</button>
        </nav>
        <div className="topbar-actions">
          <div className={`status status-${status}`}><i />{statusLabel}</div>
          <span className="tick">Tick {observation?.tick?.toLocaleString() ?? "--"}</span>
          <button className="icon-button" onClick={() => void loadOverview()} title="立即刷新" aria-label="立即刷新">
            <RefreshCw size={17} className={loading ? "spin" : ""} />
          </button>
        </div>
      </header>

      <TacticalConsole active={view === "tactical"} jumpToTick={jumpTick ?? undefined} onJumpHandled={() => setJumpTick(null)} />
      <div style={{ display: view === "tactical" ? "none" : "contents" }}>
      {status !== "healthy" && (
        <div className={`notice notice-${status}`}>
          <CircleAlert size={17} />
          <span>{status === "stale" ? `最近数据距今 ${overview?.age_seconds ?? "--"} 秒` : "Dashboard 暂时无法取得 Agent 数据"}</span>
        </div>
      )}

      <section className="metrics-band" aria-label="实时状态">
        <Metric icon={Wheat} label="资源" value={`${observation?.economy.resources ?? "--"} / ${observation?.economy.capacity ?? "--"}`} detail={`运输中 ${observation?.economy.cargo ?? "--"}`} tone="green" />
        <Metric icon={Users} label="人口" value={observation?.population.total ?? "--"} detail={`承诺 ${observation?.strategy.committed_population ?? "--"} · 生产 ${observation?.strategy.production_ceiling ?? observation?.strategy.population_limit ?? "--"}`} tone="blue" />
        <Metric icon={Swords} label="战斗编制" value={`${observation?.population.vanguards ?? "--"}V · ${observation?.population.rangers ?? "--"}R`} detail={`${observation?.population.workers ?? "--"} Worker`} tone="amber" />
        <Metric icon={Shield} label="Core" value={`${observation?.core.hp ?? "--"} HP · ${observation?.core.shield ?? "--"} SH`} detail={observation?.core.state ?? "--"} tone={observation?.core.alive ? "green" : "red"} />
        <Metric icon={Box} label="阻塞" value={(observation?.economy.delivery_blocked ?? 0) + (observation?.economy.resource_blocked ?? 0)} detail={`交付 ${observation?.economy.delivery_blocked ?? "--"} · 资源 ${observation?.economy.resource_blocked ?? "--"}`} tone={(observation?.economy.delivery_blocked ?? 0) > 0 ? "amber" : "neutral"} />
      </section>

      <section className="dashboard-grid">
        <div className="main-column">
          <div className="section-heading">
            <div><h2>运行趋势</h2><span>{chartData.length} 个采样点</span></div>
            <div className="segments" role="group" aria-label="趋势时间范围">
              {ranges.map((item) => <button key={item} className={range === item ? "active" : ""} onClick={() => setRange(item)}>{item}</button>)}
            </div>
          </div>
          <div className="chart-panel">
            {chartData.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 12, right: 14, left: -14, bottom: 4 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" strokeDasharray="2 4" vertical={false} />
                  <XAxis dataKey="time" tick={{ fill: "#71717a", fontSize: 11 }} minTickGap={36} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="economy" tick={{ fill: "#71717a", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="units" orientation="right" tick={{ fill: "#71717a", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: "#0a0a0a", border: "1px solid rgba(255,255,255,0.14)", borderRadius: 10, boxShadow: "0 12px 30px rgba(0,0,0,0.34)" }} labelStyle={{ color: "#d4d4d8" }} />
                  <Legend iconType="plainline" wrapperStyle={{ fontSize: 12, color: "#71717a" }} />
                  <Line yAxisId="economy" type="monotone" dataKey="resources" name="资源" stroke="#76b889" dot={false} strokeWidth={2} />
                  <Line yAxisId="economy" type="monotone" dataKey="capacity" name="容量" stroke="#4591c5" dot={false} strokeWidth={1.5} />
                  <Line yAxisId="units" type="monotone" dataKey="population" name="人口" stroke="#e1b64e" dot={false} strokeWidth={2} />
                  <Line yAxisId="units" type="monotone" dataKey="blocked" name="阻塞" stroke="#c66370" dot={false} strokeWidth={1.5} />
                </LineChart>
              </ResponsiveContainer>
            ) : <div className="empty-state"><Activity size={24} /><span>等待趋势数据</span></div>}
          </div>

          <div className="section-heading events-heading">
            <div><h2>事件流</h2><span>已脱敏 · 最近 {events.length} 条 · 点击战斗事件可跳转回放</span></div>
          </div>
          <div className="event-table" role="table" aria-label="脱敏事件">
            {events.length ? events.map((event) => (
              <div className="event-row" role="row" key={event.event_id} onClick={() => { setJumpTick(event.tick); setView("tactical"); }}>
                <span className={`event-category category-${event.category.toLowerCase()}`}>{categoryNames[event.category] ?? event.category}</span>
                <div className="event-name"><b>{event.event_type}</b><span>{event.reason_code ?? "成功"}</span></div>
                <div className="event-values">{Object.entries(event.values).map(([key, value]) => <span key={key}>{key} {value}</span>)}</div>
                <span className="event-tick">#{event.tick.toLocaleString()}</span>
                <time>{formatTime(event.observed_at)}</time>
              </div>
            )) : <div className="empty-state compact"><Database size={22} /><span>暂无显著事件</span></div>}
          </div>
        </div>

        <aside className="side-column">
          <section className="side-section strategy-section">
            <div className="side-title"><Gauge size={17} /><h2>战略控制</h2></div>
            <div className="strategy-state">
              <div><span>姿态</span><strong>{observation?.strategy.posture ?? "--"}</strong></div>
              <ChevronRight size={17} />
              <div><span>阶段</span><strong>{observation?.strategy.phase ?? "--"}</strong></div>
            </div>
            <div className="source-line"><span>参数来源</span><b>{observation?.strategy.source ?? "--"}</b></div>
            <p className="reason">{reasonNames[observation?.strategy.reason ?? ""] ?? observation?.strategy.reason ?? "--"}</p>
            <div className="source-line"><span>迁移影子</span><b>{observation?.strategy.migration_shadow?.status ?? "NOT_EVALUATED"}</b></div>
            <div className="weights">
              <Weight label="经济" value={observation?.strategy.economy_weight ?? 0} color="#56b884" />
              <Weight label="领土" value={observation?.strategy.territory_weight ?? 0} color="#668fbe" />
              <Weight label="战斗" value={observation?.strategy.combat_weight ?? 0} color="#da6f68" />
              <Weight label="安全" value={observation?.strategy.safety_weight ?? 0} color="#e4ad5e" />
            </div>
            <div className="target-grid">
              <span><b>{observation?.strategy.worker_target ?? "--"}</b> Worker</span>
              <span><b>{observation?.strategy.vanguard_target ?? "--"}</b> Vanguard</span>
              <span><b>{observation?.strategy.ranger_target ?? "--"}</b> Ranger</span>
              <span><b>{observation?.strategy.scout_percent ?? "--"}%</b> Scout</span>
            </div>
          </section>

          <section className="side-section force-section">
            <div className="side-title"><Swords size={17} /><h2>军力阶段</h2><span className="small-status enabled">{forceProgress}%</span></div>
            <div className="force-stage-header">
              <div><span>当前阶段</span><strong>{forceStageNames[observation?.strategy.force_stage ?? ""] ?? "--"}</strong></div>
              <b>{observation?.population.total ?? "--"} / {observation?.strategy.force_target_population ?? "--"}</b>
            </div>
            <div className="force-progress"><i style={{ width: `${forceProgress}%` }} /></div>
            <div className="campaign-flags">
              <span className={observation?.battlefield.beacon_runner_active ? "active" : ""}>信标执行者</span>
              <span className={(observation?.battlefield.combat_patrol_units ?? 0) > 0 ? "active" : ""}>外围巡逻 {observation?.battlefield.combat_patrol_units ?? 0}</span>
            </div>
            <div className="force-rows">
              <div><span>Worker</span><b>{observation?.population.workers ?? "--"} / {observation?.strategy.force_target_workers ?? "--"}</b><em>缺 {observation?.strategy.force_worker_deficit ?? "--"}</em></div>
              <div><span>Vanguard</span><b>{observation?.population.vanguards ?? "--"} / {observation?.strategy.force_target_vanguards ?? "--"}</b><em>缺 {observation?.strategy.force_vanguard_deficit ?? "--"}</em></div>
              <div><span>Ranger</span><b>{observation?.population.rangers ?? "--"} / {observation?.strategy.force_target_rangers ?? "--"}</b><em>缺 {observation?.strategy.force_ranger_deficit ?? "--"}</em></div>
            </div>
          </section>

          <section className="side-section adviser-section">
            <div className="side-title"><BrainCircuit size={17} /><h2>模型顾问</h2><span className={`small-status ${observation?.adviser.enabled ? "enabled" : ""}`}>{observation?.adviser.enabled ? "已启用" : "本地模式"}</span></div>
            <div className="model-name"><Bot size={18} /><div><b>{observation?.adviser.model ?? "确定性规划器"}</b><span>{observation?.adviser.provider ?? "未连接外部模型"}</span></div></div>
            <dl className="detail-list">
              <div><dt>最近结果</dt><dd>{observation?.adviser.outcome ?? "--"}</dd></div>
              <div><dt>调用 / 应用 / 失败</dt><dd>{observation ? `${observation.adviser.requests} / ${observation.adviser.applied} / ${observation.adviser.failures}` : "--"}</dd></div>
              <div><dt>TTL 剩余</dt><dd>{observation?.adviser.ttl_remaining_ticks != null ? `${observation.adviser.ttl_remaining_ticks} Tick` : "--"}</dd></div>
              <div><dt>安全覆盖</dt><dd>{observation?.adviser.overridden ? "已覆盖模型" : "未触发"}</dd></div>
            </dl>
          </section>

          <section className="side-section system-section">
            <div className="side-title"><HardDrive size={17} /><h2>部署状态</h2></div>
            <dl className="detail-list">
              <div><dt>Agent 服务</dt><dd>{overview?.deployment.service_active === true ? "active" : overview?.deployment.service_active === false ? "inactive" : "unknown"}</dd></div>
              <div><dt>重启次数</dt><dd>{overview?.deployment.restarts ?? "--"}</dd></div>
              <div><dt>Release</dt><dd title={overview?.deployment.release ?? ""}>{overview?.deployment.release ?? "--"}</dd></div>
              <div><dt>Commit</dt><dd>{shortCommit(overview?.deployment.commit)}</dd></div>
              <div><dt>数据时间</dt><dd>{formatTime(observation?.generated_at)}</dd></div>
            </dl>
          </section>
        </aside>
      </section>
      </div>
    </main>
  );
}

export default App;
