import React, {useEffect, useState} from 'react'
import './routing-analysis.css'

const controlApi = `${import.meta.env.BASE_URL}api/control`
const policyLabels = {linucb:'LinUCB',round_robin:'Round Robin',shortest_predicted:'예상 완료 시간 최소'}

// Read-only snapshot of the live router's SQLite outcomes, grouped in KST.
// Contains aggregate counts and token statistics only.
const days = [
  ['11월 9일', 131, 495.59, 531.90, 52],
  ['11월 10일', 346, 496.55, 650.58, 262],
  ['11월 11일', 945, 552.69, 562.56, 896],
  ['11월 15일', 12, 175.50, 329.00, 9],
]
const servers = [
  ['ai', 546, 559.89, 464], ['ai1', 149, 704.05, 114], ['ai2', 739, 474.31, 641],
]
const policies = [
  ['6건', 106, 103, .86, .86],
  ['12건', 160, 161, .92, .92],
  ['15건', 322, 320, 1.09, 1.09],
  ['18건', 456, 447, 1.23, 1.22],
  ['19건', 480, 484, 1.26, 1.26],
  ['21건', 554, 562, 1.33, 1.34],
]
const format = value => value.toLocaleString('ko-KR', {maximumFractionDigits:2})
const totalRequests = days.reduce((total, [,count])=>total + count, 0)

// Completed requests in completion order, grouped by 100.
// [sum of absolute token error, sum of actual output tokens]
const errorBatches = [[50656,64957],[53067,89126],[50410,96467],[44906,72000],[49850,94540],[47863,76847],[49723,90824],[57392,90163],[57597,90551],[57578,90349],[57597,90551],[57578,90349],[57597,90551],[57578,90349]]

function ErrorRateChart() {
  const points = errorBatches.map(([error,actual],i)=>{
    const rate = error / actual * 100
    return {label:`${i*100+1}–${(i+1)*100}`, rate, x:48+i*37, y:139-rate*1.18}
  })
  const first = points[0], last = points.at(-1)
  const summary = points.map(({label,rate})=>`${label}번째 요청: ${rate.toFixed(1)}%`).join('; ')
  return <section className="routing-analysis-card request-share" aria-labelledby="error-rate-title">
    <div className="request-share-header">
      <div><h3 id="error-rate-title">100건당 예측 오차율</h3>
        <p>처음 <strong>{first.rate.toFixed(1)}%</strong> → 최근 <strong>{last.rate.toFixed(1)}%</strong></p></div>
      <span>1,400건</span>
    </div>
    <svg className="request-share-chart" viewBox="0 0 560 168" role="img" aria-label="100건마다 출력 길이 예측 오차율 선그래프" aria-describedby="error-rate-values">
      <desc id="error-rate-values">{summary}</desc>
      {[0,25,50,75,100].map(value=>{
        const y=139-value*1.18
        return <g key={value}>
          <line x1="42" x2="536" y1={y} y2={y} className="request-share-grid"/>
          <text x="34" y={y} textAnchor="end" dominantBaseline="middle">{value}%</text>
        </g>
      })}
      <polyline className="request-share-line" points={points.map(({x,y})=>`${x},${y}`).join(' ')}/>
      {points.map(({label,rate,x,y},i)=><g key={label}>
        <circle cx={x} cy={y} r="3.5"><title>{label}번째 요청 · {rate.toFixed(1)}%</title></circle>
        {(i===0||i===points.length-1)&&<text className="request-share-value" x={x} y={y-10} textAnchor={i===0?'start':'end'}>{rate.toFixed(1)}%</text>}
        {i%2===0&&<text x={x} y="160" textAnchor="middle">{(i+1)*100}</text>}
      </g>)}
    </svg>
    <p className="analysis-note">예측기는 재학습하지 않아요. 변화는 질문 구성 차이예요.</p>
  </section>
}

function DataTable({columns, rows}) {
  return <div className="routing-analysis-table"><table>
    <thead><tr>{columns.map(column=><th scope="col" key={column}>{column}</th>)}</tr></thead>
    <tbody>{rows.map(([label,...values],index)=><tr key={`${label}-${index}`}><th scope="row">{label}</th>{values.map((v,i)=><td key={i}>{v}</td>)}</tr>)}</tbody>
  </table></div>
}

function ExperimentTrend({label,unit,points}) {
  const shown=points.filter(point=>Number.isFinite(point.value))
  const values=shown.map(point=>point.value)
  const hi=Math.max(0,...values),lo=Math.min(0,...values)
  const y=value=>76-68*(value-lo)/(hi-lo||1)
  const x=index=>18+index*284/Math.max(1,shown.length-1)
  const latest=shown.at(-1)
  return <div className="routing-analysis-card"><h4>{label}</h4>{latest?<>
    <p><strong>{latest.value>0?'+':''}{latest.value.toFixed(2)} {unit}</strong> · {latest.count.toLocaleString()}건 학습 기준</p>
    <svg role="img" aria-label={label} viewBox="0 0 320 86" style={{width:'100%',maxHeight:130}}>
      <line x1="12" x2="308" y1={y(0)} y2={y(0)} stroke="#cbd5e1"/>
      {shown.length>1&&<polyline points={shown.map((p,i)=>`${x(i)},${y(p.value)}`).join(' ')} fill="none" stroke="#20835b" strokeWidth="2"/>}
      {shown.map((p,i)=><circle key={`${p.count}-${i}`} cx={x(i)} cy={y(p.value)} r="4" fill={p.value<0?'#a43343':'#20835b'}><title>{p.count}건: {p.value.toFixed(2)} {unit}</title></circle>)}
    </svg>
  </>:<p>비교 실측이 쌓이면 표시합니다.</p>}</div>
}

function durations(value) {
  if(!Number.isFinite(value))return '수집 중'
  const s=Math.floor(value)
  return `${Math.floor(s/3600)}시간 ${Math.floor(s%3600/60)}분 ${s%60}초`
}

// [range, requests, MAE, error rate %, under-predicted %, avg predicted, avg actual]
const errorGroups = {
  input: {label:'입력 길이별', unit:'입력', rows:[
    ['30토큰 미만',417,424.7,70.1,63.3,427,606],
    ['30–49토큰',848,570.8,64.1,99.1,327,891],
    ['50–99토큰',102,628.0,37.8,91.2,1146,1659],
    ['100토큰 이상',67,536.7,74.0,32.8,937,725],
  ]},
  output: {label:'실제 출력 길이별', unit:'실제 출력', rows:[
    ['100토큰 미만',146,419.1,716.5,8.9,469,58],
    ['100–299토큰',150,184.3,128.8,60.0,204,143],
    ['300–599토큰',17,278.8,70.1,41.2,463,398],
    ['600–999토큰',535,422.0,51.0,98.3,415,827],
    ['1,000토큰 이상',586,753.9,59.2,99.5,522,1274],
  ]},
}

function ErrorBreakdown() {
  const [mode,setMode] = useState('input')
  const group = errorGroups[mode]
  const largest = Math.max(...group.rows.map(row=>row[2]))
  return <section className="routing-analysis-card" aria-labelledby="error-breakdown-title">
    <h3 id="error-breakdown-title">구간별 오차</h3>
        <div className="routing-analysis-controls" role="group" aria-label="오차 분석 기준">
      {Object.entries(errorGroups).map(([key,item])=><button type="button" key={key} aria-pressed={mode===key} onClick={()=>setMode(key)}>{item.label}</button>)}
    </div>
    <div className="routing-analysis-bars" aria-label={`${group.label} 평균 절대 오차`}>
      {group.rows.map(([range,,mae])=><div key={range}>
        <span>{range}</span><strong>{format(mae)} 토큰</strong>
        <span className="analysis-track" aria-hidden="true"><i style={{width:`${mae/largest*100}%`}}/></span>
      </div>)}
    </div>
    <DataTable columns={[group.unit+' 구간','요청','MAE','오차율','과소 예측','평균 예측 / 실제']}
      rows={group.rows.map(([range,n,mae,rate,under,pred,actual])=>[range,format(n),format(mae),`${rate}%`,`${under}%`,`${format(pred)} / ${format(actual)}`])}/>
    <p className="analysis-note">{mode==='input'
      ? '입력 길이와 오차는 비례하지 않아요.'
      : '긴 답변일수록 짧게 예측해요.'}
      </p>
  </section>
}

export default function RoutingAnalysis() {
  const [group,setGroup] = useState('day')
  const [trendPolicy,setTrendPolicy]=useState('work_eval')
  const [live,setLive] = useState({summary:null,replay:null,benchmarks:null})
  useEffect(()=>{
    let alive=true,timer,controller
    const poll=async()=>{
      controller=new AbortController()
      try{
        const response=await fetch(controlApi,{method:'POST',credentials:'same-origin',cache:'no-store',
          headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'monitor_dashboard',hours:24,limit:60}),signal:controller.signal})
        if(response.ok&&alive){
          const payload=await response.json()
          setLive({...payload.analysis,shadow:payload.shadow_linucb,comparison:payload.shadow_comparison,gpuActivity:payload.gpu_activity})
        }
      }catch{}
      finally{if(alive)timer=setTimeout(poll,3000)}
    }
    poll()
    return()=>{alive=false;clearTimeout(timer);controller?.abort()}
  },[])
  const monitorSummary=live.summary
  const estimate=live.replay
  const benchmark=live.benchmarks
  const scheduledDate=benchmark?.next_run_at?new Date(benchmark.next_run_at*1000).toLocaleString('ko-KR',{dateStyle:'medium',timeStyle:'short'}):null
  const latestBenchmark=benchmark?.runs?.find(run=>run.status==='completed')
  const testedPolicies=[...new Set(latestBenchmark?.groups?.map(row=>row.policy)??[])]
  const plannedPolicies=benchmark?.policies??Object.keys(policyLabels)
  const comparison=live.comparison
  const shadowName=policy=>({shadow_eval:'실험 LinUCB',work_eval:'작업량 기반 LinUCB',shortest_eval:'예상 완료 시간 최소'}[policy]??'운영 LinUCB')
  const seconds=value=>Number.isFinite(value)?`${value.toFixed(2)}초`:'—'
  const trend=(comparison?.trend??[]).map(run=>{
    const sample=run.results.find(r=>r.scenario==='mixed'&&r.policy===trendPolicy)
    const baseline=run.results.find(r=>r.scenario==='mixed'&&r.policy==='production_eval')
    const saved=run.savings.find(r=>r.policy===trendPolicy)
    return {count:Object.values(run.training_samples??{}).reduce((a,b)=>a+b,0),
      token:sample?.token_wape!=null&&sample?.fixed_wape!=null?sample.fixed_wape-sample.token_wape:null,
      p95:sample?.ttft_p95!=null&&baseline?.ttft_p95>0?100*(baseline.ttft_p95-sample.ttft_p95)/baseline.ttft_p95:null,
      time:saved?.matched?saved.total_seconds_saved*100/saved.matched:null,
      slots:saved?.matched?saved.slot_seconds_saved*100/saved.matched:null}
  })
  return <div className="routing-analysis" id="analysis">
    <section className="routing-analysis-intro" aria-labelledby="routing-analysis-title">
      <span className="routing-analysis-kicker">QLess · 실측 기록과 재생 추정</span>
      <h2 id="routing-analysis-title">로드밸런서 상세 분석</h2>
      <small>실측값과 추정값을 구분해 표시합니다.</small>
    </section>
    <section className="routing-analysis-card">
      <h3>운영 기록 · 실측</h3>
      <p>요청 기록과 GPU 서버 자원을 3초마다 저장합니다. 질문 원문은 기록하지 않습니다.</p>
      <dl className="routing-analysis-kpis">
        <div><dt>실제 요청 · 최근 30일</dt><dd>{monitorSummary?.total_requests?.toLocaleString('ko-KR')??'—'}<small>건</small></dd></div>
        <div><dt>완료 요청</dt><dd>{monitorSummary?.completed_requests?.toLocaleString('ko-KR')??'—'}<small>건</small></dd></div>
        <div><dt>자원 표본</dt><dd>{monitorSummary?.resource_samples?.toLocaleString('ko-KR')??'—'}<small>개</small></dd></div>
      </dl>
      <div className="routing-analysis-table"><table><thead><tr><th>장애·SLA 기준</th><th>건수</th></tr></thead><tbody>
        <tr><th>완료 30초 초과</th><td>{monitorSummary?.failure_criteria?.completion_over_30_seconds??'—'}</td></tr>
        <tr><th>대기열 초과 거절</th><td>{monitorSummary?.failure_criteria?.queue_overflow??'—'}</td></tr>
        <tr><th>대기 시간 초과</th><td>{monitorSummary?.failure_criteria?.queue_timeout??'—'}</td></tr>
        <tr><th>GPU 메모리 95% 이상 + 대기 발생</th><td>{monitorSummary?.failure_criteria?.gpu_memory_backlog_episodes??'—'}회</td></tr>
        <tr><th>첫 토큰 2초 초과</th><td>{monitorSummary?.failure_criteria?.first_token_over_2_seconds??'—'}</td></tr>
      </tbody></table></div>
      <p className="analysis-note">자원은 서버별 CPU·일반 메모리·GPU·GPU 메모리·대기/실행 요청을 3초 간격으로 수집하며 45일 보관합니다.</p>
    </section>
    <section className="routing-analysis-card">
      <h3>같은 운영 요청 재생 · 추정</h3>
      {estimate?.available?<>
        <p>{estimate.request_count.toLocaleString('ko-KR')}건의 실제 입력·출력 길이와 서버별 측정 처리 속도로 재생한 결과입니다.</p>
        <dl className="routing-analysis-kpis">
          <div><dt>누적 지연 감소 · 추정</dt><dd>{estimate.estimated_cumulative_delay_reduction_seconds.toLocaleString('ko-KR')}<small>초</small></dd></div>
          <div><dt>첫 응답 2초 초과 감소 · 추정</dt><dd>{estimate.estimated_first_token_violations_avoided.toLocaleString('ko-KR')}<small>건</small></dd></div>
          <div><dt>요청 재생 표본</dt><dd>{estimate.request_count.toLocaleString('ko-KR')}<small>건</small></dd></div>
        </dl>
        <DataTable columns={['재생 정책','필요 GPU 수 · 추정','3대일 때 P95 첫 응답','3대일 때 평균 완료']}
          rows={[
            ['Round Robin',estimate.required_servers_for_p95_2s.round_robin==null?'3대 초과':`${estimate.required_servers_for_p95_2s.round_robin}대`,`${estimate.scenarios.round_robin_3_servers.p95_first_response_seconds}초`,`${estimate.scenarios.round_robin_3_servers.average_completion_seconds}초`],
            ['예상 완료 비용 최소',estimate.required_servers_for_p95_2s.estimated_completion_cost==null?'3대 초과':`${estimate.required_servers_for_p95_2s.estimated_completion_cost}대`,`${estimate.scenarios.estimated_completion_cost_3_servers.p95_first_response_seconds}초`,`${estimate.scenarios.estimated_completion_cost_3_servers.average_completion_seconds}초`],
          ]}/>
        <p className="analysis-note">{estimate.method} 필요 서버 수는 기록된 요청 흐름에서 P95 첫 응답 2초 이내를 만족하는 최소 수입니다.</p>
      </>:<p>{estimate?.reason??'최근 운영 요청을 수집하는 중입니다.'}</p>}
    </section>
    <section className="routing-analysis-card">
      <h3>정기 동일 질문 비교 · 실제 GPU 실측</h3>
      {latestBenchmark?<>
        <p>최근 실측 {new Date(latestBenchmark.completed_at*1000).toLocaleString('ko-KR')} · {testedPolicies.map(policy=>policyLabels[policy]??policy).join(' / ')}</p>
        <DataTable columns={['정책','동시 요청','완료','실패','첫 응답 평균 / P95','완료 평균 / P95','첫 응답 2초 이내','순환 대체']}
          rows={latestBenchmark.groups.map(row=>[policyLabels[row.policy]??row.policy,`${row.concurrency}건`,`${row.completed}/${row.requests}`,row.failures,
            row.average_first_response_seconds==null?'—':`${row.average_first_response_seconds} / ${row.p95_first_response_seconds}초`,
            row.average_completion_seconds==null?'—':`${row.average_completion_seconds} / ${row.p95_completion_seconds??'—'}초`,`${row.under_2s_count}/${row.requests}건`,row.fallback_count==null?'—':`${row.fallback_count}건`])}/>
      </>:null}
      <p className="analysis-note">{testedPolicies.length < plannedPolicies.length ? '세 정책 비교 예정' : '다음 비교'}: {scheduledDate??'스케줄 확인 중'} · {plannedPolicies.map(policy=>policyLabels[policy]??policy).join(' / ')}</p>
      {benchmark?.last_error&&<p className="analysis-note">최근 테스트 상태: {benchmark.last_error}</p>}
      <p className="analysis-note">운영 LinUCB 학습은 비교 테스트 요청에서 일시 중지합니다. 테스트는 3대 서버에서 실제 응답을 생성합니다.</p>
    </section>
    <section className="routing-analysis-card" aria-label="GPU 서버 요청 가동 시간">
      <h3>GPU 서버 요청 가동 시간</h3><dl className="analysis-summary">
        <div><dt>집계 시작 후</dt><dd>{durations(live.gpuActivity?.total_seconds)}</dd></div>
        <div><dt>최근 24시간</dt><dd>{durations(live.gpuActivity?.last_24h_seconds)}</dd></div>
      </dl><p className="analysis-note">한 대라도 GPU 서버가 요청을 처리한 구간을 한 번만 집계합니다. A가 0~3초, B가 2~7초에 처리하면 총 7초입니다. 유휴 시간과 로드밸런서 대기 시간은 제외합니다.</p>
    </section>
    <section className="routing-analysis-card" id="shadow-comparison">
      <h3>로드밸런서 정책 비교 · 실제 GPU</h3>
      <p>실험 모델 학습: {live.shadow?Object.entries(live.shadow).map(([server,count])=>`${server} ${count.toLocaleString()}건`).join(' · '):'연결 상태 확인 중'}</p>
      {comparison?.status==='running'&&<p role="status">실측 중 · {comparison.rounds.length}/{comparison.planned_rounds}회 완료{comparison.current?` · ${shadowName(comparison.current.policy)} · 동시 ${comparison.current.load}건 중 ${comparison.current.completed}건 완료 · 중앙 대기 최대 ${comparison.current.peak_router_wait}건`:''}</p>}
      {comparison?.status==='interrupted'&&<p role="status">실측이 중단되었습니다. 완료한 구간까지만 표시합니다.</p>}
      {comparison?.status==='completed'&&<p>실측 완료 · {comparison.version===2?'동시 19·21건 및 긴 작업 중 짧은 요청 유입을 같은 조건으로 반복했습니다.':'같은 요청을 동시 19·21건으로 정책마다 두 번 반복했습니다.'}</p>}
      {!comparison&&<p>별도 모델을 학습 중입니다. 실측을 시작하면 진행 상황이 여기에 표시됩니다.</p>}
      {!!(comparison?.results?.length||comparison?.rounds?.length)&&<DataTable
        columns={['정책','부하 조건','완료','첫 응답 P95','첫 응답 2초 초과','완료 P95','중앙 대기 P95','실제 출력 평균','생성 ms/토큰','출력 오차율']}
        rows={(comparison.results.length?comparison.results:comparison.rounds).map(row=>[
          shadowName(row.policy),row.scenario==='mixed'?'긴 작업 + 짧은 요청':`동시 ${row.load}건`,`${row.completed}/${row.attempted}`,
          seconds(row.ttft_p95),row.first_over_2s??'—',seconds(row.completion_p95),seconds(row.router_wait_p95),row.mean_output_tokens??'—',row.decode_ms_per_token??'—',
          row.token_wape==null?'—':`${row.token_wape.toFixed(1)}% · ${row.natural_count}건`])}/>}
      <p className="analysis-note">비교 요청은 모델 학습에 반영하지 않습니다.</p>
    </section>
    <section className="routing-analysis-card"><h3>체크포인트별 개선 추이</h3>
      <p>위로 갈수록 개선입니다. 시간과 슬롯은 실제 출력 길이가 같은 요청 100건 기준으로 환산합니다.</p>
      <label>비교 정책 <select value={trendPolicy} onChange={event=>setTrendPolicy(event.target.value)}>
        {['work_eval','shortest_eval','shadow_eval'].map(policy=><option key={policy} value={policy}>{shadowName(policy)}</option>)}
      </select></label>
      <ExperimentTrend label="출력 토큰 오차 감소 · 고정 400토큰 대비" unit="%p" points={trend.map(p=>({...p,value:p.token}))}/>
      <ExperimentTrend label="첫 응답 P95 개선 · 혼합 부하" unit="%" points={trend.map(p=>({...p,value:p.p95}))}/>
      <ExperimentTrend label="로드밸런서로 절약한 요청 완료 시간" unit="초" points={trend.map(p=>({...p,value:p.time}))}/>
      <ExperimentTrend label="절약한 GPU 서버 슬롯 점유 시간" unit="슬롯·초" points={trend.map(p=>({...p,value:p.slots}))}/>
      <p className="analysis-note">출력 오차는 자연 종료만 집계합니다. 슬롯 점유 시간은 GPU 연산 시간이나 비용과 다릅니다.</p>
    </section>
    <ErrorRateChart/>
    <ErrorBreakdown/>
    <section className="routing-analysis-card" aria-label="분석 요약">
      <dl className="routing-analysis-kpis">
        <div><dt>완료 요청</dt><dd>{format(totalRequests)}<small>건</small></dd></div>
        <div><dt>평균 오차</dt><dd>530.77<small>토큰</small></dd></div>
        <div><dt>짧게 예측</dt><dd>85.0<small>%</small></dd></div>
      </dl>
    </section>
    <section className="routing-analysis-card">
      <h3>출력 길이 예측</h3>
      <p>고정 400토큰보다 오차 <strong>8.3% 감소</strong></p>
      <div className="routing-analysis-bars" aria-label="방식별 평균 절대 오차">
        {[['출력 예측 모델',530.77],['고정 400토큰',579.04]].map(([label,value])=><div key={label}>
          <span>{label}</span><strong>{format(value)} 토큰</strong>
          <span className="analysis-track" aria-hidden="true"><i style={{width:`${value/600*100}%`}}/></span>
        </div>)}
      </div>
      <div className="routing-analysis-controls" role="group" aria-label="집계 기준">
        <button type="button" aria-pressed={group==='day'} onClick={()=>setGroup('day')}>날짜별</button>
        <button type="button" aria-pressed={group==='server'} onClick={()=>setGroup('server')}>서버별</button>
      </div>
      <DataTable columns={group==='day'?['날짜','완료 요청','모델 MAE','고정 400 MAE','과소 예측']:['서버','완료 요청','모델 MAE','과소 예측']}
        rows={group==='day'?days.map(([date,n,mae,fixed,under])=>[date,format(n),format(mae),format(fixed),format(under)]):
          servers.map(([id,n,mae,under])=>[id,format(n),format(mae),format(under)])}/>
      <p className="analysis-note">11월 15일은 12건뿐이에요.</p>
    </section>
    <section className="routing-analysis-card">
      <h3>이전 수동 비교 · 실측</h3>
      <p>같은 질문 248건씩 · 실제 GPU 3대 · 2026.11.09–11.15</p>
      <DataTable columns={['동시 요청','LinUCB 첫 응답 (ms)','Round Robin 첫 응답 (ms)','LinUCB 완료 (초)','Round Robin 완료 (초)']}
        rows={policies.map(([n,a,b,c,d])=>[n,a,b,c.toFixed(2),d.toFixed(2)])}/>
      <p className="analysis-note">두 정책 차이는 거의 없어요. 19건부터 대기가 생겨요.</p>
    </section>
    <section className="routing-analysis-card">
      <h3>학습 여부</h3>
      <dl className="routing-analysis-learning">
        <div><dt>LinUCB 서버 배정</dt><dd>처리 결과로 계속 학습해요.</dd></div>
        <div><dt>출력 길이 예측</dt><dd>재학습하지 않아요.</dd></div>
      </dl>
          </section>
  </div>
}
