import { Room, RoomEvent, createLocalAudioTrack } from 'https://esm.sh/livekit-client@2'

// DOM refs
const startBtn = document.getElementById('startBtn')
const landing = document.getElementById('landing')
const inCall = document.getElementById('inCall')
const callBar = document.getElementById('callBar')
const statusDot = document.getElementById('statusDot')
const timerEl = document.getElementById('timer')
const remoteAudio = document.getElementById('remoteAudio')
const waveCanvas = document.getElementById('waveCanvas')
const ctx = waveCanvas.getContext('2d')
const btnHangup = document.getElementById('btnHangup')
const btnMute = document.getElementById('btnMute')
const btnUnmute = document.getElementById('btnUnmute')
const btnLog = document.getElementById('btnLog')
const logPanel = document.getElementById('logPanel')
const btnCloseLog = document.getElementById('btnCloseLog')
const logEl = document.getElementById('log')
// Unified panel
const infoPanel = document.getElementById('infoPanel')
const infoTitle = document.getElementById('infoTitle')
const infoBody = document.getElementById('infoBody')
const infoActions = document.getElementById('infoActions')

let room, localTrack, analyser, dataArray, audioCtx
let identityConfirmed = false
let callStart = 0, timerInterval

function log(msg){
  const atBottom = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 5
  const li = document.createElement('li')
  li.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`
  logEl.appendChild(li)
  if(atBottom) logEl.scrollTop = logEl.scrollHeight
}

function formatTime(sec){ const m=Math.floor(sec/60).toString().padStart(2,'0'); const s=(sec%60).toString().padStart(2,'0'); return `${m}:${s}` }
function startTimer(){ callStart=Date.now(); timerInterval && clearInterval(timerInterval); timerInterval=setInterval(()=>{ const secs=Math.floor((Date.now()-callStart)/1000); timerEl.textContent=formatTime(secs) },1000) }
function stopTimer(){ clearInterval(timerInterval); timerInterval=null; timerEl.textContent='00:00' }

async function fetchToken(identity){ const r = await fetch(`/api/token?identity=${encodeURIComponent(identity)}`); if(!r.ok) throw new Error('Token fetch failed'); return r.json() }

function showCall(){ landing.classList.add('hidden'); inCall.classList.remove('hidden'); callBar.classList.add('active') }
function showLanding(){ landing.classList.remove('hidden'); inCall.classList.add('hidden'); callBar.classList.remove('active') }

function sendData(obj){ if(!room) return; try { const payload = new TextEncoder().encode(JSON.stringify(obj)); room.localParticipant.publishData(payload) } catch(e){ log('Send data err '+ e.message) } }

function initAudioAnalyser(){
  if(audioCtx) return
  audioCtx = new (window.AudioContext || window.webkitAudioContext)()
  try {
    const source = audioCtx.createMediaElementSource(remoteAudio)
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = 2048
    dataArray = new Uint8Array(analyser.frequencyBinCount)
    source.connect(analyser)
    analyser.connect(audioCtx.destination)
    drawWave()
  } catch(e){ log('Analyser init fail '+ e.message) }
}

function drawWave(){
  if(!analyser){ requestAnimationFrame(drawWave); return }
  const W = waveCanvas.width = waveCanvas.clientWidth * window.devicePixelRatio
  const H = waveCanvas.height = waveCanvas.clientHeight * window.devicePixelRatio
  analyser.getByteTimeDomainData(dataArray)
  ctx.clearRect(0,0,W,H)
  const grad = ctx.createLinearGradient(0,0,W,H)
  grad.addColorStop(0,'#60a5fa'); grad.addColorStop(.5,'#818cf8'); grad.addColorStop(1,'#a78bfa')
  ctx.lineWidth = Math.max(2, W/1400*2)
  ctx.strokeStyle = grad
  ctx.beginPath()
  const slice = W / dataArray.length
  const mid = H/2
  for(let i=0;i<dataArray.length;i++){
    const v = (dataArray[i]-128)/128
    const y = mid + v * (H*0.38)
    const x = i * slice
    i? ctx.lineTo(x,y): ctx.moveTo(x,y)
  }
  ctx.stroke()
  ctx.globalCompositeOperation='lighter'
  ctx.fillStyle='rgba(96,165,250,0.06)'
  ctx.fillRect(0,0,W,H)
  ctx.globalCompositeOperation='source-over'
  requestAnimationFrame(drawWave)
}

function showIdentity(data){
  infoPanel.classList.add('show')
  infoTitle.textContent = 'Thông tin bệnh nhân'
  infoBody.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:.55rem .9rem;font-size:.65rem;">\n  <div><label style='display:block;font-size:.5rem;opacity:.55;text-transform:uppercase;letter-spacing:.5px;'>Họ tên</label>${data.patient_name || '<i>(chưa)</i>'}</div>\n  <div><label style='display:block;font-size:.5rem;opacity:.55;text-transform:uppercase;letter-spacing:.5px;'>SĐT</label>${data.phone || '<i>(chưa)</i>'}</div>\n</div>`
  infoActions.innerHTML = ''
  if(identityConfirmed){
    const btn = document.createElement('button')
    btn.textContent = 'Sửa'; btn.className='ghost'; btn.style.fontSize='.6rem'
    btn.onclick = () => {
      const n = prompt('Tên', data.patient_name || '')
      const p = prompt('SĐT', data.phone || '')
      if(n||p) sendData({type:'identity_corrected', patient_name:n||data.patient_name, phone:p||data.phone})
    }
    infoActions.appendChild(btn)
    infoActions.style.display='flex'
  } else {
    infoActions.style.display='none'
  }
}

function showBookingPending(){
  infoPanel.classList.add('show')
  infoTitle.textContent='Đặt lịch'
  infoBody.innerHTML='<span class="muted" style="font-size:.65rem;">Đang tìm lịch khám phù hợp...</span>'
  infoActions.style.display='none'
}
function showBooking(result){
  infoPanel.classList.add('show')
  infoTitle.textContent='Lịch hẹn'
  const b = result.booking || result
  infoBody.innerHTML = `<div style='display:grid;gap:.35rem;font-size:.65rem;'>
    ${b.department?`<div><b>Khoa:</b> ${b.department}</div>`:''}
    ${b.doctor_name?`<div><b>Bác sĩ:</b> ${b.doctor_name}</div>`:''}
    ${(b.slot_time||b.appointment_time)?`<div><b>Thời gian:</b> ${b.slot_time||b.appointment_time}</div>`:''}
    ${b.room?`<div><b>Phòng:</b> ${b.room}</div>`:''}
    ${b.queue_number?`<div><b>STT:</b> ${b.queue_number}</div>`:''}
    ${b.symptoms?`<div><b>Triệu chứng:</b> ${Array.isArray(b.symptoms)? b.symptoms.map(s=>s.name||s).join(', '): b.symptoms}</div>`:''}
  </div>`
  infoActions.style.display='none'
}

function attachEvents(r){
  r.on(RoomEvent.ConnectionStateChanged, st => {
    if(st === 'connected'){ statusDot.classList.remove('connecting','err'); statusDot.classList.add('connected') }
    else if(st === 'connecting'){ statusDot.classList.remove('connected','err'); statusDot.classList.add('connecting') }
    else if(st === 'disconnected'){ statusDot.classList.remove('connected','connecting') }
  })
  r.on(RoomEvent.TrackSubscribed, (track) => {
    if(track.kind==='audio'){
      track.attach(remoteAudio)
      remoteAudio.addEventListener('play', ()=> initAudioAnalyser(), { once:true })
      log('Đã nhận audio từ agent')
    }
  })
  r.on(RoomEvent.DataReceived, payload => {
    try {
      const msg = JSON.parse(new TextDecoder().decode(payload))
      switch(msg.type){
        case 'identity_captured': log('Identity đề xuất'); showIdentity(msg); break
        case 'identity_confirmed': log('Identity xác nhận'); identityConfirmed=true; showIdentity(msg); break
        case 'booking_pending': log('Đang đặt lịch'); showBookingPending(); break
        case 'booking_result': log('Đặt lịch xong'); showBooking(msg); break
        case 'wrapup_done': log('Kết thúc phiên'); hangup(); break
        default: log('DATA '+ JSON.stringify(msg))
      }
    } catch(e){ log('Data(raw) '+ payload.byteLength + ' bytes') }
  })
  r.on(RoomEvent.Disconnected, () => { log('Disconnected'); hangup(true) })
}

async function startCall(){
  startBtn.disabled = true
  statusDot.classList.add('connecting')
  try {
    const identity = 'web-' + Math.random().toString(36).slice(2,8)
    const { url, token } = await fetchToken(identity)
    room = new Room()
    attachEvents(room)
    const track = await createLocalAudioTrack()
    localTrack = track
    await room.connect(url, token, { autoSubscribe:true })
    await room.localParticipant.publishTrack(track)
    showCall(); startTimer();
    log('Đã tham gia phòng')
  } catch(e){
    statusDot.classList.add('err')
    log('Join lỗi: '+ e.message)
    startBtn.disabled = false
  }
}

async function hangup(silent){
  try { if(localTrack){ localTrack.stop(); localTrack=null } } catch{}
  try { if(room){ await room.disconnect(); room=null } } catch{}
  stopTimer(); identityConfirmed=false
  infoPanel.classList.remove('show')
  log('Đã thoát phòng')
  if(!silent){ showLanding(); startBtn.disabled=false }
}

function mute(){ if(!localTrack) return; localTrack.mute(); btnMute.classList.add('hidden'); btnUnmute.classList.remove('hidden'); log('Mic OFF') }
function unmute(){ if(!localTrack) return; localTrack.unmute(); btnUnmute.classList.add('hidden'); btnMute.classList.remove('hidden'); log('Mic ON') }

btnMute.onclick = mute
btnUnmute.onclick = unmute
btnHangup.onclick = () => hangup()
startBtn.addEventListener('click', startCall)
btnLog.onclick = () => { logPanel.classList.toggle('show') }
btnCloseLog.onclick = () => logPanel.classList.remove('show')

const ro = new ResizeObserver(()=> drawWave())
ro.observe(waveCanvas)
// Kick off animation even before audio
requestAnimationFrame(drawWave)
