import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Activity, Clock3, Database, GitBranch, LayoutDashboard, Menu, Send, Server, Terminal, Zap } from 'lucide-react'
import RequestParticle from './RequestParticle.jsx'
import { FlowMessages, NarrationControls } from './FlowNarration.jsx'
import RequestStory from './RequestStory.jsx'

const COLORS = ['#1aaa79', '#478bcb', '#8d71c3']
const n = value => Number(value).toLocaleString()

export function DashboardShell({ onNavigate, children }) {
  const [menu, setMenu] = useState(false)
  const [active, setActive] = useState(() => window.location.hash || '#overview')
  useEffect(() => {
    const update = () => setActive(window.location.hash || '#overview')
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return <div className={`app-shell integrated-dashboard ${menu ? 'menu-open' : ''}`}>
    <aside>
      <nav aria-label="대시보드 메뉴"><p>WORKSPACE</p>
        {[[LayoutDashboard,'대시보드','#overview'],[Activity,'실시간 요청 흐름','#live-flow'],[Server,'GPU 서버 상태','#servers'],[Clock3,'처리 완료 기록','#completions'],[Database,'연구·데이터 설명','#research-guide'],[Database,'고려 요소 현황','#factors'],[GitBranch,'서버 선택 근거','#routing'],[Terminal,'최근 분배 기록','#request-logs']].map(([Icon,label,href]) => <a href={href} key={href} className={active === href ? 'active' : ''} aria-current={active === href ? 'location' : undefined} onClick={event => {
          if(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
          setMenu(false)
          onNavigate(href)
        }}><Icon size={18}/>{label}</a>)}
      </nav>
      <nav className="analysis-nav" aria-label="분석 메뉴">
        <a href="#analysis" className={active === '#analysis' ? 'active' : ''} aria-current={active === '#analysis' ? 'page' : undefined} onClick={() => {setMenu(false); onNavigate('#analysis')}}><Activity size={18}/>상세 분석</a>
      </nav>
    </aside>
    <main className="simulation-app" id="overview"><header>
      <button className="mobile-menu" aria-label="메뉴 열기" aria-expanded={menu} onClick={() => setMenu(x => !x)}><Menu/></button>
      <div className="title"><h1>로드밸런서 대시보드</h1></div>
    </header>{children}</main>
  </div>
}

export function LegacyLiveFlowMap({ state }) {
  const [storyStep, setStoryStep] = useState(0)
  const root = useRef(null), svg = useRef(null), source = useRef(null), balancer = useRef(null)
  const viewport = useRef(null)
  const nodes = useRef([])
  const [geometry, setGeometry] = useState(null)
  useLayoutEffect(() => {
    const element = viewport.current
    const center = () => { element.scrollLeft = Math.max(0, (element.scrollWidth - element.clientWidth) / 2) }
    center()
    const observer = new ResizeObserver(center)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  useLayoutEffect(() => {
    const measure = () => {
      if (!root.current || !source.current || !balancer.current || nodes.current.length !== 3) return
      const frame = root.current.getBoundingClientRect()
      const point = element => {
        const box = element.getBoundingClientRect()
        return {x:box.left - frame.left + box.width / 2, top:box.top - frame.top, bottom:box.bottom - frame.top}
      }
      const start = point(source.current), lb = point(balancer.current), targets = nodes.current.map(point)
      const branchY = Math.min(...targets.map(p => p.top)) - 40
      setGeometry({width:frame.width, height:frame.height, paths:targets.map(p => `M${start.x} ${start.bottom} L${lb.x} ${lb.top} L${lb.x} ${lb.bottom} L${lb.x} ${branchY} L${p.x} ${branchY} L${p.x} ${p.top}`)})
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(root.current)
    for (const node of nodes.current) if (node) observer.observe(node)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (state.running) svg.current?.unpauseAnimations()
    else svg.current?.pauseAnimations()
  }, [state.running, state.processed, geometry])
  const latest = state.decisions.at(-1)
  const queue = state.servers.reduce((sum,s) => sum + s.queue, 0)
  return <section className="panel flow-panel integrated-flow" id="live-flow">
    <div className="panel-head flow-title"><div><span className="eyebrow">PRIMARY VIEW</span><h2>Live request flow</h2><p>Mooncake 실제 요청 → 예상 완료 비용 비교 → GPU 서버 선택</p></div><div className="flow-head-stats"><b>{n(state.processed)}</b><small>ARRIVED REQUESTS</small><span className="live-pill"><i/>{state.running ? 'REPLAY LIVE' : state.finished ? 'COMPLETED' : 'PAUSED'}</span></div></div>
    <p className="scroll-hint">흐름도는 좌우로 이동할 수 있습니다.</p>
    <NarrationControls step={storyStep} setStep={setStoryStep}/>
    <div className="narration-compact"><FlowMessages state={state} step={storyStep}/></div>
    <div ref={viewport} className="flow-viewport" role="region" aria-label="서버별 요청 흐름도 · 좁은 화면에서는 좌우로 이동" tabIndex={0}>
    <div className={`flow-map narrated-map story-step-${storyStep} ${state.running ? 'is-running' : 'replay-paused'}`} ref={root}>
      <FlowMessages state={state} step={storyStep}/>
      {geometry && <svg ref={svg} className="integrated-routes" viewBox={`0 0 ${geometry.width} ${geometry.height}`} aria-label="서버별 요청 이동 경로">
        {geometry.paths.map((path,i) => <path key={i} id={`live-route-${['A','B','C'][i]}`} d={path}/>)}
        {state.decisions.filter(d => d.selected).map(d => <RequestParticle key={d.id} decision={d} prefix="live-route-" neutralUntil={(() => {
          const start = source.current.getBoundingClientRect()
          const lb = balancer.current.getBoundingClientRect()
          const target = nodes.current['ABC'.indexOf(d.selected)].getBoundingClientRect()
          const incoming = lb.bottom - start.bottom
          const total = target.top - start.bottom + Math.abs(target.left + target.width/2 - (lb.left + lb.width/2))
          return incoming / total
        })()}/>)}
      </svg>}
      <div className="flow-source flow-box" ref={source}><span><Send size={25}/></span><div><strong>User requests</strong><small>Mooncake · {n(state.total)} recorded requests</small></div></div>
      <div className="balancer flow-box" ref={balancer}><span><GitBranch size={27}/></span><div><strong>Load Balancer</strong><small>{state.policy === 'cost' ? 'Minimum estimated completion cost' : 'Round Robin Routing'}</small></div><b>{n(queue)} queued</b></div>
      <div className="branches">{state.servers.map((s,i) => <div className="branch" key={s.id}><div ref={element => {nodes.current[i] = element}} className={`node ${latest?.selected === s.id ? 'receiving' : ''}`} style={{'--accent':COLORS[i]}}>
        <span className="node-icon"><Server size={23}/></span><span><strong>GPU Server {s.id}</strong><small>{s.activity ? `#${s.activity.id + 1} · ${s.activity.phase === 'prefill' ? '입력 처리' : '답변 생성'}` : '대기 중'} · 완료 {n(s.completed)}개</small></span><em>{n(s.queue)}<small>QUEUE</small></em>
        <div className="live-worker-progress"><span>{s.activity ? `예상 진행 ${s.activity.progress}%` : 'Idle'}</span><div><i style={{width:`${s.activity?.progress ?? 0}%`}}/></div></div>
        <div className="live-queue">{Array.from({length:Math.min(12,s.queue)},(_,j) => <i key={j}/>)}{s.queue > 12 && <small>+{n(s.queue - 12)}</small>}</div>
      </div></div>)}</div>
    </div>
    </div>
    <div className="traffic-legend"><span><i className="legend-dot small"/>작은 입력</span><span><i className="legend-dot large"/>큰 입력</span><span>초록 A · 파랑 B · 보라 C</span></div>
    <div className="flow-note"><Activity size={16}/>{state.running ? 'Mooncake 요청 재생 중' : state.finished ? '재생 완료' : '재생 중지'}</div>
    <p className="flow-disclaimer">점 = 요청 · 이동은 설명용 · 고배속 시 일부 생략 · 진행률은 예측 기준</p>
  </section>
}

export const LiveFlowMap = RequestStory

function Sparkline({ values, color }) {
  if (values.length < 2) return null
  const max = Math.max(...values), min = Math.min(...values), range = max - min
  const top = 6, bottom = 56 * 0.87
  const points = values.map((value,i) => `${i / (values.length - 1) * 100},${range > 0 ? bottom - (value-min) / range * (bottom - top) : (top + bottom) / 2}`).join(' ')
  return <svg className="sparkline" viewBox="0 0 100 56" preserveAspectRatio="none" aria-label="실제 상태 조회 추이"><polyline points={points} stroke={color} strokeWidth="1.5" fill="none" vectorEffect="non-scaling-stroke"/></svg>
}

export function LiveMetrics({state, samples}) {
  const metrics = [[Activity,'Total requests',n(state.processed),'최근 24시간 총 요청','servers',COLORS[0]],
    [Clock3,'Avg. latency',`${state.avg_latency.toFixed(2)} s`,'완료 요청의 평균 지연','latency',COLORS[1]],
    [Zap,'Completed requests',n(state.completed),'처리 완료','completed',COLORS[2]]]
  return <section className="metrics-grid below-flow actual-metrics">{metrics.map(([Icon,label,value,sub,key,color]) => <article className="metric-card" key={key}><div className="metric-top"><span className="icon-box"><Icon size={17}/></span>{label}</div><div className="metric-value">{value}</div><div className="metric-sub">{sub}</div><div className="metric-spark"><Sparkline values={samples.map(p => key === 'servers' ? p.processed : p[key])} color={color}/></div></article>)}</section>
}

export function LiveServerCard({server:s,index,selected,onClick}) {
  const color = COLORS[index]
  return <button className={`server-card ${selected ? 'selected' : ''}`} style={{'--accent':color}} onClick={onClick} aria-pressed={selected}>
    <div className="server-head"><span className="server-icon"><Server size={19}/></span><span><strong>GPU Server {s.id}</strong><small><i/>{s.activity ? s.activity.phase === 'prefill' ? '입력 처리 중' : '답변 생성 중' : '유휴'}</small></span></div>
    <div className="server-body"><div className="server-stats"><div><span>대기 요청</span><b>{n(s.queue)}개</b></div><div><span>처리 중 입력</span><b>{n(s.context_tokens)} tok</b></div><div><span>처리 진행</span><b>{s.activity ? `${s.activity.progress}%` : '대기 중'}</b></div></div></div>
    <div className="server-foot"><span><Send size={13}/>{n(s.assigned ?? 0)} assigned</span><span><Zap size={13}/>{n(s.completed)} completed</span></div>
  </button>
}

export function DistributionChart({samples}) {
  const max = Math.max(1,...samples.flatMap(p => p.servers))
  const series = [0,1,2].map(i => samples.map(p => p.servers[i]))
  const points = values => values.map((value,i) => `${i / Math.max(1,values.length-1) * 100},${100 - value/max*90}`).join(' ')
  return <section className="panel chart-panel live-distribution" id="distribution"><div className="panel-head"><div><h2>Request distribution</h2><p>서버별 누적 배정 · 최근 최대 60회 API 상태 조회 · req/s 아님</p></div><div className="legend">{COLORS.map((color,i) => <span key={color}><i style={{background:color}}/>Server {['A','B','C'][i]}</span>)}</div></div>
    {samples.length < 2 ? <p className="chart-empty">재생을 시작하면 실제 서버 배정 추이가 표시됩니다.</p> : <div className="chart-wrap"><div className="y-labels"><span>{n(max)}</span><span>{n(Math.round(max/2))}</span><span>0</span></div><svg viewBox="0 0 100 110" preserveAspectRatio="none" aria-label="서버별 누적 요청 배정 추이">{[10,55,100].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="#dce8e3" strokeWidth=".3"/>)}{series.map((values,i) => <polyline key={i} points={points(values)} fill="none" stroke={COLORS[i]} strokeWidth="1.7" vectorEffect="non-scaling-stroke"/>)}</svg><div className="x-labels"><span>{samples[0].time.toFixed(1)}s</span><span>시뮬레이션 시각</span><span>{samples.at(-1).time.toFixed(1)}s</span></div></div>}
  </section>
}
