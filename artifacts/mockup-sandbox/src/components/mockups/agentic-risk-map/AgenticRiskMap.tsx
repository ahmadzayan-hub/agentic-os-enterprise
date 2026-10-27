import { useMemo, useState } from "react";
import {
  Activity,
  ArrowUpRight,
  Bell,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  Command,
  Crosshair,
  Database,
  Filter,
  Layers3,
  MapPin,
  MoreHorizontal,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";

type Zone = {
  id: string;
  name: string;
  sub: string;
  score: number;
  status: "critical" | "watch" | "stable";
  incidents: number;
  x: number;
  y: number;
  w: number;
  h: number;
  accent: string;
  detail: string;
  owner: string;
  ago: string;
};

const zones: Zone[] = [
  {
    id: "payments",
    name: "Payments",
    sub: "prod / us-east-1",
    score: 87,
    status: "critical",
    incidents: 4,
    x: 8,
    y: 13,
    w: 37,
    h: 34,
    accent: "#e4573d",
    detail: "Agent retries are crossing the approved spend boundary on card authorization.",
    owner: "Mira Chen",
    ago: "8 min ago",
  },
  {
    id: "identity",
    name: "Identity",
    sub: "prod / global",
    score: 63,
    status: "watch",
    incidents: 2,
    x: 50,
    y: 8,
    w: 42,
    h: 28,
    accent: "#d09235",
    detail: "Token refresh latency is rising for service accounts with elevated scope.",
    owner: "Jon Bell",
    ago: "19 min ago",
  },
  {
    id: "fulfillment",
    name: "Fulfillment",
    sub: "prod / eu-west-1",
    score: 38,
    status: "stable",
    incidents: 1,
    x: 53,
    y: 43,
    w: 35,
    h: 37,
    accent: "#4c8b72",
    detail: "No active policy violations. Queue age is inside the expected operating range.",
    owner: "Ari Okafor",
    ago: "42 min ago",
  },
  {
    id: "intelligence",
    name: "Intelligence",
    sub: "internal / shared",
    score: 51,
    status: "watch",
    incidents: 3,
    x: 10,
    y: 56,
    w: 34,
    h: 28,
    accent: "#d09235",
    detail: "A new model route is emitting an unusual volume of unclassified tool calls.",
    owner: "Leila Park",
    ago: "1 hr ago",
  },
];

const statusLabel = { critical: "high risk", watch: "watch", stable: "stable" };

export function AgenticRiskMap() {
  const [selectedId, setSelectedId] = useState("payments");
  const [resolved, setResolved] = useState<string[]>([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const [quiet, setQuiet] = useState(false);
  const selected = zones.find((zone) => zone.id === selectedId) ?? zones[0];
  const openIncidents = useMemo(
    () => zones.reduce((sum, zone) => sum + (resolved.includes(zone.id) ? 0 : zone.incidents), 0),
    [resolved],
  );

  function resolveSelected() {
    setResolved((current) => (current.includes(selected.id) ? current : [...current, selected.id]));
  }

  return (
    <main className="risk-app">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Space+Grotesk:wght@400;500;600;700&display=swap');
        .risk-app{--ink:#182238;--muted:#738097;--line:#dfe4ec;--paper:#f6f7f4;--panel:#fbfcfa;--blue:#4058cb;min-height:100dvh;background:var(--paper);color:var(--ink);font-family:'Space Grotesk',ui-sans-serif,sans-serif;display:flex;overflow:hidden}
        .risk-sidebar{width:226px;background:#202b48;color:#c3cbe0;padding:25px 15px 20px;display:flex;flex-direction:column;flex-shrink:0}
        .risk-mark{display:flex;align-items:center;gap:10px;color:#f4f6ff;font-size:15px;font-weight:600;letter-spacing:-.02em;padding:0 11px 31px}
        .mark-box{height:25px;width:25px;border-radius:8px;background:#e6b35b;display:grid;place-items:center;color:#202b48}
        .mark-box svg{stroke-width:3}
        .side-caption{text-transform:uppercase;letter-spacing:.13em;font-size:9px;color:#7583a3;padding:0 11px 10px;font-weight:600}
        .side-link{display:flex;align-items:center;gap:11px;border-radius:8px;padding:10px 11px;margin:2px 0;color:#aeb9d2;font-size:12px;cursor:pointer;border:0;background:transparent;width:100%;text-align:left}
        .side-link:hover,.side-link.active{background:#344263;color:#fff}.side-link.active{box-shadow:inset 3px 0 #e6b35b}
        .side-link svg{width:16px;height:16px;stroke-width:1.8}.side-count{margin-left:auto;color:#e8b75d;font:11px 'DM Mono',monospace}
        .side-bottom{margin-top:auto;border-top:1px solid #35415f;padding:15px 10px 0;color:#aeb9d2;font-size:11px}
        .profile{display:flex;align-items:center;gap:9px;margin-top:12px}.avatar{width:27px;height:27px;border-radius:50%;background:#c7d0ed;color:#273352;display:grid;place-items:center;font-size:10px;font-weight:700}
        .risk-body{flex:1;min-width:0;padding:25px 29px 29px;overflow:auto}
        .topline{display:flex;align-items:center;justify-content:space-between;gap:20px}.crumb{display:flex;align-items:center;gap:9px;color:var(--muted);font-size:11px}.crumb strong{color:var(--ink);font-weight:600}.tiny-dot{width:5px;height:5px;border-radius:50%;background:#4c8b72}
        .header-actions{display:flex;align-items:center;gap:10px}.command{font:11px 'DM Mono',monospace;color:#7f8ba1;border:1px solid var(--line);background:#fff;border-radius:6px;padding:7px 9px;display:flex;gap:8px;align-items:center}
        .header-icon{border:0;background:transparent;color:#708099;padding:6px;cursor:pointer}.header-icon:hover{color:var(--ink)}
        .heading{display:flex;justify-content:space-between;align-items:flex-end;margin:39px 0 21px}.eyebrow{text-transform:uppercase;color:#8a94a7;font:10px 'DM Mono',monospace;letter-spacing:.12em;margin-bottom:8px}.heading h1{font-size:28px;line-height:1.05;margin:0;letter-spacing:-.045em;font-weight:600}.heading p{color:var(--muted);font-size:12px;margin:10px 0 0}
        .range{font:11px 'DM Mono',monospace;color:#5d6b84;display:flex;align-items:center;gap:6px;border:1px solid var(--line);background:#fff;border-radius:6px;padding:8px 10px;cursor:pointer}
        .metrics{display:grid;grid-template-columns:1.28fr 1fr 1fr 1fr;gap:11px;margin-bottom:20px}.metric{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:14px 16px;min-height:80px}.metric:first-child{background:#293654;color:#f7f7f4;border-color:#293654}.metric-label{font-size:10px;color:#8c98aa;display:flex;justify-content:space-between;align-items:center}.metric:first-child .metric-label{color:#aeb9d2}.metric-value{font:500 23px 'DM Mono',monospace;letter-spacing:-.06em;margin-top:11px}.metric-value small{font:11px 'Space Grotesk';letter-spacing:0;color:#e6b35b;margin-left:7px}.metric:not(:first-child) .metric-value small{color:#4c8b72}
        .workspace{display:grid;grid-template-columns:minmax(490px,1fr) 294px;gap:13px;min-height:535px}.map-card,.drawer{background:var(--panel);border:1px solid var(--line);border-radius:10px}.map-card{padding:17px;position:relative;overflow:hidden}.map-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:15px}.map-title{font-size:12px;font-weight:600}.map-sub{font:10px 'DM Mono',monospace;color:#8994a6;margin-top:4px}.map-controls{display:flex;gap:4px}.map-control{height:27px;min-width:29px;border:1px solid var(--line);background:#fff;color:#76839a;border-radius:5px;display:grid;place-items:center;cursor:pointer}.map-control:hover{background:#eef0f8;color:var(--blue)}
        .map-canvas{height:435px;border:1px solid #e5e8ed;border-radius:7px;background-color:#f0f2ee;background-image:linear-gradient(#e0e5df 1px,transparent 1px),linear-gradient(90deg,#e0e5df 1px,transparent 1px);background-size:36px 36px;position:relative;overflow:hidden}
        .map-canvas:after{content:"";position:absolute;inset:0;background:linear-gradient(135deg,transparent 47%,rgba(255,255,255,.45) 47%,rgba(255,255,255,.45) 48%,transparent 48%);opacity:.55;pointer-events:none}
        .zone{position:absolute;border:1px solid;cursor:pointer;transition:transform .18s ease,opacity .18s ease;overflow:hidden;text-align:left;padding:13px 14px;border-radius:7px}.zone:hover{transform:translateY(-2px)}.zone.selected{box-shadow:0 0 0 2px #fff,0 0 0 4px var(--blue);z-index:4}.zone.critical{background:rgba(228,87,61,.17);border-color:rgba(207,72,53,.5)}.zone.watch{background:rgba(208,146,53,.16);border-color:rgba(190,129,39,.45)}.zone.stable{background:rgba(76,139,114,.13);border-color:rgba(59,123,94,.4)}.zone.done{opacity:.42;filter:saturate(.4)}
        .zone-name{font-size:13px;font-weight:600;display:block}.zone-sub{font:9px 'DM Mono',monospace;color:#68768b;display:block;margin-top:4px}.zone-score{position:absolute;right:12px;top:12px;font:500 15px 'DM Mono',monospace}.zone-score span{display:block;font:8px 'Space Grotesk';text-transform:uppercase;letter-spacing:.1em;color:#778297;text-align:right;margin-top:1px}.zone-foot{position:absolute;bottom:11px;left:14px;right:14px;display:flex;justify-content:space-between;font:9px 'DM Mono',monospace;color:#66748b}
        .map-legend{position:absolute;bottom:15px;left:17px;display:flex;gap:12px;background:rgba(251,252,250,.9);border:1px solid var(--line);border-radius:5px;padding:7px 9px;font-size:9px;color:#6e7b91}.legend-item{display:flex;align-items:center;gap:5px}.legend-dot{height:7px;width:7px;border-radius:2px}.map-stamp{position:absolute;right:18px;bottom:17px;font:9px 'DM Mono',monospace;color:#94a0ab}
        .drawer{padding:18px 17px;display:flex;flex-direction:column}.drawer-head{display:flex;align-items:center;justify-content:space-between;padding-bottom:16px;border-bottom:1px solid var(--line)}.drawer-label{font:10px 'DM Mono',monospace;text-transform:uppercase;letter-spacing:.09em;color:#8490a4}.drawer-close{border:0;background:transparent;color:#8793a5;cursor:pointer}.incident-list{display:flex;flex-direction:column;gap:0}.incident{padding:15px 0;border-bottom:1px solid #eaedf0;cursor:pointer}.incident:last-child{border:0}.incident:hover .incident-title{text-decoration:underline}.incident-line{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.pill{font:9px 'DM Mono',monospace;text-transform:uppercase;letter-spacing:.05em;padding:4px 6px;border-radius:4px}.pill.critical{background:#fae2dd;color:#bc4938}.pill.watch{background:#f9ecd4;color:#a46e1d}.pill.stable{background:#e0eee7;color:#39765d}.incident-age{font:9px 'DM Mono',monospace;color:#a0a9b5}.incident-title{font-size:12px;line-height:1.4;font-weight:500;margin:0;color:#25304a}.incident-meta{display:flex;align-items:center;gap:7px;color:#8792a4;font-size:10px;margin-top:8px}.meta-dot{height:3px;width:3px;background:#bac1cb;border-radius:50%}.drawer-summary{margin-top:auto;background:#f1f3f6;border-radius:7px;padding:12px}.drawer-summary p{font-size:10px;line-height:1.45;color:#6c7890;margin:8px 0 0}.summary-top{display:flex;align-items:center;gap:6px;font-size:10px;font-weight:600}.summary-top svg{color:#4058cb}.resolve{width:100%;border:0;border-radius:6px;background:#4058cb;color:#fff;padding:10px;margin-top:11px;font:600 11px 'Space Grotesk';cursor:pointer}.resolve:hover{background:#3249b4}.resolve:disabled{background:#8d98ad;cursor:default}
        @media(max-width:900px){.risk-sidebar{width:70px;padding:20px 9px}.risk-mark{padding:0 0 30px;justify-content:center}.risk-mark span,.side-caption,.side-link span,.side-count,.side-bottom{display:none}.side-link{justify-content:center;padding:11px}.risk-body{padding:22px 18px}.workspace{grid-template-columns:1fr}.drawer{min-height:350px}.metrics{grid-template-columns:repeat(2,1fr)}}
        @media(max-width:620px){.risk-sidebar{display:none}.risk-body{padding:18px 13px}.heading{margin-top:28px;align-items:flex-start;gap:15px;flex-direction:column}.metrics{grid-template-columns:1fr 1fr;gap:8px}.metric{padding:12px 11px}.metric-value{font-size:18px}.workspace{display:block}.drawer{margin-top:12px}.map-card{padding:11px}.map-canvas{height:390px}.zone{padding:9px}.zone-foot{left:9px;right:9px}.zone-name{font-size:11px}.zone-sub{font-size:8px}.zone-score{right:8px;top:8px;font-size:12px}.header-actions .command{display:none}}
      `}</style>
      <aside className="risk-sidebar">
        <div className="risk-mark"><span className="mark-box"><Crosshair size={15} /></span><span>agentic / ops</span></div>
        <div className="side-caption">Workspace</div>
        <button className="side-link active"><MapPin /><span>Risk map</span><b className="side-count">{openIncidents}</b></button>
        <button className="side-link"><Activity /><span>Live signals</span></button>
        <button className="side-link"><Database /><span>Run history</span></button>
        <button className="side-link"><ShieldAlert /><span>Policies</span></button>
        <div className="side-caption" style={{ marginTop: 28 }}>Observe</div>
        <button className="side-link"><Layers3 /><span>All environments</span></button>
        <button className="side-link"><SlidersHorizontal /><span>Preferences</span></button>
        <div className="side-bottom"><div>Northstar operations</div><div className="profile"><span className="avatar">MC</span><span>Mira Chen<br /><small style={{ color: "#7481a1" }}>operator</small></span></div></div>
      </aside>
      <section className="risk-body">
        <div className="topline">
          <div className="crumb"><span className="tiny-dot" />Northstar / <strong>Operations</strong> / Risk map</div>
          <div className="header-actions"><button className="command"><Command size={12} /> K <span style={{ opacity: .45 }}>Search anything</span></button><button className="header-icon" onClick={() => setQuiet(!quiet)} title="Toggle notifications"><Bell size={17} fill={quiet ? "currentColor" : "none"} /></button><button className="header-icon"><MoreHorizontal size={18} /></button></div>
        </div>
        <div className="heading"><div><div className="eyebrow">Operational field / live view</div><h1>Risk map</h1><p>Signals arranged by system surface, severity, and recency.</p></div><button className="range"><Clock3 size={13} /> Last 24 hours <ChevronDown size={13} /></button></div>
        <div className="metrics">
          <div className="metric"><div className="metric-label">OPEN SIGNALS <ArrowUpRight size={14} /></div><div className="metric-value">{openIncidents}<small>− 3 since 09:00</small></div></div>
          <div className="metric"><div className="metric-label">SYSTEM HEALTH <CircleDot size={13} /></div><div className="metric-value">82.6<small>/ 100</small></div></div>
          <div className="metric"><div className="metric-label">RESOLUTION RATE <Check size={13} /></div><div className="metric-value">94.2<small>%</small></div></div>
          <div className="metric"><div className="metric-label">LAST UPDATED <Activity size={13} /></div><div className="metric-value" style={{ fontSize: 18 }}>10:42<small>UTC</small></div></div>
        </div>
        <div className="workspace">
          <section className="map-card">
            <div className="map-top"><div><div className="map-title">System surface</div><div className="map-sub">SELECT A ZONE TO INSPECT ACTIVE SIGNALS</div></div><div className="map-controls"><button className="map-control" onClick={() => setFilterOpen(!filterOpen)}><Filter size={14} /></button><button className="map-control"><Layers3 size={14} /></button></div></div>
            <div className="map-canvas">
              {zones.map((zone) => <button key={zone.id} className={`zone ${zone.status} ${zone.id === selectedId ? "selected" : ""} ${resolved.includes(zone.id) ? "done" : ""}`} style={{ left: `${zone.x}%`, top: `${zone.y}%`, width: `${zone.w}%`, height: `${zone.h}%`, ["--zone" as string]: zone.accent }} onClick={() => setSelectedId(zone.id)}><span className="zone-name">{zone.name}</span><span className="zone-sub">{zone.sub}</span><span className="zone-score" style={{ color: zone.accent }}>{zone.score}<span>risk score</span></span><span className="zone-foot"><span>{resolved.includes(zone.id) ? "resolved" : `${zone.incidents} active`}</span><span>{statusLabel[zone.status]}</span></span></button>)}
              <div className="map-legend"><span className="legend-item"><i className="legend-dot" style={{ background: "#e4573d" }} />High risk</span><span className="legend-item"><i className="legend-dot" style={{ background: "#d09235" }} />Watch</span><span className="legend-item"><i className="legend-dot" style={{ background: "#4c8b72" }} />Stable</span></div><span className="map-stamp">NORTHSTAR / 10:42:08 UTC</span>
            </div>
            {filterOpen && <div style={{ position: "absolute", right: 17, top: 61, background: "#fff", border: "1px solid var(--line)", borderRadius: 6, padding: 11, width: 145, boxShadow: "0 8px 22px #24304c14", zIndex: 8 }}><div style={{ fontSize: 10, fontWeight: 600, marginBottom: 8 }}>Show on map</div><label style={{ display: "flex", gap: 7, fontSize: 10, color: "#68768b", margin: "7px 0" }}><input type="checkbox" defaultChecked /> Active signals</label><label style={{ display: "flex", gap: 7, fontSize: 10, color: "#68768b", margin: "7px 0" }}><input type="checkbox" defaultChecked /> Resolved today</label></div>}
          </section>
          <aside className="drawer">
            <div className="drawer-head"><div><div className="drawer-label">Incident drawer</div><div style={{ fontSize: 11, color: "#7d899d", marginTop: 4 }}>{selected.name} · {selected.incidents} signals</div></div><button className="drawer-close" onClick={() => setSelectedId("")}><X size={16} /></button></div>
            <div className="incident-list"><div className="incident" onClick={() => setSelectedId(selected.id)}><div className="incident-line"><span className={`pill ${selected.status}`}>{statusLabel[selected.status]}</span><span className="incident-age">{selected.ago}</span></div><p className="incident-title">{selected.detail}</p><div className="incident-meta"><span>{selected.owner}</span><i className="meta-dot" /><span>policy boundary</span></div></div><div className="incident"><div className="incident-line"><span className="pill watch">context</span><span className="incident-age">31 min ago</span></div><p className="incident-title">Volume is {selected.status === "critical" ? "2.4×" : "1.3×"} above its seven-day baseline.</p><div className="incident-meta"><span>agent observation</span><i className="meta-dot" /><span>auto-triaged</span></div></div></div>
            <div className="drawer-summary"><div className="summary-top"><Sparkles size={13} /> Agent summary</div><p>One decision is waiting on you. Review the highlighted signal before the next scheduled run.</p><button className="resolve" onClick={resolveSelected} disabled={resolved.includes(selected.id)}>{resolved.includes(selected.id) ? "Signal resolved" : "Mark as resolved"}</button></div>
          </aside>
        </div>
      </section>
    </main>
  );
}

export default AgenticRiskMap;