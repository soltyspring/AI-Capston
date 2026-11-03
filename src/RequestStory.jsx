import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowRight, Check, GitBranch, RotateCcw, Send, Server } from 'lucide-react'
import TrafficPipeline from './TrafficPipeline.jsx'
import { advanceStory, nextStoryRequest, routePoint, STORY_PHASES, STORY_NUMBER_DURATION, storyQuestion, storyProgress, storyNumber, storyExplanation } from './storyTimeline.js'

const COLORS = {A:'#1a8c67', B:'#427bbb', C:'#8669b5'}
const n = value => Number(value).toLocaleString()
const TITLES = {typing:'01 · 사용자 입력',inbound:'요청을 전달합니다',input:'02 · 입력 크기 확인',output:'예상 답변 길이 확인',wait:'서버의 남은 작업 확인',compare:'어디서 먼저 끝날까요?',dispatch:'03 · 선택한 서버로 이동',processing:'요청 처리 중',complete:'04 · 처리 완료',live:'실시간 요청 흐름'}

function useRequestStory(state) {
  const [frame,setFrame] = useState({phase:'typing',elapsed:0})
  const [tracked,setTracked] = useState(null)
  const [completion,setCompletion] = useState(null)
  const [processingPercent,setProcessingPercent] = useState(0)
  const used = useRef(new Set())
  const previousRequest = useRef(null)
  const cycle = useRef(0)
  const current = useRef({state,tracked,completion,processingPercent})
  current.current = {state,tracked,completion,processingPercent}
  useEffect(() => {
    if (cycle.current !== (frame.cycle || 0)) {
      cycle.current = frame.cycle || 0
      previousRequest.current = tracked
      setTracked(null)
      setCompletion(null)
      setProcessingPercent(0)
      return
    }
    if (!tracked) {
      const request = nextStoryRequest(state.decisions,used.current,previousRequest.current)
      if (request) {used.current.add(request.id);setTracked(request)}
    }
    const done = state.completions?.find(item => item.id === tracked?.id)
    if (done) setCompletion(done)
  }, [state.decisions,state.completions,tracked,frame.cycle])
  useEffect(() => {
    if (!state.running || frame.phase !== 'processing') return
    const timer = setInterval(() => setProcessingPercent(value => Math.min(100,value+1)),20)
    return () => clearInterval(timer)
  },[state.running,frame.phase])
  useEffect(() => {
    if (!state.running) return
    let handle, previous = performance.now()
    const tick = now => {
      const delta = now-previous
      if (delta >= 16) {
        previous = now
        const value = current.current
        setFrame(old => advanceStory(old,delta,{hasDecision:!!value.tracked,hasCompletion:!!value.completion,hasAnimationFinished:value.processingPercent===100}))
      }
      handle = requestAnimationFrame(tick)
    }
    handle = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(handle)
  }, [state.running,frame.phase])
  const restart = () => setFrame(old=>({phase:'typing',elapsed:0,cycle:(old.cycle || 0)+1}))
  return {frame,tracked,completion,restart,processingPercent}
}

export default function RequestStory({state}) {
  const viewport = useRef(null)
  const svg = useRef(null)
  const [dotScale,setDotScale] = useState(1)
  useLayoutEffect(() => {
    const element=viewport.current
    const center=()=>{
      element.scrollLeft=(element.scrollWidth-element.clientWidth)/2
      // Routes stretch to the card positions, but markers must stay round in CSS pixels.
      setDotScale(1000 / (element.firstElementChild?.getBoundingClientRect().width || 1000))
    }
    center()
    const observer=new ResizeObserver(center)
    observer.observe(element)
    observer.observe(element.firstElementChild)
    return ()=>observer.disconnect()
  },[])
  useEffect(() => {
    if (state.running) svg.current?.unpauseAnimations()
    else svg.current?.pauseAnimations()
  },[state.running])
  const {frame,tracked,completion,restart,processingPercent} = useRequestStory(state)
  const live = frame.phase === 'live'
  const request = tracked
  const phaseIndex = STORY_PHASES.indexOf(frame.phase)
  const progress = storyProgress(frame)
  const question = storyQuestion(frame)
  const typed = frame.phase === 'typing' ? question.text.slice(0,Math.floor(frame.elapsed/95)) : question.text
  const selected = request?.selected
  const candidates = request?.candidates || []
  const source = {x:500,y:176}, lbTop = {x:500,y:244}, lbBottom = {x:500,y:425}
  const targetX = {A:170,B:500,C:830}
  const target = {x:targetX[selected] || 500,y:508}
  const dispatchPoints = [lbBottom,{x:500,y:466},{x:target.x,y:466},target]
  const ball = frame.phase === 'inbound' ? routePoint([source,lbTop],progress) : frame.phase === 'dispatch' ? routePoint(dispatchPoints,progress) : frame.phase === 'processing' ? target : null
  const dotPaths = ['A','B','C'].map(id => `M500 176 L500 244 L500 425 L500 466 L${targetX[id]} 466 L${targetX[id]} 508`)
  const stage = phaseIndex < 2 ? 0 : phaseIndex < 6 ? 1 : phaseIndex < 8 ? 2 : 3
  const processing = frame.phase === 'processing'
  const afterDispatch = phaseIndex >= 6
  const done = frame.phase === 'complete'
  const chosenCost = candidates.find(candidate => candidate.server === selected)
  const explanation = storyExplanation(frame,request)
  return <section className={`panel request-story phase-${frame.phase} ${state.running ? 'story-playing' : 'story-paused'}`} id="live-flow" aria-label="요청 처리 동작 시연">
    <div className="story-heading"><div><span className="eyebrow">LIVE REQUEST FLOW</span><h2>실시간 요청 분배</h2></div><span className="story-live"><i/>{state.running ? 'LIVE' : '중지됨'}</span></div>
    <div className="story-stage-strip"><div className="story-dots" aria-label="입력, 비교, 분배, 완료 단계">{['입력','비교','분배','완료'].map((label,i) => <span className={stage===i ? 'active' : stage>i ? 'passed' : ''} key={label}><i/>{label}</span>)}</div>{live && <button className="story-restart" onClick={restart}><RotateCcw size={14}/>다시 시연</button>}</div>
    <p className="story-caption">설명용 질문 · {request ? `실제 요청 #${request.id+1}의 계산·결과로 시연` : 'Mooncake 요청 연결 중'}</p>
    <div className="story-traffic-summary" aria-label="실제 재생 중 요청 현황">
      <span title={state.latest_arrival ? `같은 시각 도착 묶음 · 즉시 시작 ${state.latest_arrival.immediate}건 / 대기 진입 ${state.latest_arrival.queued}건 / 거절 ${state.latest_arrival.rejected}건` : '도착 대기'}>최근 <b>{state.latest_arrival?.arrived || 0}건</b> 도착</span>
      <span>현재 <b>{state.servers.filter(server=>server.activity).length}건</b> 처리</span>
      <span>현재 <b>{state.servers.reduce((sum,server)=>sum+server.queue,0)}건</b> 대기</span>
      <small>{state.latest_arrival ? `도착 당시 ${state.latest_arrival.immediate}건 바로 처리 · ${state.latest_arrival.queued}건 대기${state.latest_arrival.rejected ? ` · ${state.latest_arrival.rejected}건 제외` : ''} / ` : ''}점 1개 = 요청 1건</small>
    </div>
    <div className="story-flow-surface">
        <div className={`story-phase-callout ${done || live ? 'is-complete' : ''}`} role="status" aria-live="polite">
          <span className="story-phase-icon">{done || live ? <Check size={18}/> : phaseIndex<2 ? <Send size={18}/> : phaseIndex<6 ? <GitBranch size={18}/> : <Server size={18}/>}</span>
          <div><strong>{done || live ? '04 · 처리 완료' : TITLES[frame.phase]}</strong><small>{done || live ? `서버 ${selected || '—'} · 요청 #${request ? request.id+1 : '—'}` : frame.phase==='compare' ? '서버별 예상 완료 시간 계산' : processing ? `서버 ${selected} · ${processingPercent}%` : phaseIndex>=2 && phaseIndex<6 ? '요청 크기와 서버 상태 확인' : '입력 → 비교 → 분배 → 처리'}</small></div>
          {done || live ? <span className="story-phase-done">완료</span> : <span className="story-phase-step">{String(stage+1).padStart(2,'0')} / 04</span>}
        </div>
      {explanation && <div className="story-explanation" role="note" key={`${frame.cycle || 0}-${explanation.key}`} aria-label="처리 단계 설명"><div className="story-explanation-clip"><div className="story-explanation-message"><strong>{explanation.title}</strong><p>{explanation.text}</p></div></div></div>}
    <div ref={viewport} className="story-canvas-viewport" tabIndex={0} role="region" aria-label="요청 이동 시연 · 좁은 화면은 좌우 이동">
      <div className="story-canvas">
        <svg ref={svg} className="story-routes" viewBox="0 0 1000 650" preserveAspectRatio="none" aria-label="요청의 이동 경로">
          {dotPaths.map((path,i) => <path key={i} id={`story-route-${'ABC'[i]}`} d={path}/>)}
          {afterDispatch && selected && !live && <path className="story-route-selected" d={`M500 425 L500 466 L${target.x} 466 L${target.x} 508`} style={{stroke:COLORS[selected]}}/>}
          {ball && <g className="story-traveller" transform={`translate(${ball.x} ${ball.y}) scale(${dotScale} 1)`}><circle r="10" fill={frame.phase==='inbound' ? '#9aa3af' : COLORS[selected]} opacity=".1"/><circle className="story-traveller-core" r="6" fill={frame.phase==='inbound' ? '#9aa3af' : COLORS[selected]} stroke="#fff" strokeWidth="1.5"/></g>}
          <TrafficPipeline state={state} dotScale={dotScale}/>
        </svg>
        <div className={`story-source ${phaseIndex<2 ? 'focused' : ''}`}>
          <div className="story-source-head"><span><Send size={21}/></span><div><strong>User requests</strong><small>Mooncake · {n(state.total)} requests</small></div><span className="story-send-status">{frame.phase==='typing' ? '입력 중' : <Check size={17}/>}</span></div>
          <div className="story-message" key={frame.cycle || 0}><blockquote><span>{typed || '\u00a0'}</span>{frame.phase==='typing' && <i className="story-caret"/>}</blockquote><span className={`story-send-button ${frame.phase==='typing' ? '' : 'sent'}`}><ArrowRight size={17}/></span></div>
          <div className="story-message-meta"><span>{question.label}</span><span>{frame.phase==='typing' ? '질문 작성 중' : '요청 전달됨'}</span></div>
          <div className="story-compose-line"><i style={{width:`${Math.min(100,typed.length/question.text.length*100)}%`}}/></div>
        </div>
        <div className={`story-balancer ${phaseIndex>=2 && phaseIndex<7 ? 'focused' : ''}`}>
          <div className="story-balancer-head"><GitBranch size={23}/><strong>Load Balancer</strong><span>{phaseIndex<2 ? '요청 대기' : phaseIndex<6 ? <><i className="thinking-dot"/>비교 중</> : selected ? `서버 ${selected} 선택` : '선택 대기'}</span></div>
          <div className="story-facts">{[
            ['input','입력 토큰',request?.input_tokens,'tok'],
            ['output','예상 출력',request?.predicted_output_tokens,'tok'],
            ['wait','선택 시 대기',request ? candidates.reduce((sum,c)=>sum+(c.observation?.queue||0),0) : null,'건'],
          ].map(([key,label,target,unit],i) => {
            const value=storyNumber(frame,key,target)
            const confirmed=phaseIndex>2+i || (frame.phase===key && frame.elapsed>=STORY_NUMBER_DURATION.fact)
            return <div className={`${frame.phase===key ? 'scanning' : ''} ${value==null ? 'fact-pending' : ''}`} key={key}><span>{label}{confirmed ? <Check size={11}/> : frame.phase===key ? <i className="thinking-dot"/> : null}</span><strong>{value==null ? '—' : n(Math.round(value))}<small>{value==null ? '' : unit}</small></strong><i className="story-fact-track"><i style={{width:`${target===0 && value!=null ? 100 : target ? (value || 0)/target*100 : 0}%`}}/></i></div>
          })}</div>
          <div className="story-costs" aria-label="서버별 예상 완료 시간 · 0초부터 계산값까지 표시">{candidates.length ? candidates.map((candidate,i) => {
            const value=storyNumber(frame,'compare',candidate.total,{delay:i*STORY_NUMBER_DURATION.stagger,duration:STORY_NUMBER_DURATION.cost})
            const counting=frame.phase==='compare' && frame.elapsed<i*STORY_NUMBER_DURATION.stagger+STORY_NUMBER_DURATION.cost
            return <div key={candidate.server} className={`${value==null ? 'cost-pending' : ''} ${counting ? 'cost-counting' : ''} ${phaseIndex>=6 && candidate.server===selected ? 'winner' : ''}`}><span>{candidate.server}</span><strong>{value==null ? '—' : candidate.eligible ? <>{value.toFixed(2)}<small>초</small></> : '용량 초과'}</strong>{phaseIndex>=6 && candidate.server===selected ? <Check size={13}/> : <span className="story-cost-marker"/>}</div>
          }) : <span className="story-waiting">요청 연결 중</span>}</div>
        </div>
        <div className="story-server-row">{state.servers.map(server => {
          const highlighted = afterDispatch && selected===server.id
          const storyStatus = (done || live) && highlighted ? '처리 완료' : processing && highlighted ? processingPercent===100 ? '완료 확인 중' : '처리 과정 재현' : server.activity ? '처리 중' : '대기 중'
          const percent = highlighted ? phaseIndex>=8 ? 100 : processing ? processingPercent : 0 : Math.floor(server.activity?.progress || 0)
          return <div className={`story-server ${highlighted ? 'story-selected' : ''} ${done&&highlighted ? 'story-done' : ''}`} key={server.id} style={{'--server-color':COLORS[server.id]}}>
            <div className="story-server-head"><Server size={21}/><strong>GPU Server {server.id}</strong>{done&&highlighted ? <Check size={18}/> : null}</div>
            <div className="story-server-status"><span>{storyStatus}</span><span>{highlighted ? <b className="story-percent">{percent}%</b> : <>대기 <b>{n(server.queue)}</b></>}</span></div>
            <div className={`story-server-meter ${highlighted ? 'story-replay-meter' : ''}`} role="progressbar" aria-label={`서버 ${server.id} 처리 과정 재현 진행률`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{width:`${percent}%`}}/></div>
            <small>{highlighted ? `요청 #${tracked.id+1} · ${processing ? '빠른 시연 · 실제 처리 시간과 별개' : done || live ? '완료 기록 확인' : '요청 전달 중'}` : `완료 ${n(server.completed)}건`}</small>
          </div>
        })}</div>
      </div>
    </div>
    </div>
    <div className={`story-result ${done ? 'result-visible' : ''}`}>
      {done && completion ? <><span className="story-result-check"><Check size={19}/></span><div><strong>서버 {completion.server}에서 요청을 처리했습니다.</strong><p>예상 {n(completion.predicted_output_tokens)} <ArrowRight size={13}/> 실제 {n(completion.output_tokens)}토큰 <span>· 지연 {completion.latency.toFixed(2)}초</span></p></div></> : <><span className="story-result-label">{live ? '실시간 재생' : '처리 흐름'}</span><p>{frame.phase==='typing' ? '질문을 입력하면 요청이 만들어집니다.' : frame.phase==='inbound' ? '요청이 연결선을 따라 로드밸런서로 이동합니다.' : phaseIndex>=2 && phaseIndex<6 ? '입력 크기, 예상 답변, 서버의 남은 작업을 확인합니다.' : frame.phase==='dispatch' ? `예상 ${chosenCost?.total.toFixed(2) || '—'}초 · 서버 ${selected}로 전달합니다.` : processing ? '실제 완료 기록을 확인하고 있습니다.' : '실제 요청의 분배와 서버 상태를 확인하세요.'}</p></>}
    </div>
  </section>
}
