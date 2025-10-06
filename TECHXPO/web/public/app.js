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
  
  // Error handler function
  function handleLogoError() {
    logoImg.style.display = 'none'
    logoFallback.style.display = 'flex'
    logoContainer.classList.add('fallback')
    createFallbackFavicon()
  }
  
  // Set up error handler
  logoImg.onerror = handleLogoError
  
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
  testImg.onerror = handleLogoError
  testImg.src = logoPath
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
// Progress timeline elements (hidden until call)
const progressRoot = document.getElementById('flowProgress')
const progressWrapper = document.getElementById('flowWrapper')
let currentStage = 1

function setProgressStage(stage){
  if(!progressRoot) return
  const steps = Array.from(progressRoot.querySelectorAll('.flow-step'))
  const bar = progressRoot.querySelector('#flowBarFill')
  const maxStage = steps.length
  stage = Math.min(Math.max(1, stage), maxStage)
  const prev = currentStage
  currentStage = stage
  steps.forEach(step => {
    const s = parseInt(step.getAttribute('data-step'))
    step.classList.remove('active','completed')
    if(s < stage) step.classList.add('completed')
    else if(s === stage) step.classList.add('active')
    step.classList.remove('bursting')
  })
  // Compute dynamic bar start & width (thin line): start at center of first node
  requestAnimationFrame(()=>{
    const first = steps[0]?.querySelector('.flow-node')
    const target = steps[stage-1]?.querySelector('.flow-node')
    const last = steps[steps.length-1]?.querySelector('.flow-node')
    if(first && target && bar){
      const rectFirst = first.getBoundingClientRect()
      const rectTarget = target.getBoundingClientRect()
      const rectLast = last.getBoundingClientRect()
      const containerRect = progressRoot.getBoundingClientRect()
      const startX = rectFirst.left + rectFirst.width/2 - containerRect.left
      const endX = rectTarget.left + rectTarget.width/2 - containerRect.left
      const width = Math.max(0, endX - startX)
      bar.style.left = startX + 'px'
      bar.style.width = width + 'px'
      // Update base line custom properties to stretch EXACTLY from first to last
      const baseStart = startX
      const baseEnd = rectLast.left + rectLast.width/2 - containerRect.left
      const baseWidth = Math.max(0, baseEnd - baseStart)
      progressRoot.style.setProperty('--base-left', baseStart + 'px')
      progressRoot.style.setProperty('--base-width', baseWidth + 'px')
      // Trigger charging animation only when advancing
      if(stage > prev){
        progressRoot.classList.remove('charging')
        void progressRoot.offsetWidth // restart animation
        progressRoot.classList.add('charging')
        // Burst effect on the newly active step
        const activeStep = steps[stage-1]
        if(activeStep){
          activeStep.classList.add('bursting')
          setTimeout(()=>activeStep.classList.remove('bursting'), 900)
        }
      }
    }
  })
}

// Initial stage
setTimeout(()=>setProgressStage(1), 0)
// Unified panel
const infoPanel = document.getElementById('infoPanel')
const infoTitle = document.getElementById('infoTitle')
const infoBody = document.getElementById('infoBody')
const infoActions = document.getElementById('infoActions')
const infoGuide = document.getElementById('infoGuide')
const centerNotice = document.getElementById('centerNotice')
const centerNoticeText = document.getElementById('centerNoticeText')

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
  if(infoGuide){ infoGuide.innerHTML = '' }
  infoPanel.className = ''
  infoPanel.id = 'infoPanel' // ensure id intact (class reset)
  // Clear log
  logEl.innerHTML = ''
  // Flags
  identityConfirmed = false
  // Hide center notice
  if(centerNotice){ centerNotice.style.display = 'none'; if(centerNoticeText){ centerNoticeText.textContent = '' } }
}

// Smoothly transition guide content with slide/fade
function setGuide(html){
  if(!infoGuide) return
  const prev = infoGuide.querySelector('.guide-block')
  if(prev){
    prev.classList.add('leave')
    // Remove after animation ends
    prev.addEventListener('animationend', () => {
      if(prev && prev.parentElement) prev.parentElement.removeChild(prev)
    }, { once: true })
  }
  const block = document.createElement('div')
  block.className = 'guide-block'
  block.innerHTML = `
    <div><b>Hướng dẫn:</b></div>
    <div class="guide-content">${html}</div>
  `
  infoGuide.appendChild(block)
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
  document.body.classList.add('in-call')
  if(progressWrapper){
    progressWrapper.classList.remove('hidden')
    // Recalculate layout after becoming visible
    setTimeout(()=>setProgressStage(currentStage||1),50)
  }
  // Initial guidance before identity
  setGuide(`
    <ul style="margin:.25rem 0 0 1rem;">
      <li>Nói yêu cầu của bạn</li>
      <li>Đọc rõ họ tên và số điện thoại</li>
    </ul>
  `)
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

  // Pure white background already provided by #waveBar; optional faint center glow
  ctx.fillStyle = 'rgba(255,255,255,1)'
  ctx.fillRect(0,0,W,H)
  const cg = ctx.createRadialGradient(W/2,H/2,0,W/2,H/2,Math.max(W,H)/2)
  cg.addColorStop(0,'rgba(186,230,253,0.18)')
  cg.addColorStop(1,'rgba(186,230,253,0)')
  ctx.fillStyle = cg
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

  // Ocean blue vertical gradient for bars
  const barGrad = ctx.createLinearGradient(0,0,0,H)
  barGrad.addColorStop(0,'#0ea5e9')
  barGrad.addColorStop(.45,'#0284c7')
  barGrad.addColorStop(1,'#0369a1')
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
  ctx.fillStyle = `rgba(14,165,233,${0.06 + energy*0.12})`
  ctx.fillRect(0,0,W,H)
  // Flat detection retained internally (no text overlay to maximize visual area)
  if(energy < 0.02) { _flatCounter++; } else { _flatCounter = 0 }
  requestAnimationFrame(drawWave)
}

function showIdentity(data){
  infoPanel.classList.add('show')
  // Visual glow for propose / confirm handled externally by caller
  infoTitle.textContent = 'Thông tin bệnh nhân'
  // Unified layout (no background frame). Keep stacked label + large line style from original capture,
  // but force black text for identity lines and remove dark block visuals.
  infoBody.innerHTML = `<div class="identity-stack">\n    <div class="identity-field">\n      <span class='identity-label uplain'>Họ tên</span>\n      <span class='identity-line uplain-line'>${data.patient_name || '<i>(chưa)</i>'}</span>\n    </div>\n    <div class="identity-field">\n      <span class='identity-label uplain'>SĐT</span>\n      <span class='identity-line uplain-line'>${data.phone || '<i>(chưa)</i>'}</span>\n    </div>\n  </div>`
  infoActions.innerHTML = ''
  // Always allow edit (still sends correction event; server may decide reconfirm flow)
  const btn = document.createElement('button')
  btn.textContent = 'Sửa'
  btn.className='ghost'
  btn.style.fontSize='.6rem'
  btn.onclick = () => {
    const n = prompt('Tên', data.patient_name || '')
    const p = prompt('SĐT', data.phone || '')
    if(n||p){
      sendData({type:'identity_corrected', patient_name:n||data.patient_name, phone:p||data.phone})
    }
  }
  infoActions.appendChild(btn)
  infoActions.style.display='flex'
  // Update guidance after propose or confirm
  if(!identityConfirmed){
    setGuide(`
      <ul style="margin:.25rem 0 0 1rem;">
        <li>Kiểm tra kĩ lại tên và số điện thoại</li>
        <li>Nếu đúng rồi thì thông báo xác nhận <b>Đúng</b> với Medly</li>
      </ul>
    `)
  } else {
    setGuide(`Hãy trò chuyện với Medly để nêu các triệu chứng và các nhu cầu đặt lịch nhé`)
  }
}

function showBookingPending(){
  infoPanel.classList.add('show')
  infoTitle.textContent='Đặt lịch'
  infoBody.innerHTML='<span class="muted" style="font-size:.65rem;">Đang tìm lịch khám phù hợp...</span>'
  infoActions.style.display='none'
  // Center notice while booking
  if(centerNotice && centerNoticeText){
    centerNoticeText.textContent = 'Quá trình đặt lịch của Medly có thể diễn ra khoảng 30 giây tới 1 phút. Vui lòng đợi.'
    centerNotice.style.display = 'flex'
  }
}
function showBooking(result, showChoiceGuide = true){
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
    infoBody.innerHTML = `<div class='booking-single' style='display:grid;gap:.35rem;'>
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
  // Hide center notice after options appear
  if(centerNotice){ centerNotice.style.display = 'none' }
  // Guidance after schedules appear - only if requested and multiple options
  if(showChoiceGuide && multi && !payload.chosen) {
    setGuide(`Hãy nói với Medly bạn muốn chọn lịch nào.`)
  }
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
          triggerStartOverlayTransition(()=>{ showCall(); startTimer(); })
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
          // Stage 1 still active
          setProgressStage(1)
          break
        case 'identity_confirmed':
          log('Identity xác nhận');
          identityConfirmed=true; showIdentity(msg);
          infoPanel.classList.remove('glow-blue','glow-amber','glow-purple')
          infoPanel.classList.add('glow-green') // confirm -> green
          // Move to stage 2 (collect request)
          setProgressStage(2)
          break
        case 'identity_updated': 
          log('Identity cập nhật'); identityConfirmed=true; showIdentity(msg); 
          infoPanel.classList.remove('glow-blue','glow-amber','glow-purple')
          infoPanel.classList.add('glow-green')
          setProgressStage(2)
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
        case 'booking_pending':
          log('Đang đặt lịch');
          showBookingPending();
          infoPanel.classList.remove('glow-green','glow-purple')
          infoPanel.classList.add('glow-amber') // searching -> amber
          // Move to stage 3 (scheduling)
          setProgressStage(3)
          break
        case 'booking_result':
          log('Đặt lịch xong');
          showBooking(msg, false); // Don't auto-show guide in showBooking
          // list options -> purple highlight
          infoPanel.classList.remove('glow-amber','glow-green')
          infoPanel.classList.add('glow-purple')
          // Stay in stage 3 (still choosing)
          setProgressStage(3)
          // Always show choice guidance after booking results
          setGuide(`Hãy nói với Medly bạn muốn chọn lịch nào.`)
          break
        case 'booking_option_chosen':
          log('Đã chọn 1 phương án');
          showBooking(msg.booking || msg, false); // Don't show choice guide since option is already chosen
          // finalize chosen -> green
          infoPanel.classList.remove('glow-amber','glow-blue','glow-purple')
          infoPanel.classList.add('glow-green')
          // Move to stage 4 (finished)
          setProgressStage(4)
          // Final guidance before wrapup
          setGuide(`Khi Medly thông báo kết thúc cuộc gọi và chào bạn thì hãy chào lại Medly nhé.`)
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
  // Prepare overlay so khi audio tới có thể animate ngay (tính tâm & bán kính hiện tại)
  prepareStartOverlay()
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
    setTimeout(()=>{ if(!firstRemoteAudio){
        log('Không thấy audio, vào giao diện (fallback)')
        triggerStartOverlayTransition(()=>{ showCall(); startTimer(); })
      } }, 6000)
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
  if(progressWrapper){ progressWrapper.classList.add('hidden') }
  document.body.classList.remove('in-call')
  if(!silent){
    showLanding();
    startBtn.disabled=false; firstRemoteAudio=false; startBtn.textContent='BẮT ĐẦU';
    const sm=document.createElement('span'); sm.className='small'; sm.textContent='Cho phép Micro'; startBtn.appendChild(sm); setProgressStage(1)
  } else { showLanding(); }
}

function mute(){ if(!localTrack) return; localTrack.mute(); btnMute.classList.add('hidden'); btnUnmute.classList.remove('hidden'); log('Mic OFF') }
function unmute(){ if(!localTrack) return; localTrack.unmute(); btnUnmute.classList.add('hidden'); btnMute.classList.remove('hidden'); log('Mic ON') }

btnMute.onclick = mute
btnUnmute.onclick = unmute
if(btnHangup) btnHangup.onclick = () => hangup()
startBtn.addEventListener('click', startCall)
btnLog.onclick = () => { logPanel.classList.toggle('show') }

// =========== Start Button Overlay Expansion (new implementation) ============
function ensureOverlayEl(){
  let ov = document.getElementById('startExpandOverlay')
  if(!ov){
    ov = document.createElement('div')
    ov.id = 'startExpandOverlay'
    document.body.appendChild(ov)
  }
  return ov
}
function prepareStartOverlay(){
  if(!startBtn) return
  const rect = startBtn.getBoundingClientRect()
  const landingRect = landing.getBoundingClientRect()
  
  // Tính vị trí chính xác của nút, bù trừ cho transform: translateY() của #landing
  const cx = rect.left + rect.width/2
  const cy = rect.top + rect.height/2
  
  document.body.style.setProperty('--cx', cx+'px')
  document.body.style.setProperty('--cy', cy+'px')
  document.body.style.setProperty('--r0', (Math.max(rect.width, rect.height)/2 + 8)+'px')
  ensureOverlayEl()
}
function triggerStartOverlayTransition(cb){
  if(!startBtn){ if(cb) cb(); return }
  prepareStartOverlay()
  document.body.classList.add('transitioning')
  startBtn.classList.add('expanding')
  // Ensure overlay present
  ensureOverlayEl()
  const ov = document.getElementById('startExpandOverlay')
  // Expand duration tăng từ 1.1s lên 1.6s để chậm hơn
  let durationMs = 1800
  setTimeout(()=>{
    startBtn.classList.remove('expanding')
    document.body.classList.remove('transitioning')
    if(cb) cb()
  }, durationMs)
}

btnCloseLog.onclick = () => logPanel.classList.remove('show')

// Resize observer to keep canvas crisp
const ro = new ResizeObserver(()=> { /* force a redraw next frame */ })
ro.observe(waveCanvas.parentElement || waveCanvas)
// Kick animation loop
requestAnimationFrame(drawWave)
