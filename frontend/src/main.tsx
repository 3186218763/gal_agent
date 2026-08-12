import React from 'react'
import ReactDOM from 'react-dom/client'
import LiveApp from './live/LiveApp'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <LiveApp />
  </React.StrictMode>,
)
