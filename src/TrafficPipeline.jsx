import React, {useEffect, useRef, useState} from 'react'
import {queuePathLength, queuePoint, visibleQueue} from './trafficGeometry.js'

const COLORS={A:'#1a8c67',B:'#427bbb',C:'#8669b5'}

// The API owns queue membership/order. Only travel between those slots is animated.
export default function TrafficPipeline({state,dotScale=1}) {
  const dots=useRef(new Map())
  const pending=useRef([])
  const incoming=useRef(null)
  const lastId=useRef(state.decisions.at(-1)?.id ?? -1)
  const [picture,setPicture]=useState({dots:[],incoming:null})
  useEffect(()=>{
    for(const server of state.servers) {
      const waiting=new Set((server.queued_requests || []).map(job=>job.id))
      const visible=visibleQueue(server)
      const ids=new Set(visible.map(job=>job.id))
      for(const [key,dot] of dots.current) {
        if(dot.server!==server.id || ids.has(dot.id)) continue
        if(waiting.has(dot.id)) dots.current.delete(key)
        else {dot.exiting=true;dot.target=0}
      }
      visible.forEach((job,index)=>{
        const key=`${server.id}-${job.id}`
        const previous=dots.current.get(key)
        if(previous) {previous.target=job.distance;previous.exiting=false}
        else dots.current.set(key,{id:job.id,server:server.id,distance:queuePathLength(server.id)+index*20,target:job.distance,exiting:false})
      })
    }
    for(const decision of state.decisions) {
      if(decision.id>lastId.current) pending.current.push(decision.id)
    }
    lastId.current=Math.max(lastId.current,state.decisions.at(-1)?.id ?? -1)
  },[state.servers,state.decisions])
  useEffect(()=>{
    if(!state.running) return
    let handle,previous=performance.now()
    const tick=now=>{
      const delta=Math.min(80,now-previous)
      previous=now
      for(const [key,dot] of dots.current) {
        const gap=dot.target-dot.distance
        dot.distance+=Math.sign(gap)*Math.min(Math.abs(gap),delta*.24)
        if(dot.exiting && dot.distance<=0) dots.current.delete(key)
      }
      // One arrival packet at a time, even for a simultaneous batch.
      if(!incoming.current && pending.current.length) incoming.current={id:pending.current.shift(),elapsed:0}
      if(incoming.current) {
        incoming.current.elapsed+=delta
        if(incoming.current.elapsed>=450) incoming.current=null
      }
      setPicture({dots:[...dots.current.values()].map(dot=>({...dot})),incoming:incoming.current ? {...incoming.current} : null})
      handle=requestAnimationFrame(tick)
    }
    handle=requestAnimationFrame(tick)
    return()=>cancelAnimationFrame(handle)
  },[state.running])
  return <g className="traffic-pipeline" aria-label="서버 앞 실제 대기열 · 점 하나는 요청 한 건">
    {picture.incoming && <g transform={`translate(500 ${176+68*picture.incoming.elapsed/450}) scale(${dotScale} 1)`}><circle className="pipeline-incoming" r="4" fill="#8996a5"><title>요청 #{picture.incoming.id+1} 도착</title></circle></g>}
    {picture.dots.filter(dot=>dot.distance<=queuePathLength(dot.server)).map(dot=>{
      const point=queuePoint(dot.server,dot.distance)
      return <g key={`${dot.server}-${dot.id}`} transform={`translate(${point.x} ${point.y}) scale(${dotScale} 1)`}><circle className={`pipeline-queue-dot ${dot.exiting?'departing':''}`} data-request={dot.id} data-server={dot.server} r="5" fill={COLORS[dot.server]} stroke="#fff" strokeWidth="2"><title>서버 {dot.server} · 요청 #{dot.id+1} {dot.exiting?'서버로 이동':'대기'}</title></circle></g>
    })}
    {state.servers.map(server=>{
      const hidden=Math.max(0,server.queue-visibleQueue(server).length)
      const x={A:295,B:575,C:705}[server.id],y=server.id==='B'?491:446
      return <g key={server.id} className="pipeline-queue-label" transform={`translate(${x} ${y}) scale(${dotScale} 1)`}>
        <rect x="-43" y="-12" width="86" height="23" rx="7"/>
        <text textAnchor="middle" dominantBaseline="middle" fill={COLORS[server.id]}>{server.id} 대기 {server.queue}{hidden?` (+${hidden})`:''}</text>
      </g>
    })}
  </g>
}
