import { Room, RoomEvent, createLocalAudioTrack } from 'https://esm.sh/livekit-client@2'

// Logo handling
function createFavicon(logoSrc) {
  // Create canvas to generate favicon
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  canvas.width = 32
  canvas.height = 32
  
  const img = new Image()
  img.onload = function() {
    // Clear canvas with rounded background
    ctx.fillStyle = '#2563eb'
    ctx.fillRect(0, 0, 32, 32)
    
    // Draw logo centered
    const size = 28 // Leave 2px padding
    const offset = 2
    ctx.drawImage(img, offset, offset, size, size)
    
    // Convert to favicon
    const faviconUrl = canvas.toDataURL('image/png')
    
    // Update favicon
    let favicon = document.querySelector('link[rel="shortcut icon"]')
    if (!favicon) {
      favicon = document.createElement('link')
      favicon.rel = 'shortcut icon'
      document.head.appendChild(favicon)
    }
    favicon.href = faviconUrl
    
    // Also update 32x32 favicon
    let favicon32 = document.querySelector('link[rel="icon"][sizes="32x32"]')
    if (favicon32) {
      favicon32.href = faviconUrl
    }
  }
  img.src = logoSrc
}

function createFallbackFavicon() {
  // Create a simple AI favicon as fallback
  const canvas = document.createElement('canvas')
  const ctx = canvas.getContext('2d')
  canvas.width = 32
  canvas.height = 32
  
  // Background gradient
  const gradient = ctx.createLinearGradient(0, 0, 32, 32)
  gradient.addColorStop(0, '#2563eb')
  gradient.addColorStop(1, '#4f46e5')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 32, 32)
  
  // Draw "AI" text
  ctx.fillStyle = '#ffffff'
  ctx.font = 'bold 16px Arial'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText('AI', 16, 16)
  
  // Update favicon
  const faviconUrl = canvas.toDataURL('image/png')
  let favicon = document.querySelector('link[rel="shortcut icon"]')
  if (!favicon) {
    favicon = document.createElement('link')
    favicon.rel = 'shortcut icon'
    document.head.appendChild(favicon)
  }
  favicon.href = faviconUrl
}

function initLogo() {
  const logoImg = document.getElementById('logoImg')
  const logoFallback = document.getElementById('logoFallback')
  const logoContainer = document.getElementById('logoContainer')
  
  // Try to load logo.png from images directory
  const logoPath = '/images/logo.png'
  
  // Test if logo exists
  const testImg = new Image()
  testImg.onload = function() {
    // Logo exists, show it
    logoImg.src = logoPath
    logoImg.style.display = 'block'
    logoFallback.style.display = 'none'
    logoContainer.classList.remove('fallback')
    
    // Create favicon from logo
    createFavicon(logoPath)
  }
  testImg.onerror = function() {
    // Logo doesn't exist, use fallback
    logoImg.style.display = 'none'
    logoFallback.style.display = 'flex'
    logoContainer.classList.add('fallback')
    
    // Create fallback favicon
    createFallbackFavicon()
  }
  testImg.src = logoPath
}

// Global logo error handler
window.handleLogoError = function() {
  const logoImg = document.getElementById('logoImg')
  const logoFallback = document.getElementById('logoFallback')
  const logoContainer = document.getElementById('logoContainer')
  
  logoImg.style.display = 'none'
  logoFallback.style.display = 'flex'
  logoContainer.classList.add('fallback')
  
  // Create fallback favicon when logo fails to load
  createFallbackFavicon()
}

// Initialize logo on page load
document.addEventListener('DOMContentLoaded', initLogo)

// DOM refs
const startBtn = document.getElementById('startBtn')
const landing = document.getElementById('landing')
const inCall = document.getElementById('inCall')
const callBar = document.getElementById('callBar')
const statusDot = document.getElementById('statusDot')
const timerEl = document.getElementById('timer')
const remoteAudio = document.getElementById('remoteAudio')
const waveCanvas = document.getElementById('waveCanvas')
const ctx = waveCanvas.getContext('2d',{alpha:true})
// Old hangup removed, new button inside waveBar
const btnHangup = document.getElementById('btnEndCall')
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

let room, localTrack, analyser, freqArray, audioCtx, remoteSource
let firstRemoteAudio = false
// Preload chime
const chime = new Audio('data:audio/mp3;base64,//uQxAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAACcQCA//////////////////////////////////////////////8AAAAALGFuY2UAAAACAAAAYXRhAAAAAQAAAAD//w==') // tiny silent placeholder, replace with real asset if available
chime.volume = 0.85
let identityConfirmed = false
let callStart = 0, timerInterval
// Reset UI state between calls
function resetUI(){
  // Clear booking / info content
  infoTitle.textContent = 'Thông tin'
  infoBody.innerHTML = '<span style="font-size:.9rem;opacity:.6;">Đang nhận dữ liệu...</span>'
  infoActions.innerHTML = ''
  infoPanel.className = ''
  infoPanel.id = 'infoPanel' // ensure id intact (class reset)
  // Clear log
  logEl.innerHTML = ''
  // Flags
  identityConfirmed = false
}

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

function showCall(){
  landing.classList.add('hidden')
  inCall.classList.remove('hidden')
  callBar.classList.add('active')
}
function showLanding(){ landing.classList.remove('hidden'); inCall.classList.add('hidden'); callBar.classList.remove('active') }

function sendData(obj){ if(!room) return; try { const payload = new TextEncoder().encode(JSON.stringify(obj)); room.localParticipant.publishData(payload) } catch(e){ log('Send data err '+ e.message) } }

function initAudioAnalyserFromMediaStreamTrack(msTrack){
  if(!msTrack) return
  if(!audioCtx){
    audioCtx = new (window.AudioContext || window.webkitAudioContext)()
  }
  // Always create new analyser for remote talker so we switch source cleanly
  try {
    const ms = new MediaStream([msTrack])
    remoteSource = audioCtx.createMediaStreamSource(ms)
    analyser = audioCtx.createAnalyser()
  analyser.fftSize = 2048
  analyser.smoothingTimeConstant = 0.85
  freqArray = new Uint8Array(analyser.frequencyBinCount)
    remoteSource.connect(analyser)
    // Do NOT connect analyser to destination (no duplication of audio)
    if(audioCtx.state === 'suspended') { audioCtx.resume().catch(()=>{}) }
  } catch(e){ log('Analyser init fail '+ e.message) }
}

let _flatCounter = 0
function drawWave(){
  const dpr = window.devicePixelRatio || 1
  const cssW = waveCanvas.clientWidth || waveCanvas.parentElement.clientWidth || 300
  const cssH = waveCanvas.clientHeight || waveCanvas.parentElement.clientHeight || 120
  const W = waveCanvas.width = cssW * dpr
  const H = waveCanvas.height = cssH * dpr
  ctx.clearRect(0,0,W,H)

  // Background subtle gradient
  const bgGrad = ctx.createLinearGradient(0,0,W,H)
  bgGrad.addColorStop(0,'rgba(30,58,138,0.15)')
  bgGrad.addColorStop(1,'rgba(15,23,42,0.35)')
  ctx.fillStyle = bgGrad
  ctx.fillRect(0,0,W,H)

  if(!analyser){
    ctx.fillStyle='rgba(148,163,184,0.18)'
    ctx.fillRect(0, H/2 - 1*dpr, W, 2*dpr)
    requestAnimationFrame(drawWave)
    return
  }

  analyser.getByteFrequencyData(freqArray)
  if(!freqArray || !freqArray.length){ requestAnimationFrame(drawWave); return }
  // Centered symmetric bars: compute half side and mirror
  // Denser bars (roughly double): reduce slot base from 6px to ~3px (bar+gap)
  const BAR_GAP = 1 * dpr
  const maxBarsFull = Math.floor(W / (3 * dpr))
  const fullCount = Math.min(maxBarsFull, 220)
  const halfCount = Math.floor(fullCount / 2)
  const binSize = Math.max(1, Math.floor(freqArray.length / fullCount))

  const barGrad = ctx.createLinearGradient(0,0,0,H)
  barGrad.addColorStop(0,'#a78bfa')
  barGrad.addColorStop(.35,'#818cf8')
  barGrad.addColorStop(1,'#60a5fa')
  ctx.fillStyle = barGrad

  let globalMax = 0
  const barSlot = W / fullCount
  const centerX = W / 2
  for(let i=0;i<halfCount;i++){
    let sumL=0, sumR=0
    for(let j=0;j<binSize;j++){
      sumL += freqArray[i*binSize + j] || 0
      sumR += freqArray[(fullCount-1 - i)*binSize + j] || 0
    }
    const avgL = (sumL / binSize) / 255
    const avgR = (sumR / binSize) / 255
    const avg = (avgL + avgR)/2
    if(avg>globalMax) globalMax = avg
    const eased = Math.pow(avg, 0.65)
    const barH = Math.max(2*dpr, eased * (H*0.75))
    const barW = Math.max(3*dpr, barSlot - BAR_GAP)
    const offset = (i+0.2) * barSlot
    const xLeft = centerX - offset - barW/2
    const xRight = centerX + offset - barW/2
    const y = (H - barH)/2
    const r = Math.min(4*dpr, barW/2)
    function drawBar(x){
      ctx.beginPath()
      ctx.moveTo(x, y + barH)
      ctx.lineTo(x, y + r)
      ctx.quadraticCurveTo(x, y, x + r, y)
      ctx.lineTo(x + barW - r, y)
      ctx.quadraticCurveTo(x + barW, y, x + barW, y + r)
      ctx.lineTo(x + barW, y + barH)
      ctx.closePath()
      ctx.globalAlpha = 0.55 + eased * 0.45
      ctx.fill()
    }
    drawBar(xLeft)
    drawBar(xRight)
  }
  // central minimal bar (if odd count) for aesthetic
  if(fullCount % 2 === 1){
    const midEnergy = globalMax * 0.9
    const eased = Math.pow(midEnergy,0.65)
    const barH = Math.max(2*dpr, eased * (H*0.75))
    const barW = Math.max(3*dpr, barSlot - BAR_GAP)
    const x = centerX - barW/2
    const y = (H - barH)/2
    const r = Math.min(4*dpr, barW/2)
    ctx.beginPath()
    ctx.moveTo(x, y + barH)
    ctx.lineTo(x, y + r)
    ctx.quadraticCurveTo(x, y, x + r, y)
    ctx.lineTo(x + barW - r, y)
    ctx.quadraticCurveTo(x + barW, y, x + barW, y + r)
    ctx.lineTo(x + barW, y + barH)
    ctx.closePath()
    ctx.globalAlpha = 0.55 + Math.pow(midEnergy,0.65) * 0.45
    ctx.fill()
  }
  ctx.globalAlpha = 1
  const energy = globalMax
  ctx.fillStyle = `rgba(96,165,250,${0.04 + energy*0.10})`
  ctx.fillRect(0,0,W,H)
  // Flat detection retained internally (no text overlay to maximize visual area)
  if(energy < 0.02) { _flatCounter++; } else { _flatCounter = 0 }
  requestAnimationFrame(drawWave)
}

function showIdentity(data){
  infoPanel.classList.add('show')
  // Visual glow for propose / confirm handled externally by caller
  infoTitle.textContent = 'Thông tin bệnh nhân'
  infoBody.innerHTML = `<div class="identity-block">\n    <div>\n      <span class='identity-label'>Họ tên</span>\n      <span class='identity-line'>${data.patient_name || '<i>(chưa)</i>'}</span>\n    </div>\n    <div>\n      <span class='identity-label'>SĐT</span>\n      <span class='identity-line'>${data.phone || '<i>(chưa)</i>'}</span>\n    </div>\n  </div>`
  infoActions.innerHTML = ''
  if(identityConfirmed){
    const btn = document.createElement('button')
    btn.textContent = 'Sửa'; btn.className='ghost'; btn.style.fontSize='.6rem'
    btn.onclick = () => {
      const n = prompt('Tên', data.patient_name || '')
      const p = prompt('SĐT', data.phone || '')
      if(n||p){
        identityConfirmed = false; // force flow to reconfirm
        sendData({type:'identity_corrected', patient_name:n||data.patient_name, phone:p||data.phone})
      }
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
  const payload = result.booking || result
  const multi = !!payload.options
  if(multi){
    // Render multiple options + highlight chosen if available
    const chosen = payload.chosen || payload.options[0]
    const cards = payload.options.map((opt,idx)=>{
    const hospLabel = opt.hospital_name || opt.hospital || opt.hospital_code || 'Bệnh viện'
    const chosenHosp = chosen?.hospital_name || chosen?.hospital || chosen?.hospital_code
    const isChosen = chosen && (opt.slot_time===chosen.slot_time && opt.doctor_name===chosen.doctor_name && hospLabel===chosenHosp)
    const img = opt.image_url ? `<img src='${opt.image_url}' alt='${hospLabel}' loading='lazy'/>` : `<div class='no-img-fallback'></div>`
      return `<div class='bk-card${isChosen?' chosen':''}' data-idx='${idx}'>
        <div class='bk-img'>
          ${isChosen?`<div class='bk-badge'>Chọn</div>`:''}
          ${img}
      <div class='bk-hosp-overlay'>${hospLabel}</div>
        </div>
        <div class='bk-meta'>
          ${opt.department?`<div class='bk-line'><span>Khoa:</span> ${opt.department}</div>`:''}
          ${opt.doctor_name?`<div class='bk-line'><span>BS:</span> ${opt.doctor_name}</div>`:''}
          ${opt.slot_time?`<div class='bk-line'><span>Giờ:</span> ${opt.slot_time}</div>`:''}
          ${opt.room?`<div class='bk-line'><span>Phòng:</span> ${opt.room}</div>`:''}
          ${opt.score?`<div class='bk-score'>${opt.score.toFixed(2)}</div>`:''}
        </div>
      </div>`
    }).join('')
    infoBody.innerHTML = `<div class='bk-wrapper'>
      <div class='bk-grid'>${cards}</div>
    </div>`
  } else {
    const b = payload
    infoBody.innerHTML = `<div style='display:grid;gap:.35rem;font-size:.65rem;'>
  ${(b.hospital_name||b.hospital)?`<div><b>Bệnh viện:</b> ${b.hospital_name||b.hospital}</div>`:''}
      ${b.department?`<div><b>Khoa:</b> ${b.department}</div>`:''}
      ${b.doctor_name?`<div><b>Bác sĩ:</b> ${b.doctor_name}</div>`:''}
      ${(b.slot_time||b.appointment_time)?`<div><b>Thời gian:</b> ${b.slot_time||b.appointment_time}</div>`:''}
      ${b.room?`<div><b>Phòng:</b> ${b.room}</div>`:''}
      ${b.queue_number?`<div><b>STT:</b> ${b.queue_number}</div>`:''}
      ${b.symptoms?`<div><b>Triệu chứng:</b> ${Array.isArray(b.symptoms)? b.symptoms.map(s=>s.name||s).join(', '): b.symptoms}</div>`:''}
    </div>`
  }
  infoActions.style.display='none'
}

function renderBookingOptions(options) {
  const wrap = document.getElementById('bookingOptions');
  if (!wrap) return;
  wrap.innerHTML = '';
  options.forEach((opt, idx) => {
    const hospitalName = opt.hospital_name || opt.hospital || opt.hospital_code || 'Bệnh viện';
    const dep = opt.department || opt.department_name || opt.department_code || '';
    const doc = opt.doctor_name || '';
    const time = opt.slot_time || '';
    const img = opt.image_url || '/images/default.png';
    const card = document.createElement('div');
    card.className = 'booking-card';
    card.innerHTML = `
      <div class="booking-card-img" style="background-image:url('${img}')"></div>
      <div class="booking-card-body">
        <div class="booking-hospital">${hospitalName}</div>
        <div class="booking-dep">Khoa: ${dep}</div>
        <div class="booking-doc">BS: ${doc}</div>
        <div class="booking-time">Giờ: ${time}</div>
      </div>`;
    card.onclick = () => chooseOption(idx);
    wrap.appendChild(card);
  });
}

// Nếu trước đó có hàm khác (updateBookingCards / showBookingOptions) gọi, thay nó gọi renderBookingOptions(data.options)

function attachEvents(r){
  r.on(RoomEvent.ConnectionStateChanged, st => {
    if(st === 'connected'){ statusDot.classList.remove('connecting','err'); statusDot.classList.add('connected') }
    else if(st === 'connecting'){ statusDot.classList.remove('connected','err'); statusDot.classList.add('connecting') }
    else if(st === 'disconnected'){ statusDot.classList.remove('connected','connecting') }
  })
  r.on(RoomEvent.TrackSubscribed, (track) => {
    if(track.kind==='audio'){
      track.attach(remoteAudio)
      // Some browsers need explicit play call
      remoteAudio.play().catch(()=>{})
      // Build analyser directly from track media stream
      const msTrack = track.mediaStreamTrack || (track._mediaStreamTrack) // fallback internal
      initAudioAnalyserFromMediaStreamTrack(msTrack)
      log('Đã nhận audio từ agent')
      if(!firstRemoteAudio){
        firstRemoteAudio = true
        // Play chime then transition UI if still on landing
        try { chime.currentTime = 0; chime.play().catch(()=>{}) } catch{}
        // Animate start button circle expansion before showing call
        if(!inCall || inCall.classList.contains('hidden')){
          document.body.classList.add('transitioning')
          if(startBtn){
            startBtn.classList.add('expanding')
            setTimeout(()=>{ showCall(); startTimer(); startBtn.classList.remove('expanding'); document.body.classList.remove('transitioning') }, 1300)
          } else {
            showCall(); startTimer();
          }
        }
      }
    }
  })
  r.on(RoomEvent.DataReceived, payload => {
    try {
      const msg = JSON.parse(new TextDecoder().decode(payload))
      switch(msg.type){
        case 'identity_captured':
          log('Identity đề xuất');
          showIdentity(msg);
          infoPanel.classList.remove('glow-green','glow-amber','glow-purple')
          infoPanel.classList.add('glow-blue') // propose -> blue
          break
        case 'identity_confirmed':
          log('Identity xác nhận');
          identityConfirmed=true; showIdentity(msg);
          infoPanel.classList.remove('glow-blue','glow-amber','glow-purple')
          infoPanel.classList.add('glow-green') // confirm -> green
          break
        case 'identity_updated': 
          log('Identity cập nhật'); identityConfirmed=true; showIdentity(msg); 
          infoPanel.classList.remove('glow-blue','glow-amber','glow-purple')
          infoPanel.classList.add('glow-green')
          break
        case 'personal_context_loaded':
          log('Personal context loaded: ' + (msg.visits_count || 0) + ' visits')
          if (msg.visits_count > 0 && infoTitle.textContent === 'Thông tin bệnh nhân') {
            const badge = document.createElement('div')
            badge.className = 'returning-patient-badge'
            badge.style.cssText = 'margin-top:.4rem;color:#10b981;font-size:.6rem;font-weight:500;'
            badge.textContent = `Khách quen • ${msg.visits_count} lần khám`
            infoBody.appendChild(badge)
          }
          break
  case 'identity_updated': log('Identity cập nhật'); identityConfirmed=true; showIdentity(msg); break
        case 'booking_pending':
          log('Đang đặt lịch');
          showBookingPending();
          infoPanel.classList.remove('glow-green','glow-purple')
          infoPanel.classList.add('glow-amber') // searching -> amber
          break
        case 'booking_result':
          log('Đặt lịch xong');
          showBooking(msg);
          // list options -> purple highlight
          infoPanel.classList.remove('glow-amber','glow-green')
          infoPanel.classList.add('glow-purple')
          break
        case 'booking_option_chosen':
          log('Đã chọn 1 phương án');
          showBooking(msg.booking || msg);
          // finalize chosen -> green
          infoPanel.classList.remove('glow-amber','glow-blue','glow-purple')
          infoPanel.classList.add('glow-green')
          break
  case 'booking_error': log('Lỗi đặt lịch'); infoTitle.textContent='Đặt lịch'; infoBody.innerHTML='<span style="color:#dc2626;font-size:.65rem;">Không đặt được lịch, sẽ thử lại sau.</span>'; break
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
  startBtn.querySelector('.small')?.classList.add('hidden')
  // Reset UI so new call không thấy card cũ
  resetUI()
  try {
    const identity = 'web-' + Math.random().toString(36).slice(2,8)
    const { url, token } = await fetchToken(identity)
    room = new Room()
    attachEvents(room)
    const track = await createLocalAudioTrack()
    localTrack = track
    await room.connect(url, token, { autoSubscribe:true })
    await room.localParticipant.publishTrack(track)
    // Delay UI transition until first remote audio arrives; add connecting visual
    startBtn.textContent = 'ĐANG KẾT NỐI...'
    const span = document.createElement('span'); span.className='small'; span.textContent='Đợi phản hồi'; startBtn.appendChild(span)
    log('Đã tham gia phòng (đợi audio)')
    // Fallback: if no remote audio in 6s, proceed anyway
    setTimeout(()=>{ if(!firstRemoteAudio){ showCall(); startTimer(); log('Không thấy audio, vào giao diện') } }, 6000)
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
  if(!silent){ showLanding(); startBtn.disabled=false; firstRemoteAudio=false; startBtn.textContent='BẮT ĐẦU'; const sm=document.createElement('span'); sm.className='small'; sm.textContent='Cho phép Micro'; startBtn.appendChild(sm) }
}

function mute(){ if(!localTrack) return; localTrack.mute(); btnMute.classList.add('hidden'); btnUnmute.classList.remove('hidden'); log('Mic OFF') }
function unmute(){ if(!localTrack) return; localTrack.unmute(); btnUnmute.classList.add('hidden'); btnMute.classList.remove('hidden'); log('Mic ON') }

btnMute.onclick = mute
btnUnmute.onclick = unmute
if(btnHangup) btnHangup.onclick = () => hangup()
startBtn.addEventListener('click', startCall)
btnLog.onclick = () => { logPanel.classList.toggle('show') }
btnCloseLog.onclick = () => logPanel.classList.remove('show')

// Resize observer to keep canvas crisp
const ro = new ResizeObserver(()=> { /* force a redraw next frame */ })
ro.observe(waveCanvas.parentElement || waveCanvas)
// Kick animation loop
requestAnimationFrame(drawWave)
