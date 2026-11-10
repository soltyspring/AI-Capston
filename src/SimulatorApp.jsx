import React, { useEffect, useRef, useState } from 'react'
import { DashboardShell, LiveFlowMap, LiveMetrics, LiveServerCard, DistributionChart } from './DashboardView.jsx'
import ConsiderationPanel from './ConsiderationPanel.jsx'
import ResearchGuide from './ResearchGuide.jsx'
import ActualDashboard from './ActualDashboard.jsx'

const number = value => Number(value).toLocaleString()
// Vite's base is '/' locally and '/aicap/' for the path-based public deployment.
const API_BASE = `${import.meta.env.BASE_URL}api`

async function sendControl(action, extra = {}) {
  const response = await fetch(`${API_BASE}/control`, {
    method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({action, policy: 'cost', ...extra}),
  })
  if (!response.ok) throw new Error(`요청 실패 (${response.status})`)
  return response.json()
}

export default function SimulatorApp() {
  const [page,setPage]=useState(1)
  const pageRef=useRef(page)
  pageRef.current=page
  const [state, setState] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState('A')
  const [samples, setSamples] = useState([])
  const requestVersion = useRef(0)
  const commandInFlight = useRef(false)
  useEffect(() => {
    if (!state) return
    setSamples(previous => {
      const point = {run:`${state.predictor}-${state.total}-${state.run_id}`, time:state.time, processed:state.processed, servers:state.servers.map(s => s.assigned ?? 0), completed:state.completed, latency:state.avg_latency, rejected:state.rejected}
      if (previous.at(-1)?.run !== point.run || state.time < (previous.at(-1)?.time ?? 0) || state.processed < (previous.at(-1)?.processed ?? 0)) return [point]
      if (previous.at(-1)?.time === point.time) return previous
      return [...previous.slice(-59), point]
    })
  }, [state?.time, state?.run_id, state?.predictor, state?.total, state?.processed])
  useEffect(() => {
    let alive = true, timer, initialized = false
    async function poll() {
      if (pageRef.current !== 1) {timer = setTimeout(poll, 1000); return}
      if (commandInFlight.current) {timer = setTimeout(poll, 500); return}
      const version = ++requestVersion.current
      try {
        const response = await fetch(`${API_BASE}/state`, {credentials: 'same-origin', cache: 'no-store'})
        if (!response.ok) throw new Error('API 조회 실패')
        let data = await response.json()
        if (!initialized && alive) {
          if (data.finished || data.policy !== 'cost' || data.traffic?.mode !== 'standard') await sendControl('reset', {traffic_mode:'standard'})
          if (!alive) return
          data = await sendControl('start', {speed: 1})
          initialized = true
        }
        if (alive && version === requestVersion.current) { setState(data); setError('') }
      } catch {
        if (alive && version === requestVersion.current) setError('시뮬레이션 연결이 지연되고 있습니다. 잠시 후 다시 시도해 주세요.')
      } finally { if (alive) timer = setTimeout(poll, 500) }
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  async function command(action) {
    if (commandInFlight.current) return
    commandInFlight.current = true
    ++requestVersion.current
    setBusy(true)
    try {
      if (action === 'start' && state?.finished) await sendControl('reset')
      const data = await sendControl(action)
      setState(previous => ({ ...previous, ...data })); setError('')
    } catch (e) { setError(e.message) }
    finally { commandInFlight.current = false; setBusy(false) }
  }
  async function changeTraffic(trafficMode) {
    if (commandInFlight.current) return
    commandInFlight.current = true
    ++requestVersion.current
    setBusy(true)
    try {
      await sendControl('reset', {traffic_mode:trafficMode, speed:1})
      const data = await sendControl('start')
      setState(data); setError('')
    } catch (e) {setError(e.message)}
    finally {commandInFlight.current = false; setBusy(false)}
  }
  const latest = state?.decisions.at(-1)
  return <DashboardShell state={state}>
    <div className="content" hidden={page!==1}>
      <div className="playback-toolbar">
        <span className="traffic-mode-label">{state?.traffic?.label ?? '일반 보기'}</span>
        <details className="traffic-experiment"><summary>실험 모드</summary><div>
          <p>모드를 바꾸면 내 재생만 처음부터 시작합니다.</p>
          <button disabled={busy || !state || state.traffic?.mode === 'standard'} onClick={() => changeTraffic('standard')}>일반 보기</button>
          <button disabled={busy || !state || state.traffic?.mode === 'original'} onClick={() => changeTraffic('original')}>원본 트래픽 (과부하)</button>
          <small>일반: 유입 간격 {state?.traffic?.calibration?.interval_scale ?? '—'}배 · 원본: 실제 도착 간격</small>
        </div></details>
        <button className="playback-toggle" disabled={busy || !state} onClick={() => command(state.running ? 'pause' : 'start')}>{!state ? '연결 중' : state.running ? '중지' : '시작'}</button>
      </div>
      {error && <p role="alert" className="simulation-error">{error}</p>}
      {state && <LiveFlowMap key={`${state.run_id}-${state.policy}-${state.dataset?.name}`} state={state}/>}
      <details className="panel dataset-panel"><summary>{state?.dataset?.name ?? '데이터 연결 확인 중'} · 데이터 출처와 재생 설정</summary>
        {state?.dataset && <><p>원본 {number(state.dataset.requests)}개 · {Math.floor(state.dataset.duration_seconds / 60)}분 {Math.round(state.dataset.duration_seconds % 60)}초 · {state.dataset.mode}</p>
          <p>실제 기록: 도착 시각 · 입력/출력 토큰 수. 가상 계산: 서버 3대 · 큐 · 처리 시간.</p>
          <label>요청 재생 {number(state.processed)} / {number(state.total)} ({(state.processed / state.total * 100).toFixed(1)}%)<progress value={state.processed} max={state.total}/></label>
          <p>{state.traffic?.mode === 'standard' ? `일반 보기: 원본 유입 간격을 ${state.traffic.arrival_interval_scale}배로 조정합니다. 요청 순서·동시 도착·토큰 값은 유지합니다.` : '과부하 실험: 원본 도착 간격을 그대로 재생합니다.'}</p>
          <details><summary>출처와 시뮬레이션 설정</summary><p><a href="https://github.com/kvcache-ai/Mooncake/blob/main/FAST25-release/README.md" target="_blank" rel="noreferrer">Mooncake 공식 데이터 설명</a></p><p>파일: {state.source} · 예측: {state.predictor}</p><p>입력 4,000토큰/초 · 생성 100토큰/초 (가정값, GPU 실측 아님)</p><p>출력 원본 길이는 완료 계산에만 사용하며, 서버 선택에는 예상 출력만 사용합니다.</p><p className="checksum">SHA256: {state.dataset.sha256 ?? '사용자 지정 데이터'}<br/>버전: {state.dataset.revision ?? '사용자 지정'}</p></details>
        </>}
      </details>
      <ResearchGuide state={state}/>
      {state && <>
        <LiveMetrics state={state} samples={samples}/>
        <ConsiderationPanel state={state}/>
        <DistributionChart samples={samples}/>
        <div className="section-title" id="servers"><div><h2>GPU server status</h2><p>서버를 선택하면 상세 상태를 확인합니다.</p></div></div>
        <section className="server-grid">{state.servers.map((s,i) => <LiveServerCard key={s.id} server={s} index={i} selected={selected === s.id} onClick={() => setSelected(s.id)}/>)}</section>
        <section className="selected-server-detail">{state.servers.filter(s => s.id === selected).map(s => <article className="panel" key={s.id}>
          <h2>서버 {s.id} · {s.activity ? s.activity.phase === 'prefill' ? '입력 처리 중' : '답변 생성 중' : '유휴'}</h2>
          {s.activity && <><p>요청 #{s.activity.id + 1} · 경과 {s.activity.elapsed.toFixed(1)}초 · 생성 {number(s.activity.generated_tokens)}토큰</p><progress aria-label={`서버 ${s.id} 예상 진행률`} value={s.activity.progress} max="100"/><small>예측 출력 길이 기준 {s.activity.progress}% · 완료 전 최대 99%</small></>}
          <dl><dt>대기 요청</dt><dd>{s.queue}개</dd><dt>대기 입력</dt><dd>{number(s.queued_input_tokens)} 토큰</dd><dt>처리 중 입력</dt><dd>{number(s.context_tokens)} 토큰</dd><dt>처리 중</dt><dd>{s.gpu ? '예' : '아니요'}</dd></dl>
        </article>)}</section>
        <section className="panel decision-panel" id="completions"><h2>처리 완료 기록</h2><p>예상·실제 출력과 처리 지연</p>
          <div className="completion-timeline">{state.completions?.length ? [...state.completions].reverse().slice(0,8).map(e => <div className="completion-event" key={e.id}><span className="completion-check">✓</span><div><strong>#{e.id + 1} · 서버 {e.server} 처리 완료</strong><small>{e.time.toFixed(1)}초 시점 · 예상 {number(e.predicted_output_tokens)} / 실제 {number(e.output_tokens)}토큰 · 오차 {number(e.error_tokens)}토큰 · 총 지연 {e.latency.toFixed(2)}초</small></div></div>) : <p>아직 완료된 요청이 없습니다. 시뮬레이션을 시작하세요.</p>}</div>
        </section>
        <section className="panel decision-panel" id="routing"><h2>마지막 요청의 선택 근거</h2>
          {latest ? <><p>입력 {number(latest.input_tokens)} 토큰 · 예상 출력 {latest.predicted_output_tokens} 토큰 ({state.predictor}) · {latest.selected ? `서버 ${latest.selected} 선택` : '처리 가능한 서버 없음 → 거절'}</p>
            <div className="table-scroll"><table><thead><tr>{['서버', '가능 여부', '대기', '입력 처리', '답변 생성', '예상 합계'].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>{latest.candidates.map(c => <tr key={c.server} className={c.server === latest.selected ? 'chosen' : ''}><td>{c.server}</td><td>{c.eligible ? '가능' : '용량 초과'}</td>{['wait', 'prefill', 'decode', 'total'].map(k => <td key={k}>{c[k].toFixed(2)}초</td>)}</tr>)}</tbody></table></div>
          </> : <p>요청을 기다리는 중입니다.</p>}
        </section>
        <section className="panel decision-panel" id="request-logs"><h2>최근 분배 기록</h2><div className="table-scroll"><table><thead><tr><th>요청</th><th>도착 시각</th><th>입력 토큰</th><th>예상 출력</th><th>분배 서버</th></tr></thead><tbody>{[...state.decisions].reverse().map(d => <tr key={d.id}><td>#{d.id + 1}</td><td>{d.time.toFixed(2)}초</td><td>{number(d.input_tokens)}</td><td>{d.predicted_output_tokens}</td><td>{d.selected ?? '거절'}</td></tr>)}</tbody></table></div></section>
      </>}
    </div>
    {page===2 && <div className="content"><ActualDashboard/></div>}
    <nav className="page-switch" aria-label="화면 선택"><button aria-pressed={page===1} onClick={()=>setPage(1)}>1</button><button aria-pressed={page===2} onClick={()=>setPage(2)}>2</button></nav>
  </DashboardShell>
}
