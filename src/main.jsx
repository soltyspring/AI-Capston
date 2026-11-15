import React from 'react'
import { createRoot } from 'react-dom/client'
import SimulatorApp from './SimulatorApp.jsx'
import './styles.css'
import './readability.css'
import './narration.css'
import './request-story.css'

if (window.location.hostname === 'bluecl.cloud' &&
    /^\/aicap\/(?:index\.html)?$/.test(window.location.pathname)) {
  const destination = new URL('https://qless.bluecl.cloud/')
  destination.search = window.location.search
  destination.hash = window.location.hash
  window.location.replace(destination.href)
} else {
  createRoot(document.getElementById('root')).render(<SimulatorApp/>)
}
