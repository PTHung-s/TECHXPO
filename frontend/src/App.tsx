import React, { useState, useRef } from 'react'

// This component demonstrates basic interactions with the backend:
// - Voice WebSocket (placeholder: only text messages)
// - Camera capture and upload
// - HeyGen avatar creation polling

const App: React.FC = () => {
  const [ws, setWs] = useState<WebSocket | null>(null)
  const [caption, setCaption] = useState('')
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [avatarJob, setAvatarJob] = useState<string | null>(null)
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null)

  const connectWs = () => {
    const socket = new WebSocket('ws://localhost:8000/ws/voice')
    socket.onopen = () => setWs(socket)
    socket.onmessage = e => {
      const msg = JSON.parse(e.data)
      if (msg.caption) setCaption(msg.caption)
      if (msg.type === 'audio' && msg.data) {
        const pcm = Uint8Array.from(atob(msg.data), c => c.charCodeAt(0)).buffer
        const audioCtx = new AudioContext({ sampleRate: 24000 })
        audioCtx.decodeAudioData(pcm.slice(0)).then(buf => {
          const src = audioCtx.createBufferSource()
          src.buffer = buf
          src.connect(audioCtx.destination)
          src.start()
        }).catch(() => {})
      }
    }
  }

  const openCamera = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true })
    if (videoRef.current) {
      videoRef.current.srcObject = stream
      await videoRef.current.play()
    }
  }

  const capturePhoto = async () => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const ctx = canvas.getContext('2d')!
    ctx.drawImage(video, 0, 0)
    const blob = await new Promise<Blob | null>(r => canvas.toBlob(r, 'image/jpeg'))
    if (blob) {
      const form = new FormData()
      form.append('file', blob, 'capture.jpg')
      const res = await fetch('http://localhost:8000/upload-image', { method: 'POST', body: form })
      const js = await res.json()
      alert(js.text)
    }
  }

  const createAvatar = async (script: string) => {
    const form = new FormData()
    form.append('script', script)
    form.append('avatar_id', 'default')
    form.append('voice_id', 'default')
    const res = await fetch('http://localhost:8000/avatar/create', { method: 'POST', body: form })
    const js = await res.json()
    setAvatarJob(js.data?.video_id || js.video_id || null)
  }

  const pollAvatar = async () => {
    if (!avatarJob) return
    const res = await fetch(`http://localhost:8000/avatar/status?job_id=${avatarJob}`)
    const js = await res.json()
    if (js.data?.video_url) setAvatarUrl(js.data.video_url)
  }

  return (
    <div className="p-4 space-y-4">
      <div>
        <button className="px-2 py-1 bg-blue-500 text-white" onClick={connectWs}>Connect WS</button>
        <p className="mt-2">Caption: {caption}</p>
      </div>
      <div>
        <button className="px-2 py-1 bg-green-500 text-white" onClick={openCamera}>Open Camera</button>
        <button className="ml-2 px-2 py-1 bg-purple-500 text-white" onClick={capturePhoto}>Capture & Send</button>
        <div className="mt-2 flex space-x-2">
          <video ref={videoRef} className="w-48 h-36 bg-black" />
          <canvas ref={canvasRef} className="w-48 h-36" />
        </div>
      </div>
      <div>
        <button className="px-2 py-1 bg-orange-500 text-white" onClick={() => createAvatar('Hello from HeyGen')}>Generate Avatar</button>
        <button className="ml-2 px-2 py-1 bg-gray-500 text-white" onClick={pollAvatar}>Check Status</button>
        {avatarUrl && <video src={avatarUrl} controls className="mt-2 w-48" />}
      </div>
    </div>
  )
}

export default App
