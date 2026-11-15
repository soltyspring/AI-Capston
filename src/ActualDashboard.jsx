import React, {useEffect, useRef, useState} from 'react'
import {Activity, Clock3, GitBranch, Send, Server, Zap} from 'lucide-react'
import {savedAnswers, saveAnswer} from './savedAnswers.js'

const ids=['ai','ai1','ai2'], colors=['#1a8c67','#427bbb','#8669b5']
const failureLabels={queue_overflow:'대기열 초과',queue_timeout:'대기 시간 초과',request_limit:'요청 한도 초과',hourly_request_limit:'시간당 요청 한도',invalid_or_over_limit_input:'입력 검증 실패',upstream_error:'모델 서버 오류',no_healthy_server:'사용 가능한 서버 없음',request_timeout:'요청 시간 초과',client_disconnected:'연결 종료',stream_ended_without_completion:'완료 이벤트 없음',empty_prompt:'빈 질문'}
const examples=['GPU 서버의 역할을 한 문장으로 설명해줘.','로드밸런서는 왜 필요한가요?','인공지능 모델의 토큰이란 무엇인가요?','서버 대기열은 무엇인가요?','GPU 메모리는 어떤 역할을 하나요?','AI 추론이란 무엇인가요?','응답 지연 시간은 무엇인가요?','트래픽 분산의 장점을 알려줘.','서버 부하가 높다는 것은 무슨 뜻인가요?']
const api=`${import.meta.env.BASE_URL}api/control`
const val=(n,unit='')=>typeof n==='number'&&Number.isFinite(n)?`${Number(n.toFixed(1)).toLocaleString()}${unit}`:'—'
function memoryPercent(server) {
  const used=server?.gpu_memory_used_mib, total=server?.gpu_memory_total_mib
  return Number.isFinite(used) && used >= 0 && Number.isFinite(total) && total > 0
    ? val((used/total)*100,'%') : '—'
}
const lastSeenKey='qless-gpu-last-seen'
function lastSeen(id) {
  try {
    const time=JSON.parse(localStorage.getItem(lastSeenKey) || '{}')?.[id]
    return Number.isFinite(time) && time > 0 && time <= Date.now() ? time : null
  } catch { return null }
}
function disconnectedLabel(minutes) {
  if (minutes == null) return '연결 끊김 (확인 기록 없음)'
  const elapsed=minutes < 60 ? `${minutes}분` : `${Math.floor(minutes/60)}시간${String(minutes%60).padStart(2,'0')}분`
  return `연결 끊김 (마지막 확인 ${elapsed} 전)`
}
async function post(action,extra={},signal){return fetch(api,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra}),signal})}

export default function ActualDashboard(){
  const [prompt,setPrompt]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const [status,setStatus]=useState(null),[history,setHistory]=useState([]),[event,setEvent]=useState(null),[answer,setAnswer]=useState(''),[results,setResults]=useState([])
  const [monitor,setMonitor]=useState({summary:null,resources:[],requests:[],experiments:[]})
  const [metrics,setMetrics]=useState({sent:0,completed:0,rejected:0,totalSeconds:0})
  const [serverActivity,setServerActivity]=useState({})
  function updateServer(id, pendingDelta, completedDelta=0) {
    if (!ids.includes(id)) return
    setServerActivity(previous=>{
      const {pending=0,completed=0}=previous[id] || {}
      return {...previous,[id]:{
        pending:Math.max(0,pending+pendingDelta),
        completed:completed+completedDelta,
      }}
    })
  }
  const [metricHistory,setMetricHistory]=useState([])
  const [repeat,setRepeat]=useState(false), [particles,setParticles]=useState([]), [activeCount,setActiveCount]=useState(0), [flowPlaying,setFlowPlaying]=useState(false), stop=useRef(false), seq=useRef(0), displayed=useRef(null), active=useRef(new Set()), lastGood=useRef({}), typing=useRef(null)
  useEffect(()=>{setMetricHistory(old=>[...old.slice(-59),metrics])},[metrics])
  useEffect(()=>{
    let alive=true,timer,controller
    async function poll(){
      controller=new AbortController();const deadline=setTimeout(()=>controller.abort(),7000)
      function showStatus(data){
        const now=Date.now(),servers={},timestamps={}
        for(const id of ids){
          const current=data?.servers?.[id]
          if(current?.available){
            const age=Number.isFinite(current.age_seconds)?Math.max(0,current.age_seconds):0
            lastGood.current[id]={value:current,at:now,seen:Math.floor(now-age*1000)}
          }
          const previous=lastGood.current[id]
          const remoteSeen=Number.isFinite(current?.last_seen_at)?current.last_seen_at*1000:null
          const seen=remoteSeen ?? previous?.seen ?? lastSeen(id)
          if(seen!=null) timestamps[id]=seen
          const cached=previous && now-previous.at<60000 ? {...previous.value,stale:true} : {available:false}
          servers[id]=current?.available ? current : {
            ...cached, minutesSinceSeen:seen==null?null:Math.max(0,Math.floor((now-seen)/60000))
          }
        }
        if(Object.keys(timestamps).length){
          try { localStorage.setItem(lastSeenKey,JSON.stringify(timestamps)) } catch {}
        }
        setStatus({...data,servers})
      }
      try{
        const r=await post('real_status',{},controller.signal);if(!r.ok)throw Error();const data=await r.json()
        if(alive){showStatus(data);setHistory(Array.isArray(data.history)?data.history:[])}
        const monitorResponse=await post('monitor_dashboard',{hours:24,limit:60},controller.signal)
        if(monitorResponse.ok&&alive){const payload=await monitorResponse.json();setMonitor({summary:payload.summary,resources:payload.resources||[],requests:payload.requests||[],experiments:payload.recent_experiments||[]})}
      }
      catch{if(alive){showStatus(null);setHistory(old=>[...old.slice(-59),{time:Date.now(),values:[null,null,null]}])}}
      finally{clearTimeout(deadline);if(alive)timer=setTimeout(poll,3000)}
    }
    poll();return()=>{alive=false;clearTimeout(timer);clearInterval(typing.current);controller?.abort();stop.current=true;active.current.forEach(c=>c.abort())}
  },[])
  async function send(text, isRepeat=false){
    let assigned=null, succeeded=false
    const controller=new AbortController();active.current.add(controller);const requestId=++seq.current;const started=performance.now();setActiveCount(n=>n+1);setMetrics(m=>({...m,sent:m.sent+1}))
    try {
    const response=await post('real_stream',{prompt:text},controller.signal)
    if(!response.ok){if(response.status===429||response.status===503)setMetrics(m=>({...m,rejected:m.rejected+1}));const data=await response.json().catch(()=>({}));const e=Error(data.detail||'실제 요청을 보낼 수 없습니다.');e.status=response.status;throw e}
    if(!response.body)throw Error('실시간 응답을 받을 수 없습니다.')
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',acc='',route=null,complete=false,replay=false
    async function consume(line){
      if(!line.trim())return
      const data=JSON.parse(line)
      if(data.event==='routed'){if(!assigned){assigned=data.server_id;updateServer(assigned,1)}route=data;displayed.current=requestId;replay=!!savedAnswers()[text];setAnswer(replay?savedAnswers()[text]:'');const estimates=ids.map(id=>data.candidates?.find(row=>row.server_id===id)?.estimated_seconds ?? null);setEvent({...data,estimates,phase:'routed',sequence:requestId});if(stop.current===false&&isRepeat){clearInterval(typing.current);let n=0;setPrompt('');typing.current=setInterval(()=>{n++;setPrompt(Array.from(text).slice(0,n).join(''));if(n>=Array.from(text).length)clearInterval(typing.current)},35)}setParticles(old=>[...old,{id:requestId,server:data.server_id}]);setTimeout(()=>setParticles(old=>old.filter(p=>p.id!==requestId)),1800)}
      if(data.event==='chunk'){acc+=(data.vllm?.choices||[]).map(x=>x.delta?.content||'').join('');if(displayed.current===requestId&&!replay)setAnswer(acc)}
      if(data.event==='complete'){complete=true;succeeded=true;if(!replay)saveAnswer(text,acc);setMetrics(m=>({...m,completed:m.completed+1,totalSeconds:m.totalSeconds+(performance.now()-started)/1000}));setEvent(previous=>previous?.sequence===requestId?{...previous,...data,phase:'complete'}:previous);setResults(old=>[{prompt:text,answer:acc,...route,...data},...old].slice(0,10))}
      if(data.event==='error'){if(data.status===429||data.status===503)setMetrics(m=>({...m,rejected:m.rejected+1}));throw Error(data.detail||'서버 처리에 실패했습니다.')}
    }
    while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const lines=buffer.split('\n');buffer=lines.pop();for(const line of lines)await consume(line);if(done){if(buffer.trim())await consume(buffer);break}}
    if(!complete)throw Error('응답이 완료되기 전에 연결이 종료됐습니다.')
    } finally {
      if (assigned) updateServer(assigned,-1,succeeded?1:0)
      active.current.delete(controller)
      setActiveCount(n=>Math.max(0,n-1))
    }
  }
  async function run(repeating){
    const text=prompt.trim();if(!repeating&&!text){setError('질문을 입력해 주세요.');return}
    setBusy(true);setRepeat(repeating);setError('');stop.current=false
    setEvent({phase:'sending'});setAnswer('')
    try{let next=0;const pause=ms=>new Promise(resolve=>{const start=Date.now();const timer=setInterval(()=>{if(stop.current||Date.now()-start>=ms){clearInterval(timer);resolve()}},250)});async function worker(){while(!stop.current){const i=next++;if(!repeating&&i>=1)break;try{await send(repeating?examples[i%examples.length]:text,repeating);if(!stop.current)setError('');if(repeating)await pause(3000)}catch(e){if(e.name!=='AbortError'){setError(e.status===429?'요청 한도 대기 중 · 잠시 후 자동 재시도합니다.':e.message);if(repeating)await pause(e.status===429?60000:10000)}}}}await Promise.all(Array.from({length:repeating?3:1},()=>worker()))}finally{setBusy(false);setRepeat(false)}
  }
  const chosen=event?.server_id, idx=ids.indexOf(chosen)
  const phase=event?.phase==='complete'?'complete':event?.phase==='routed'?'dispatch':event?.phase==='sending'?'compare':'input'
  const guide={input:['사용자 요청','질문을 입력하면 실제 요청이 만들어집니다.'],compare:['서버 비교','로드밸런서가 요청과 서버 상태를 확인합니다.'],dispatch:[`서버 ${chosen} 선택`,'선택 결과에 따라 요청을 전달하고 답변을 생성합니다.'],complete:['처리 완료',`서버 ${chosen}에서 답변을 완료했습니다.`]}[phase]
  return <>
  <section className="panel real-requests actual-dashboard" id="actual-flow"><div className="real-flow-heading"><div><span className="eyebrow">LIVE REQUEST FLOW</span><h2>실제 요청 분배</h2><p>질문을 보내면 실제 선택된 GPU 서버로 점이 이동합니다.</p></div><div className="actual-flow-controls"><span className="real-live">실제 서버</span><button className="playback-toggle" disabled={busy&&!repeat} onClick={()=>repeat?(stop.current=true,setRepeat(false),setFlowPlaying(false)):(setFlowPlaying(true),run(true))}>{repeat?'중지':'시작'}</button></div></div>
  <div className="story-stage-strip"><div className="story-dots" aria-label="입력, 비교, 분배, 완료 단계">{['입력','비교','분배','완료'].map((label,i)=><span key={label} className={(event?.phase==='complete'?3:event?.phase==='routed'?2:event?.phase==='sending'?1:0)===i?'active':''}><i/>{label}</span>)}</div></div><div className="actual-canvas"><div className="actual-source"><div className="real-flow-title"><Send size={20}/><strong>User requests</strong></div><label htmlFor="actual-prompt">질문</label><textarea id="actual-prompt" value={prompt} disabled={busy} maxLength={1000} onChange={e=>setPrompt(e.target.value)} placeholder="질문을 입력해 주세요."/><div className="real-actions"><button disabled={busy} onClick={()=>run(false)}>질문 보내기</button><span>{busy?`실제 요청 ${activeCount}건 처리 중`:'직접 질문을 입력하거나 시작 버튼을 눌러주세요.'}</span></div></div>
  {error&&<p role="alert">{error}</p>}
  <div className={`real-flow-link ${event?.phase==='sending'?'is-active':''}`}/><div className={`actual-guide guide-${phase}`} role="note" aria-label="처리 단계 설명" key={`${phase}-${event?.sequence||0}`}><strong>{guide[0]}</strong><p>{guide[1]}</p></div><div className="real-flow-balancer actual-story-balancer"><div className="story-balancer-head"><GitBranch size={23}/><strong>Load Balancer</strong><span>{event?.phase==='sending'?'비교 중':chosen?`서버 ${chosen} 선택`:'요청 대기'}</span></div><div className="story-facts">{[['입력 토큰',event?.input_tokens,'tok'],['예상 출력',event?.predicted_output_tokens,'tok'],['대기 시간',event?.queue_seconds,'초']].map(([label,value,unit])=><div key={label} className={value==null?'fact-pending':''}><span>{label}</span><strong>{val(value)}<small>{value==null?'':unit}</small></strong></div>)}</div><div className="story-costs" aria-label="배정 시점 서버별 예상 완료 시간">{ids.map((id,i)=><div key={id} className={id===chosen?'winner':''}><span>{'ABC'[i]}</span><strong>{typeof event?.estimates?.[i]==='number'?`${event.estimates[i].toFixed(2)}초`:'—'}</strong></div>)}</div><small className="actual-estimate-note">배정 시점 예상 시간 · LinUCB 점수와 별개</small></div>
  <div className="actual-paths"><svg viewBox="0 0 900 80" preserveAspectRatio="none" aria-label="서버 분배 경로"><path d="M450 0 V36 H150 V80 M450 36 V80 M450 36 H750 V80"/>{flowPlaying&&ids.flatMap((id,k)=>[0,1,2].map(j=><circle key={`ambient-${id}-${j}`} r="3" fill={colors[k]} opacity=".48"><animateMotion dur={`${3.4+k*.45}s`} begin={`${j*.8}s`} repeatCount="indefinite" path={`M450 0 V36 H${[150,450,750][k]} V80`}/></circle>))}{particles.map(p=>{const k=ids.indexOf(p.server);return k<0?null:<circle key={p.id} r="6" fill={colors[k]}><animateMotion dur="1.4s" fill="freeze" path={`M450 0 V36 H${[150,450,750][k]} V80`}/></circle>})}</svg></div>
  <div className="real-flow-servers" id="actual-servers">{ids.map((id,i)=>{const s=status?.servers?.[id],up=s?.available;const {pending:local=0,completed=0}=serverActivity[id] || {};const pending=Math.max(local,s?.active_routed||0), progress=s?.work_progress_pct;return <div style={{'--server-color':colors[i]}} className={`actual-server ${chosen===id?'selected':''}`} key={id}><div className="actual-server-title"><Server size={19}/><strong>GPU Server {id}</strong><span className={`actual-server-status ${s?.stale?'stale':up?'online':'unknown'}`}><i aria-hidden="true"/>{!status?'확인 중':up&&!s.stale?'연결됨':disconnectedLabel(s?.minutesSinceSeen)}</span></div><dl><div><dt>GPU 사용률</dt><dd>{up?val(s.gpu_utilization_pct,'%'):'—'}</dd></div><div><dt>GPU 메모리</dt><dd>{up?memoryPercent(s):'—'}</dd></div><div><dt>서버 대기</dt><dd>{up?val(s.waiting_requests,'건'):'—'}</dd></div></dl><div className="actual-server-progress">{pending > 0 && <span>{`처리 중 ${pending}건`}{progress!=null&&` · 약 ${progress}%`}</span>}<div className={`story-server-meter ${pending&&progress==null?'in-progress':''}`} role="progressbar" aria-label={`서버 ${id} 요청 처리 진행`} aria-valuenow={pending?progress??undefined:0}><i aria-hidden="true" style={pending&&progress!=null?{width:`${progress}%`,background:colors[i]}:undefined}/></div>{completed > 0 && <small>완료 {completed}건</small>}</div></div>})}</div></div>
  {(answer||event?.phase==='complete')&&<div className="actual-answer"><strong>{event?.phase==='complete'?'답변 완료':'답변 생성 중'}</strong><p className="answer-preview">{answer||'답변 없음'}</p></div>}</section>
  <section className="metrics-grid below-flow actual-metrics" aria-label="실제 요청 요약">{[[Activity,'Total requests',monitor.summary?.total_requests??'—','최근 24시간 · 저장된 실제 요청','sent',colors[0]],[Clock3,'Avg. latency',monitor.summary?.average_latency_seconds==null?'—':`${monitor.summary.average_latency_seconds.toFixed(2)} s`,'완료 요청의 평균 지연 · 실측','latency',colors[1]],[Zap,'Completed requests',monitor.summary?.completed_requests??'—','최근 24시간 처리 완료 · 실측','completed',colors[2]]].map(([Icon,label,value,sub,key,color])=>{const values=metricHistory.map(m=>key==='latency'?(m.completed?m.totalSeconds/m.completed:0):m[key]);const min=Math.min(...values),max=Math.max(...values),range=max-min,top=6,bottom=56*0.87;const points=values.map((x,i)=>`${i/Math.max(1,values.length-1)*100},${range>0?bottom-(x-min)/range*(bottom-top):(top+bottom)/2}`).join(' ');return <article className="metric-card" key={key}><div className="metric-top"><span className="icon-box"><Icon size={17}/></span>{label}</div><div className="metric-value">{value}</div><div className="metric-sub">{sub}</div><div className="metric-spark">{values.length>1&&<svg className="sparkline" viewBox="0 0 100 56" preserveAspectRatio="none" aria-label={`${label} 추이`}><polyline points={points} stroke={color} strokeWidth="1.5" fill="none" vectorEffect="non-scaling-stroke"/></svg>}</div></article>})}</section>
  <section className="panel actual-chart" id="actual-factors"><span className="eyebrow">ROUTING FACTORS · 실측</span><h2>GPU 서버 자원 현황</h2><p>CPU·일반 메모리·GPU·대기 요청을 3초마다 조회해 저장합니다.</p><div className="table-scroll"><table><thead><tr><th>항목</th>{ids.map((id,i)=><th key={id}>서버 {'ABC'[i]} · {id}</th>)}</tr></thead><tbody>{[['CPU 사용률','cpu_pct','%'],['일반 메모리 사용률','memory_pct','%'],['GPU 사용률','gpu_utilization_pct','%'],['GPU 메모리 사용률','gpu_memory_used_mib','%'],['대기 요청 수','waiting_requests','건'],['실행 요청 수','running_requests','건']].map(([label,key,unit])=><tr key={key}><th>{label}</th>{ids.map(id=><td key={id}>{status?.servers?.[id]?.available?(key==='gpu_memory_used_mib'?memoryPercent(status.servers[id]):val(status.servers[id][key],unit)):'—'}</td>)}</tr>)}</tbody></table></div></section>
  <section className="panel actual-chart"><div><span className="eyebrow">LIVE METRICS · 실측</span><h2>서버별 GPU 사용률</h2><p>저장된 최근 60개 자원 표본 · 약 3초 간격</p></div><div className="actual-legend">{ids.map((id,i)=><span key={id} style={{color:colors[i]}}>● {id}</span>)}</div><svg viewBox="0 0 600 150" preserveAspectRatio="none" role="img" aria-label="서버별 실제 GPU 사용률 추이"><path d="M0 1 H600 M0 75 H600 M0 149 H600" stroke="#dfe8e4" fill="none"/>{ids.map((id,i)=>monitor.resources.slice(1).map((sample,j)=>{const previous=monitor.resources[j],a=previous.servers?.[id]?.gpu_utilization_pct,b=sample.servers?.[id]?.gpu_utilization_pct;return typeof a==='number'&&typeof b==='number'?<line key={`${id}-${j}`} x1={j/59*600} y1={150-Math.max(0,Math.min(100,a))*1.5} x2={(j+1)/59*600} y2={150-Math.max(0,Math.min(100,b))*1.5} stroke={colors[i]} strokeWidth="2" vectorEffect="non-scaling-stroke"/>:null}))}</svg><small>100% · 중간 50% · 하단 0%</small></section>
  <section className="panel actual-chart" id="actual-routing"><h2>마지막 요청의 선택 근거</h2><p>{chosen?`서버 ${chosen} 선택 · 입력 ${val(event?.input_tokens)}토큰 · 예상 출력 ${val(event?.predicted_output_tokens)}토큰`: '실제 요청을 기다리는 중입니다.'}</p><p>서버별 예상 시간은 로드밸런서가 배정 직전에 계산한 값입니다. LinUCB 점수와는 별개입니다.</p></section>
  <section className="panel actual-chart" id="actual-request-logs"><span className="eyebrow">실측 · 질문 원문 미저장</span><h2>최근 분배 기록</h2><p>같은 요청에서 배정 당시 예상 시간과 실제 첫 응답·완료 시간을 비교할 수 있습니다.</p><div className="table-scroll"><table><thead><tr><th>요청</th><th>도착</th><th>입력</th><th>예상 출력</th><th>실제 출력</th><th>서버</th>{ids.map((id,i)=><th key={id}>{'ABC'[i]} 예상</th>)}<th>대기</th><th>첫 응답</th><th>완료</th><th>결과</th></tr></thead><tbody>{monitor.requests.map(row=><tr key={row.request_id}><td>#{row.request_id.slice(0,8)}</td><td>{new Date(row.received_at*1000).toLocaleTimeString('ko-KR')}</td><td>{val(row.input_tokens)}</td><td>{val(row.predicted_output_tokens)}</td><td>{val(row.actual_output_tokens)}</td><td>{row.server_id||'—'}</td>{ids.map(id=>{const seconds=row.routing_candidates?.find(candidate=>candidate.server_id===id)?.estimated_seconds;return <td key={id}>{seconds==null?'—':`${seconds.toFixed(2)}초`}</td>})}<td>{row.queue_wait_seconds==null?'—':`${Number(row.queue_wait_seconds).toFixed(2)}초`}</td><td>{row.first_response_seconds==null?'—':`${Number(row.first_response_seconds).toFixed(2)}초`}</td><td>{row.completion_seconds==null?'—':`${Number(row.completion_seconds).toFixed(2)}초`}</td><td>{row.failure_reason?failureLabels[row.failure_reason]||row.failure_reason:row.status==='completed'?'완료':row.status}</td></tr>)}</tbody></table></div></section>
  <section className="panel actual-chart" id="actual-experiment-logs"><span className="eyebrow">실측 · 상시 실험</span><h2>최근 실험 분배 및 요청 결과</h2>{!monitor.experiments.length?<p>최근 실험 기록을 기다리는 중입니다.</p>:<div className="table-scroll"><table><thead><tr><th>요청</th><th>서버</th><th>입력</th><th>예상 출력</th><th>실제 출력</th><th>중앙 대기</th><th>첫 응답</th><th>완료</th><th>결과</th></tr></thead><tbody>{monitor.experiments.map(row=><tr key={row.request_index}><td>{row.source==='comparison'?'비교':'실험'} #{row.request_index+1}</td><td>{row.server??'—'}</td><td>{val(row.input_tokens,' tok')}</td><td>{val(row.predicted_tokens,' tok')}</td><td>{val(row.output_tokens,' tok')}</td><td>{row.queue_seconds==null?'—':row.queue_seconds.toFixed(2)+'초'}</td><td>{row.first_seconds==null?'—':row.first_seconds.toFixed(2)+'초'}</td><td>{row.completion_seconds==null?'—':row.completion_seconds.toFixed(2)+'초'}</td><td>{row.outcome==='complete'?'완료':'입력 제한'}</td></tr>)}</tbody></table></div>}</section>

  <section className="panel actual-chart" id="actual-completions"><h2>실제 요청 결과</h2>{results.length?results.map((r,i)=><article className="real-result" key={i}><strong>{r.server_id} · 입력 {val(r.input_tokens)} · 예상 {val(r.predicted_output_tokens)} · 실제 {val(r.actual_output_tokens)} 토큰</strong><p>질문: {r.prompt}</p><p className="answer-preview">{r.answer||'답변 없음'}</p></article>):monitor.requests.some(r=>r.status==='completed')?monitor.requests.filter(r=>r.status==='completed').map(r=><article className="real-result" key={r.request_id}><strong>#{r.request_id.slice(0,8)} · {r.server_id} · 실제 출력 {val(r.actual_output_tokens)}토큰</strong><p>첫 응답 {val(r.first_response_seconds,'초')} · 완료 {val(r.completion_seconds,'초')}</p></article>):<p>완료된 실제 요청이 여기에 표시됩니다.</p>}</section>
  </>
}
