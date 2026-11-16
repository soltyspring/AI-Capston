import React, {useEffect, useState} from 'react'
import './routing-analysis.css'
import lengthComparison from './output-length-comparison.json'

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

function ErrorRateChart({measurement,candidate}) {
  const batches=measurement?.batches??[]
  const highest=Math.max(100,...batches.map(row=>row.rate))
  const ceiling=Math.ceil(highest/4/(highest>=400?100:25))*(highest>=400?100:25)*4
  const y=rate=>139-118*rate/ceiling
  const points=batches.map(({rate,end},i)=>({rate,end,x:60+i*466/Math.max(1,batches.length-1),y:y(rate)}))
  const last=points.at(-1)
  return <section className="routing-analysis-card request-share" aria-labelledby="error-rate-title">
    <div className="request-share-header">
      <div><h3 id="error-rate-title">100건당 예측 오차율</h3>
        {last?<p>처음 <strong>{measurement.first.toFixed(1)}%</strong> → 최근 <strong>{last.rate.toFixed(1)}%</strong></p>:<p>첫 100건의 자연 종료 응답을 모으는 중입니다.</p>}</div>
      <span>{measurement?format(measurement.total)+'건 수집':'수집 상태 확인 중'}</span>
    </div>
    {!!points.length&&<svg className="request-share-chart" viewBox="0 0 560 168" role="img" aria-label="100건마다 출력 길이 예측 오차율 선그래프">
      {[0,1,2,3,4].map(step=>{const value=step*ceiling/4;return <g key={step}>
        <line x1="52" x2="536" y1={y(value)} y2={y(value)} className="request-share-grid"/>
        <text x="46" y={y(value)} textAnchor="end" dominantBaseline="middle">{format(value)}%</text>
      </g>})}
      <polyline className="request-share-line" points={points.map(p=>p.x+','+p.y).join(' ')}/>
      {points.map((p,i)=><g key={p.end}>
        <circle cx={p.x} cy={p.y} r="3.5"><title>{format(p.end-99)}–{format(p.end)}번째 응답 · {p.rate.toFixed(1)}%</title></circle>
        {(i===0||i===points.length-1||i%Math.ceil(points.length/5)===0)&&<text x={p.x} y="160" textAnchor="middle">{format(p.end)}</text>}
      </g>)}
    </svg>}
    {measurement?.pending>0&&<p className="analysis-note">다음 구간 {measurement.pending}/100건</p>}
    <p className="analysis-note">상시 실험에서 자연 종료한 답변만 집계합니다. 이 그래프는 낮을수록 좋습니다.</p>
    {candidate&&<p><strong>개선 예측기 검증 · {format(candidate.samples)}건</strong><br/>
      같은 응답 기준 오차율: 기존 {format(candidate.baseline_wape)}% → 개선 {format(candidate.candidate_wape)}%</p>}
    <LengthFeatureComparison report={lengthComparison}/>
  </section>
}

function LengthFeatureComparison({report}) {
  if (!report) return null
  const scores=report.variants
  const delta=key=>{
    const diff=Number((scores[key].natural_mae-scores.full.natural_mae).toFixed(2))
    if (!diff) return '차이 없음'
    return `${format(Math.abs(diff))}토큰 ${diff>0?'감소':'증가'}`
  }
  return <section aria-labelledby="length-feature-title">
    <h4 id="length-feature-title">출력 예측 요소 비교</h4>
    <p className="analysis-note">동일 질문 검증 · 학습 {format(report.training_samples)}건 · 검증 {format(report.holdout_samples)}건 중 자연 종료 {format(report.verified_natural_holdout)}건</p>
    <DataTable columns={['비교 모델','자연 종료 평균 오차','전체 평균 오차 · 출력 한도 적용']}
      rows={Object.entries({original:'석진 님 기존 모델',previous_candidate:'이전 개선 모델',base:'기본 특징만 다시 학습',full:'주제·키워드·형식 모두 적용'})
        .filter(([key])=>scores[key]).map(([key,label])=>[label,`${format(scores[key].natural_mae)}토큰`,`${format(scores[key].capped_mae)}토큰`])}/>
    <DataTable columns={['비교한 요소','요소 제외 시 평균 오차','요소 추가 효과']}
      rows={Object.entries({without_topic:'주제',without_keywords:'키워드',without_format:'답변 형식'}).map(([key,label])=>[
        label,`${format(scores[key].natural_mae)}토큰`,delta(key)])}/>
    <p className="analysis-note">나머지 특징은 그대로 두고 해당 요소만 빼서 다시 학습했습니다. 효과는 합산할 수 없습니다. 상시 실험 데이터의 오프라인 비교이며, 운영 모델은 그대로입니다.</p>
  </section>
}

function DataTable({columns, rows}) {
  return <div className="routing-analysis-table"><table>
    <thead><tr>{columns.map(column=><th scope="col" key={column}>{column}</th>)}</tr></thead>
    <tbody>{rows.map(([label,...values],index)=><tr key={`${label}-${index}`}><th scope="row">{label}</th>{values.map((v,i)=><td key={i}>{v}</td>)}</tr>)}</tbody>
  </table></div>
}

function CycleChart({title,unit,results,series}) {
  const palette=['#2164d1','#21845b','#8364ca']
  const values=results.flatMap(row=>series.map(([,value])=>value(row))).filter(Number.isFinite)
  const max=Math.max(1,...values)*1.12
  const y=value=>132-108*value/max
  const x=i=>64+i*460/Math.max(1,results.length-1)
  return <section className="routing-analysis-card request-share">
    <h3>{title}</h3>
    {!values.length?<p className="analysis-note">첫 평가 구간 완료 후 그래프가 표시됩니다.</p>:<>
      <p>{series.map(([label],i)=><span key={label} style={{color:palette[i],marginInlineEnd:16}}>{label}</span>)}</p>
      <svg className="request-share-chart" viewBox="0 0 560 164" role="img" aria-label={title}>
        {[0,1,2,3,4].map(step=><g key={step}><line className="request-share-grid" x1="58" x2="534" y1={y(max*step/4)} y2={y(max*step/4)}/><text x="52" y={y(max*step/4)} textAnchor="end">{format(max*step/4)}{unit}</text></g>)}
        {series.map(([label,get],s)=>{const pts=results.map((row,i)=>[x(i),get(row),row.checkpoint]);return <g key={label}>
          <path fill="none" stroke={palette[s]} strokeWidth="2" d={pts.map(([px,v],i)=>Number.isFinite(v)?`${i&&Number.isFinite(pts[i-1][1])?'L':'M'}${px},${y(v)}`:'').join(' ')}/>
          {pts.filter(([,v])=>Number.isFinite(v)).map(([px,v,n])=><circle key={n} cx={px} cy={y(v)} r="4" style={{fill:palette[s]}}><title>{format(n)}건 · {label} {format(v)}{unit}</title></circle>)}
        </g>})}
        {results.map((row,i)=>(i===0||i===results.length-1||i%Math.ceil(results.length/7)===0)&&<text key={row.checkpoint} x={x(i)} y="155" textAnchor="middle">{format(row.checkpoint)}</text>)}
      </svg>
    </>}
  </section>
}

function FixedTrials({data}) {
  const labels={original:'기존 예측기',candidate:'개선 예측기',round_robin:'Round Robin',shortest_eval:'예상 완료 시간 최소',shadow_eval:'실험 LinUCB'}
  const value=(n,unit)=>Number.isFinite(n)?`${format(n)}${unit}`:'—'
  const timing=r=>[r.repeat,labels[r.variant??r.policy],value(r.ttft_p95,'초'),value(r.over_2s_pct,'%'),`${r.first_samples}/${r.attempted}건`]
  const columns=['회차','비교 대상','첫 응답 P95','2초 초과율','첫 응답 표본']
  return <section className="routing-analysis-card" aria-labelledby="fixed-trials-title">
    <h3 id="fixed-trials-title">고정 질문 · 실제 GPU 비교</h3>
    <p className="analysis-note">{data?`체크포인트 ${format(data.checkpoint)}건 · ${data.repeats}회 · ${({running:'실측 중',completed:'실측 완료',interrupted:'실측 중단'})[data.status]??'상태 확인 중'}`:'아직 실측 기록이 없습니다.'}</p>
    <h4>기존·개선 예측기 · 예상 완료 시간 최소 정책</h4>
    {data?.forecast?.length>0?<DataTable columns={[...columns,'동일 출력 평균 오차','동일 출력 표본']} rows={data.forecast.map(r=>[...timing(r),value(r.matched_mae,'토큰'),`${r.matched}건`])}/>:<p className="analysis-note">첫 비교가 완료되면 표시합니다.</p>}
    <h4>세 서버 혼잡 상태 · 짧은 질문</h4>
    {data?.congestion?.length>0?<DataTable columns={columns} rows={data.congestion.map(timing)}/>:<p className="analysis-note">첫 혼잡 테스트가 완료되면 표시합니다.</p>}
    <p className="analysis-note">첫 응답은 첫 답변 토큰 기준입니다. 2초 초과율은 첫 응답을 받은 요청 중 비율입니다.</p>
  </section>
}

function KoreanHoldout({data}) {
  const cell=n=>Number.isFinite(n)?`${format(n)} 토큰`:'—'
  return <section className="routing-analysis-card" aria-labelledby="korean-holdout-title">
    <h3 id="korean-holdout-title">한국어 질문 · 유형별 출력 예측 오차</h3>
    <p className="analysis-note">{data?`체크포인트 ${format(data.checkpoint)}건 · ${data.done}/${data.total}건 · ${({running:'실측 중',completed:'실측 완료',interrupted:'실측 중단'})[data.status]??'확인 중'}`:'실측 기록을 기다리는 중입니다.'}</p>
    {data?.results?.length>0&&<DataTable columns={['질문 유형','자연 종료 / 실측','기존 MAE','개선 MAE','형식 보정 MAE','출력 한도 도달']} rows={data.results.map(r=>[r.type,`${r.natural}/${r.total}건`,cell(r.original),cell(r.candidate),cell(r.calibrated),`${r.capped}건`])}/>}
    <p className="analysis-note">학습에 넣지 않은 질문입니다. 예측 오차는 자연 종료한 같은 답변으로 비교하며, 낮을수록 좋습니다.</p>
  </section>
}

function QueuedTrials({data}) {
  const names={round_robin:'Round Robin',shortest_eval:'예상 완료 시간 최소',shadow_eval:'실험 LinUCB'}
  const kind=e=>e?.kind==='engine'?'세 서버의 vLLM 대기열':e?.kind==='router'?'세 서버 슬롯 포화 · 로드밸런서 대기열':'미확인'
  const metric=(n,u)=>Number.isFinite(n)?`${n}${u}`:'—'
  return <section className="routing-analysis-card" aria-labelledby="queued-trials-title">
    <h3 id="queued-trials-title">대기열이 쌓인 상태 · 짧은 질문 비교</h3>
    <p className="analysis-note">{data?`체크포인트 ${format(data.checkpoint)}건 · ${({running:'실측 중',completed:'실측 완료',interrupted:'실측 중단'})[data.status]??'확인 중'}`:'실측 기록을 기다리는 중입니다.'}</p>
    {data?.ramp?.length>0&&<DataTable columns={['동시 긴 요청','확인된 대기열','서버별 vLLM 최대 대기']} rows={data.ramp.map(r=>[r.load,kind(r.evidence),Object.entries(r.peak).map(([k,v])=>`${k} ${v}건`).join(' · ')])}/>}
    {data?.results?.length>0&&<DataTable columns={['회차','정책','첫 응답 P95','2초 초과율','첫 응답 표본','로드밸런서 대기 P95']} rows={data.results.map(r=>[r.repeat,names[r.policy],metric(r.ttft_p95,'초'),metric(r.over_2s_pct,'%'),`${r.first_samples}/${r.attempted}건`,metric(r.router_wait_p95,'초')])}/>}
    {data?.kind==='router'&&<p className="analysis-note">세 서버에 남은 슬롯이 없어 요청이 로드밸런서에서 대기한 조건입니다. 서버 내부 대기열과는 구분합니다.</p>}
    <p className="analysis-note">개선 후보: 로드밸런서 대기가 지연의 대부분이라면 짧은 요청에 일부 슬롯을 예약하고, 긴 요청의 최대 대기 시간도 함께 확인합니다.</p>
  </section>
}

function EvaluationCycles({data}) {
  const [metric,setMetric]=useState('first_p95')
  const results=data?.results??[]
  const latest=results.at(-1)
  const policies=[['Round Robin','round_robin'],['예상 완료 시간 최소','shortest_eval'],['실험 LinUCB','shadow_eval']]
  return <section aria-labelledby="cycle-evaluation-title">
    <h3 id="cycle-evaluation-title">체크포인트별 개선 추이</h3>
    <p className="analysis-note">{data?(data.phase==='finished'?'실험 요청 재생 완료':`체크포인트 ${format(data.checkpoint)}건 · ${data.phase==='evaluation'?(data.frozen?'학습 없이 평가 중':'모델 저장 중'):'학습 구간 수집 중'} · ${format(data.done)}/${format(data.window??100)}건`):'실험 진행 상태 확인 중'}</p>
    <CycleChart title="출력 토큰 예측 · 평균 절대 오차" unit="토큰" results={results}
      series={[["기존 예측기",r=>r.prediction.original],["형식·키워드 예측기",r=>r.prediction.candidate]]}/>
    <p className="analysis-note">자연 종료한 같은 응답으로 비교{latest?` · 최근 ${format(latest.prediction.samples)}건`:''}</p>
    <label>배정 정책 지표 <select value={metric} onChange={event=>setMetric(event.target.value)}>
      <option value="first_p95">첫 응답 P95</option><option value="completion_p95">완료 P95</option><option value="failures">실패 건수</option>
    </select></label>
    <CycleChart title="서버 배정 정책 · 3개 비교" unit={metric==='failures'?'건':'초'} results={results}
      series={policies.map(([label,key])=>[label,r=>r.policies[key]?.[metric]])}/>
    <p className="analysis-note">같은 예측기·질문·동시 요청 수·출력 한도로 실제 GPU에서 비교합니다.</p>
  </section>
}

function ExperimentTrend({label,unit,points,current}) {
  const shown=points.filter(point=>Number.isFinite(point.value))
  const values=shown.map(point=>point.value)
  const hi=Math.max(0,...values),lo=Math.min(0,...values)
  const y=value=>76-68*(value-lo)/(hi-lo||1)
  const end=Math.max(points.at(-1)?.count??1000,current??0)
  const ticks=Array.from({length:end/1000},(_,i)=>(i+1)*1000)
  const width=Math.max(320,48+Math.max(0,ticks.length-1)*46)
  const x=count=>24+(count-1000)*(width-48)/Math.max(1000,end-1000)
  const latest=shown.at(-1)
  return <div className="routing-analysis-card"><h4>{label}</h4>{latest?<>
    <p><strong>{latest.value>0?'+':''}{latest.value.toFixed(2)} {unit}</strong> · {format(latest.count)}건 체크포인트{latest.actual!==latest.count&&` (실측 시 ${format(latest.actual)}건)`}</p>
    <div className="experiment-trend-scroll" tabIndex={0} role="region" aria-label={`${label} · 체크포인트 그래프`}>
    <svg className="experiment-trend" role="img" aria-label={label} viewBox={`0 0 ${width} 108`} style={{minWidth:width}}>
      <line x1="24" x2={width-24} y1={y(0)} y2={y(0)} className="experiment-axis"/>
      {ticks.map(tick=><g key={tick}><line x1={x(tick)} x2={x(tick)} y1="84" y2="89" className="experiment-axis"/>
        <text x={x(tick)} y="103" textAnchor="middle">{format(tick)}</text></g>)}
      {shown.slice(1).map((p,i)=>
        <line key={p.count} x1={x(shown[i].count)} y1={y(shown[i].value)} x2={x(p.count)} y2={y(p.value)}
          className={`experiment-segment${p.count-shown[i].count>1000?' experiment-segment-gap':''}`}/>)}
      {shown.map(p=><circle key={p.count} cx={x(p.count)} cy={y(p.value)} r="4" fill={p.value<0?'#a43343':'var(--analysis-blue)'}>
        <title>{format(p.count)}건 체크포인트 (실측 시 {format(p.actual)}건): {p.value.toFixed(2)} {unit}</title></circle>)}
    </svg>
    </div>
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
          setLive({...payload.analysis,shadow:payload.shadow_linucb,comparison:payload.shadow_comparison,gpuActivity:payload.gpu_activity,predictionError:payload.prediction_error,predictionCandidate:payload.prediction_candidate,evaluationCycles:payload.evaluation_cycles})
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
  const trend=(comparison?.trend??[]).filter(run=>Number.isInteger(run.checkpoint)&&run.checkpoint>0&&run.checkpoint%1000===0).map(run=>{
    const sample=run.results.find(r=>r.scenario==='mixed'&&r.policy===trendPolicy)
    const baseline=run.results.find(r=>r.scenario==='mixed'&&r.policy==='production_eval')
    const saved=run.savings.find(r=>r.policy===trendPolicy)
    return {count:run.checkpoint,actual:Object.values(run.training_samples??{}).reduce((a,b)=>a+b,0),
      token:sample?.token_wape!=null&&sample?.fixed_wape!=null?sample.fixed_wape-sample.token_wape:null,
      p95:sample?.ttft_p95!=null&&baseline?.ttft_p95>0?100*(baseline.ttft_p95-sample.ttft_p95)/baseline.ttft_p95:null,
      time:saved?.matched?saved.total_seconds_saved*100/saved.matched:null,
      slots:saved?.matched?saved.slot_seconds_saved*100/saved.matched:null}
  }).sort((a,b)=>a.count-b.count)
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
      <p className="analysis-note">장애·SLA · 최근 30일 · 약 3초마다 갱신
        {monitorSummary?.checked_at&&` · 마지막 집계 ${new Date(monitorSummary.checked_at*1000).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul'})}`}
      </p>
      <div className="routing-analysis-table"><table><thead><tr><th>장애·SLA 기준</th><th>건수</th></tr></thead><tbody>
        <tr><th>완료 30초 초과</th><td>{monitorSummary?.failure_criteria?.completion_over_30_seconds??'—'}</td></tr>
        <tr><th>대기열 초과 거절</th><td>{monitorSummary?.failure_criteria?.queue_overflow??'—'}</td></tr>
        <tr><th>대기 시간 초과</th><td>{monitorSummary?.failure_criteria?.queue_timeout??'—'}</td></tr>
        <tr><th>GPU 메모리 95% 이상 + 대기 발생</th><td>{monitorSummary?.failure_criteria?.gpu_memory_backlog_episodes??'—'}회</td></tr>
        <tr><th>첫 토큰 2초 초과</th><td>{monitorSummary?.failure_criteria?.first_token_over_2_seconds??'—'}</td></tr>
      </tbody></table></div>
      <p className="analysis-note">요청 지표는 실제 운영 요청 기준입니다. GPU 메모리와 대기는 전체 서버 상태에서 관측한 횟수입니다.</p>
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
    <EvaluationCycles data={live.evaluationCycles}/>
    <FixedTrials data={comparison?.fixed_trials}/>
    <QueuedTrials data={comparison?.queued_trials}/>
    <KoreanHoldout data={comparison?.korean_holdout}/>
    {trend.length>0&&<section className="routing-analysis-card"><h3>이전 1,000건 단위 비교</h3>
      <p>학습 1,000건 간격으로 표시합니다. 점은 실측값이고, 점선은 그 사이에 실측이 없는 구간입니다. 높을수록 좋으며, 시간과 슬롯은 실제 출력 길이가 같은 요청 100건 기준입니다.</p>
      <label>비교 정책 <select value={trendPolicy} onChange={event=>setTrendPolicy(event.target.value)}>
        {['work_eval','shortest_eval','shadow_eval'].map(policy=><option key={policy} value={policy}>{shadowName(policy)}</option>)}
      </select></label>
      <ExperimentTrend label="출력 토큰 오차 감소 · 고정 400토큰 대비" unit="%p" current={comparison?.status==='running'?comparison.checkpoint:undefined} points={trend.map(p=>({...p,value:p.token}))}/>
      <ExperimentTrend label="첫 응답 P95 개선 · 혼합 부하" unit="%" current={comparison?.status==='running'?comparison.checkpoint:undefined} points={trend.map(p=>({...p,value:p.p95}))}/>
      <ExperimentTrend label="로드밸런서로 절약한 요청 완료 시간" unit="초" current={comparison?.status==='running'?comparison.checkpoint:undefined} points={trend.map(p=>({...p,value:p.time}))}/>
      <ExperimentTrend label="절약한 GPU 서버 슬롯 점유 시간" unit="슬롯·초" current={comparison?.status==='running'?comparison.checkpoint:undefined} points={trend.map(p=>({...p,value:p.slots}))}/>
      <p className="analysis-note">출력 오차는 자연 종료만 집계합니다. 슬롯 점유 시간은 GPU 연산 시간이나 비용과 다릅니다.</p>
    </section>}
    <ErrorRateChart measurement={live.predictionError} candidate={live.predictionCandidate}/>
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
