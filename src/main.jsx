import React from 'react'
import { createRoot } from 'react-dom/client'
import SimulatorApp from './SimulatorApp.jsx'
import './styles.css'
import './readability.css'
import './narration.css'
import './request-story.css'

createRoot(document.getElementById('root')).render(<SimulatorApp/>)
