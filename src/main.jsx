import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, Bell, Boxes, ChevronDown, Clock3, Cpu, Database,
  Gauge, GitBranch, LayoutDashboard, Menu, MoreHorizontal, Network,
  Pause, Play, RotateCcw, Search, Send, Server, Settings, Terminal,
  Zap
} from 'lucide-react'
import './styles.css'

const COLORS = ['#65e0ad', '#68a8ff', '#a78bfa']
const INITIAL = [
  { id: 'A', name: 'GPU Server A', gpu: 72, memory: 68, queue: 3, requests: 125, latency: 42, tokens: 492, status: 'Healthy' },
  { id: 'B', name: 'GPU Server B', gpu: 65, memory: 61, queue: 1, requests: 118, latency: 38, tokens: 468, status: 'Healthy' },
  { id: 'C', name: 'GPU Server C', gpu: 81, memory: 76, queue: 5, requests: 132, latency: 51, tokens: 505, status: 'Healthy' },
]

const clamp = (v, min, max) => Math.min(max, Math.max(min, v))
const jitter = (v, amount, min, max) => clamp(v + Math.round((Math.random() - .5) * amount), min, max)

function Sparkline({ values, color = '#65e0ad', height = 56 }) {
  const max = Math.max(...values, 1)
  const min = Math.min(...values)
  const spread = Math.max(max - min, 1)
  const points = values.map((v, i) => `${(i / (values.length - 1)) * 100},${height - 5 - ((v - min) / spread) * (height - 13)}`).join(' ')
  return (
    <svg className="sparkline" viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <defs><linearGradient id={`fill-${color.slice(1)}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity=".22"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>
      <polygon points={`0,${height} ${points} 100,${height}`} fill={`url(#fill-${color.slice(1)})`} />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.7" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}

function MiniMetric({ icon: Icon, label, value, sub, color = '#65e0ad', history }) {
  return <article className="metric-card">
    <div className="metric-top"><span className="icon-box"><Icon size={17}/></span><span>{label}</span><MoreHorizontal size={18}/></div>
    <div className="metric-value">{value}</div>
    <div className="metric-sub">{sub}</div>
    <div className="metric-spark"><Sparkline values={history} color={color}/></div>
  </article>
}

function Ring({ value, color }) {
  return <div className="ring" style={{ '--value': `${value * 3.6}deg`, '--ring': color }}><div><strong>{value}%</strong><span>GPU</span></div></div>
}

function ServerCard({ server, selected, onClick, color }) {
  return <button className={`server-card ${selected ? 'selected' : ''}`} onClick={onClick} style={{'--accent': color}}>
    <div className="server-head">
      <span className="server-icon"><Server size={19}/></span>
      <span><strong>{server.name}</strong><small><i/> {server.status}</small></span>
      <MoreHorizontal size={18}/>
    </div>
    <div className="server-body">
      <Ring value={server.gpu} color={color}/>
      <div className="server-stats">
        <div><span>VRAM</span><b>{server.memory}%</b></div>
        <div className="bar"><i style={{width: `${server.memory}%`}}/></div>
        <div><span>Queue</span><b>{server.queue}</b></div>
        <div><span>Latency</span><b>{server.latency} ms</b></div>
      </div>
    </div>
    <div className="server-foot"><span><Send size={13}/> {server.requests} requests</span><span><Zap size={13}/> {server.tokens} tok/s</span></div>
  </button>
}

function FlowMap({ servers, tick, paused }) {
  const target = tick % 3
  return <section className="panel flow-panel">
    <div className="panel-head"><div><h2>Live request flow</h2><p>Round-robin · real-time routing</p></div><span className="live-pill"><i/> LIVE</span></div>
    <div className="flow-map">
      <div className="flow-source"><span><Send size={17}/></span><div><strong>User requests</strong><small>HTTP / JSON</small></div></div>
      <div className="flow-line vertical"><i className={!paused ? 'packet down' : ''}/></div>
      <div className="balancer"><span><GitBranch size={20}/></span><div><strong>Load Balancer</strong><small>Round Robin</small></div><b>{servers.reduce((a,s)=>a+s.queue,0)} queued</b></div>
      <div className="branches">
        {servers.map((s, i) => <div className="branch" key={s.id}>
          <div className="branch-line"><i className={!paused && i === target ? 'packet active' : 'packet'}/></div>
          <div className={`node ${i === target && !paused ? 'receiving' : ''}`}><Server size={18}/><span><strong>Server {s.id}</strong><small>{s.gpu}% GPU</small></span><em>{s.queue}</em></div>
        </div>)}
      </div>
      <div className="flow-note"><Activity size={14}/> Requests are being distributed evenly across 3 healthy nodes</div>
    </div>
  </section>
}

function TrafficChart({ series }) {
  const max = 160
  const toPoints = arr => arr.map((v,i)=>`${(i/(arr.length-1))*100},${102-(v/max)*90}`).join(' ')
  return <section className="panel chart-panel">
    <div className="panel-head"><div><h2>Request distribution</h2><p>Requests per second, by server</p></div><div className="legend">{['A','B','C'].map((x,i)=><span key={x}><i style={{background:COLORS[i]}}/>Server {x}</span>)}</div></div>
    <div className="chart-wrap">
      <div className="y-labels"><span>160</span><span>120</span><span>80</span><span>40</span><span>0</span></div>
      <svg viewBox="0 0 100 110" preserveAspectRatio="none">
        {[12,34.5,57,79.5,102].map(y=><line key={y} x1="0" y1={y} x2="100" y2={y} stroke="#ffffff" strokeOpacity=".07" strokeWidth=".4"/>) }
        {series.map((arr,i)=><polyline key={i} points={toPoints(arr)} fill="none" stroke={COLORS[i]} strokeWidth="1.6" vectorEffect="non-scaling-stroke"/>) }
      </svg>
      <div className="x-labels"><span>60s ago</span><span>45s</span><span>30s</span><span>15s</span><span>now</span></div>
    </div>
  </section>
}

function App() {
  const [servers, setServers] = useState(INITIAL)
  const [paused, setPaused] = useState(false)
  const [tick, setTick] = useState(0)
  const [selected, setSelected] = useState('A')
  const [series, setSeries] = useState([
    [76,82,78,90,94,88,103,98,110,115,105,119,124,121,125],
    [64,70,74,69,81,86,84,96,92,102,108,105,114,111,118],
    [83,79,91,95,89,99,104,110,106,120,115,127,122,136,132],
  ])
  const [history, setHistory] = useState({rps:[321,330,348,340,358,365,371,369,375], latency:[46,44,48,43,41,45,42,44,43], tokens:[1230,1270,1320,1300,1380,1420,1410,1440,1465], gpu:[62,65,67,68,70,71,74,72,73]})

  useEffect(() => {
    if (paused) return
    const timer = setInterval(() => {
      setTick(t=>t+1)
      setServers(prev=>prev.map(s=>({...s, gpu:jitter(s.gpu,9,48,91), memory:jitter(s.memory,5,51,86), queue:jitter(s.queue,3,0,8), requests:s.requests+jitter(4,4,1,7), latency:jitter(s.latency,9,30,68), tokens:jitter(s.tokens,30,410,560)})))
      setSeries(prev=>prev.map((arr,i)=>[...arr.slice(1), jitter(arr.at(-1),20,75+i*5,145)]))
      setHistory(prev=>Object.fromEntries(Object.entries(prev).map(([k,arr])=>[k,[...arr.slice(1),jitter(arr.at(-1), k==='tokens'?100:k==='rps'?35:8, k==='tokens'?1100:k==='rps'?280:35, k==='tokens'?1700:k==='rps'?450:95)]])))
    }, 1700)
    return ()=>clearInterval(timer)
  }, [paused])

  const totals = useMemo(()=>({
    rps: servers.reduce((a,s)=>a+s.requests,0),
    latency: Math.round(servers.reduce((a,s)=>a+s.latency,0)/servers.length),
    tokens: servers.reduce((a,s)=>a+s.tokens,0).toLocaleString(),
    gpu: Math.round(servers.reduce((a,s)=>a+s.gpu,0)/servers.length),
  }),[servers])

  const reset = () => { setServers(INITIAL); setTick(0) }
  return <div className="app-shell">
    <aside>
      <div className="brand"><span><Network size={22}/></span><div>AICAP<small>AI INFRA OPS</small></div></div>
      <nav>
        <p>WORKSPACE</p>
        <a className="active"><LayoutDashboard size={18}/> Overview</a>
        <a><Activity size={18}/> Live traffic <em>LIVE</em></a>
        <a><Server size={18}/> GPU servers <b>3</b></a>
        <a><GitBranch size={18}/> Load balancer</a>
        <p>OBSERVABILITY</p>
        <a><Gauge size={18}/> Performance</a>
        <a><Terminal size={18}/> Request logs</a>
        <a><Database size={18}/> Checkpoints</a>
      </nav>
      <div className="side-bottom"><a><Settings size={18}/> Settings</a><div className="system-health"><span><i/> All systems operational</span><small>Updated just now</small></div></div>
    </aside>
    <main>
      <header>
        <button className="mobile-menu"><Menu/></button>
        <div className="title"><h1>Infrastructure overview</h1><p>Real-time LLM serving & load balancing</p></div>
        <div className="header-actions"><div className="search"><Search size={16}/><span>Search metrics...</span><kbd>⌘ K</kbd></div><button className="icon-btn"><Bell size={18}/><i/></button><div className="env"><span/>Production<ChevronDown size={15}/></div><div className="avatar">AK</div></div>
      </header>
      <div className="content">
        <div className="status-row"><div><span className="status-dot"/>SYSTEM ONLINE <small>Last synced 2s ago</small></div><div><button onClick={reset}><RotateCcw size={15}/> Reset</button><button className="control" onClick={()=>setPaused(p=>!p)}>{paused?<Play size={15}/>:<Pause size={15}/>} {paused?'Resume':'Pause'} stream</button></div></div>
        <section className="metrics-grid">
          <MiniMetric icon={Activity} label="Total requests" value={totals.rps} sub="+12.4% from last minute" history={history.rps}/>
          <MiniMetric icon={Clock3} label="Avg. latency" value={`${totals.latency} ms`} sub="−8.2% from last minute" color="#68a8ff" history={history.latency}/>
          <MiniMetric icon={Zap} label="Token throughput" value={totals.tokens} sub="tokens per second" color="#a78bfa" history={history.tokens}/>
          <MiniMetric icon={Cpu} label="Avg. GPU usage" value={`${totals.gpu}%`} sub="Across 3 active servers" color="#f5b45d" history={history.gpu}/>
        </section>
        <section className="overview-grid"><FlowMap servers={servers} tick={tick} paused={paused}/><TrafficChart series={series}/></section>
        <div className="section-title"><div><h2>GPU server status</h2><p>Select a server to inspect its current workload</p></div><button>View all metrics <span>→</span></button></div>
        <section className="server-grid">{servers.map((s,i)=><ServerCard key={s.id} server={s} color={COLORS[i]} selected={selected===s.id} onClick={()=>setSelected(s.id)}/>)}</section>
      </div>
    </main>
  </div>
}

createRoot(document.getElementById('root')).render(<App/>)
