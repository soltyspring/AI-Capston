import React, { useEffect, useRef } from 'react'

export default function RequestParticle({ decision, prefix = 'sim-route-', neutralUntil = .5 }) {
  const motion = useRef(null)
  const fade = useRef(null)
  const color = useRef(null)
  const assignedColor = {A:'#159b70',B:'#397fc4',C:'#8065b3'}[decision.selected]
  useEffect(() => { motion.current?.beginElement(); fade.current?.beginElement(); color.current?.beginElement() }, [])
  return <circle opacity="0" r={3 + Math.min(5, decision.input_tokens / 4000)} fill="#9aa3af">
    <animate ref={color} begin="indefinite" attributeName="fill" values={`#9aa3af;${assignedColor}`} keyTimes={`0;${Math.max(.001,Math.min(.999,neutralUntil))}`} calcMode="discrete" dur="2s" fill="freeze"/>
    <animateMotion ref={motion} begin="indefinite" dur="2s" fill="freeze"><mpath href={`#${prefix}${decision.selected}`}/></animateMotion>
    <animate ref={fade} begin="indefinite" attributeName="opacity" from=".85" to="0" dur="2s" fill="freeze"/>
  </circle>
}
