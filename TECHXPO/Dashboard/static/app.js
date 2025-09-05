const API_BASE = 'http://localhost:8090';
let HOSPITALS = {};
let META = null; // Expect code-centric meta: {departments_by_code:{code:{name,doctors}}, slots:{...}}
let LAST_VERSION = null;
let POLL_TIMER = null;

function fmtStatus(msg){
  document.getElementById('status').textContent = msg;
}

function buildInitialTable(){
  const container = document.getElementById('table-container');
  if(!META){ container.innerHTML = '<p>No meta</p>'; return; }
  const byCode = META.departments_by_code || {};
  if(Object.keys(byCode).length === 0){
    container.innerHTML = '<p>No departments_by_code found (expected code-centric data)</p>';
    return;
  }
  const slots = window.ALL_SLOTS;
  let html = '<table><thead><tr><th class="sticky">Time</th>';
  for(const s of slots){ html += `<th>${s}</th>`; }
  html += '</tr></thead><tbody>';
  for(const code of Object.keys(byCode)){
    const depObj = byCode[code];
    const disp = depObj.name || code;
    html += `<tr class="department-row"><td class="sticky">Doctor Name</td><td class="dept-name" colspan="${slots.length}">${disp} <span class="code-badge">${code}</span></td></tr>`;
    for(const doc of depObj.doctors || []){
      html += `<tr data-row-doc="${encodeURIComponent(doc)}" data-row-dep="${encodeURIComponent(disp)}" data-row-code="${encodeURIComponent(code)}"><td class="sticky">${doc}</td>`;
      for(const s of slots){
        html += `<td class="free" data-doc="${encodeURIComponent(doc)}" data-dep="${encodeURIComponent(disp)}" data-code="${encodeURIComponent(code)}" data-slot="${s}"></td>`;
      }
      html += '</tr>';
    }
  }
  html += '</tbody></table>';
  container.innerHTML = html;
  attachCellHandlersBasic();
}

function applyBookings(bookings){
  // bookings: { CODE: { doctor: [slots] } }
  const slots = window.ALL_SLOTS;
  // Clear existing booked marks
  document.querySelectorAll('#table-container td.booked').forEach(td => { td.classList.remove('booked'); td.classList.add('free'); td.textContent=''; });
  if(!bookings) return;
  for(const code in bookings){
    const docs = bookings[code];
    for(const doc in docs){
      const slotList = docs[doc];
      const bookedSet = new Set(slotList);
      for(const s of slots){
        // no-op loop, kept for potential diff highlighting
      }
      slotList.forEach(slot => {
        const cell = document.querySelector(`#table-container td[data-code="${encodeURIComponent(code)}"][data-doc="${encodeURIComponent(doc)}"][data-slot="${slot}"]`);
        if(cell){
          cell.classList.remove('free'); cell.classList.add('booked'); cell.textContent='X';
          // Attach click for visit detail if not already
          if(!cell.dataset.visitBound){
            cell.addEventListener('click', async () => {
              const hospital_code = document.getElementById('hospital_select').value;
              const date = document.getElementById('date').value || new Date().toISOString().slice(0,10);
              const doctor_name = decodeURIComponent(cell.dataset.doc);
              const slot_time = cell.dataset.slot;
              await showVisitDetail(hospital_code, date, doctor_name, slot_time);
            });
            cell.dataset.visitBound = '1';
          }
        }
      });
    }
  }
}

function attachCellHandlersBasic(){
  document.querySelectorAll('#table-container td.free').forEach(td => {
    td.addEventListener('click', () => {
      const hospital_code = document.getElementById('hospital_select').value;
      const date = document.getElementById('date').value || new Date().toISOString().slice(0,10);
      openBookingDialog({
        hospital_code,
        date,
        department: decodeURIComponent(td.dataset.dep),
        department_code: td.dataset.code ? decodeURIComponent(td.dataset.code) : null,
        doctor_name: decodeURIComponent(td.dataset.doc),
        slot_time: td.dataset.slot
      });
    });
  });
  // Booked cells: fetch visit detail if available
  document.querySelectorAll('#table-container td.booked').forEach(td => {
    td.addEventListener('click', async () => {
      const hospital_code = document.getElementById('hospital_select').value;
      const date = document.getElementById('date').value || new Date().toISOString().slice(0,10);
      const doctor_name = decodeURIComponent(td.dataset.doc);
      const slot_time = td.dataset.slot;
      await showVisitDetail(hospital_code, date, doctor_name, slot_time);
    });
  });
}

async function showVisitDetail(hospital_code, date, doctor_name, slot_time){
  fmtStatus('Load visit detail...');
  try {
    const url = `${API_BASE}/api/visit_detail?hospital_code=${encodeURIComponent(hospital_code)}&date=${encodeURIComponent(date)}&doctor_name=${encodeURIComponent(doctor_name)}&slot_time=${encodeURIComponent(slot_time)}`;
    const res = await fetch(url);
    if(res.status === 404){
      popup(`<p>Chưa có dữ liệu wrap-up cho lịch này (đang xử lý hoặc chưa finalize).</p>`);
      return;
    }
    const data = await res.json();
    const p = data.payload || {};
    const booking = p.booking || {};
    const summary = (data.summary || '').replace(/\n/g,'<br>');
    let html = `<h3>Phiếu thăm khám</h3>`;
    html += `<p><b>Bệnh nhân:</b> ${p.patient_name || booking.patient_name || '(?)'}</p>`;
    html += `<p><b>Điện thoại:</b> ${p.phone || booking.phone || '(?)'}</p>`;
    html += `<p><b>Bác sĩ:</b> ${booking.doctor_name || p.doctor_name || '(?)'}</p>`;
    html += `<p><b>Thời gian:</b> ${booking.slot_time || p.slot_time || slot_time}</p>`;
    if(p.summary_struct){
       try{ const ss = typeof p.summary_struct === 'string' ? JSON.parse(p.summary_struct) : p.summary_struct; if(ss.tentative_diagnoses){ html += `<p><b>Chẩn đoán sơ bộ:</b> ${ss.tentative_diagnoses}</p>`; }}catch(e){}
    }
    if(summary){ html += `<hr><div class='wrap-summary'>${summary}</div>`; }
    popup(html);
  }catch(e){ popup('<p>Lỗi lấy dữ liệu visit.</p>'); }
}

function popup(inner){
  const wrap = document.createElement('div');
  wrap.className='dialog-backdrop';
  wrap.innerHTML = `<div class='dialog'><div class='dialog-body'>${inner}</div><div class='dialog-actions'><button id='dlg-close'>Đóng</button></div></div>`;
  wrap.querySelector('#dlg-close').onclick = ()=>wrap.remove();
  document.body.appendChild(wrap);
}

function openBookingDialog(payload){
  const tpl = document.getElementById('booking-dialog-template');
  const frag = tpl.content.cloneNode(true);
  const backdrop = frag.querySelector('.dialog-backdrop');
  const info = frag.querySelector('#dlg-info');
  const codeSeg = payload.department_code ? ` [${payload.department_code}]` : '';
  info.textContent = `${payload.doctor_name} | ${payload.department}${codeSeg} | ${payload.date} ${payload.slot_time}`;
  frag.querySelector('#dlg-confirm').onclick = () => { doBook(payload); backdrop.remove(); };
  frag.querySelector('#dlg-cancel').onclick = () => backdrop.remove();
  document.body.appendChild(frag);
}

async function doBook(payload){
  try{
  const body = {...payload};
  // If we have department_code use code-centric endpoint
  let endpoint = '/api/book';
  if(body.department_code){ endpoint = '/api/book_by_code'; }
  if(!body.department_code) delete body.department_code; // avoid null field
  const res = await fetch(`${API_BASE}${endpoint}`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    const j = await res.json();
    fmtStatus(`Book: ${j.message}`);
    await loadData();
  }catch(e){ fmtStatus('Book error'); }
}

async function fetchMeta(){
  const hospital_code = document.getElementById('hospital_select').value;
  if(!hospital_code){ return; }
  fmtStatus('Meta...');
  const res = await fetch(`${API_BASE}/api/meta?hospital_code=${encodeURIComponent(hospital_code)}`);
  const meta = await res.json();
  META = meta;
  window.ALL_SLOTS = meta.slots ? buildSlots(meta.slots.start, meta.slots.end, meta.slots.slot_minutes) : [];
  buildInitialTable();
}

function buildSlots(start, end, step){
  // start "07:40", end "16:40" inclusive
  const out = [];
  const toMin = t=>{const [h,m]=t.split(':').map(Number);return h*60+m;};
  const pad = n=> String(n).padStart(2,'0');
  let cur = toMin(start); const endM = toMin(end);
  while(cur <= endM){ out.push(`${pad(Math.floor(cur/60))}:${pad(cur%60)}`); cur += step; }
  return out;
}

async function pollBookings(){
  const hospital_code = document.getElementById('hospital_select').value;
  if(!hospital_code || !META){ return; }
  const date = document.getElementById('date').value || new Date().toISOString().slice(0,10);
  const byCode = META.departments_by_code || {};
  const codes = Object.keys(byCode).join(',');
  let url = `${API_BASE}/api/bookings_by_code?hospital_code=${encodeURIComponent(hospital_code)}&department_codes=${encodeURIComponent(codes)}&date=${encodeURIComponent(date)}`;
  if(LAST_VERSION !== null){ url += `&since=${LAST_VERSION}`; }
  try{
    const res = await fetch(url);
    const data = await res.json();
    if(!data.unchanged){
      LAST_VERSION = data.version;
      applyBookings(data.bookings);
      fmtStatus('Bookings v'+data.version+' @ '+ new Date().toLocaleTimeString());
    }
  }catch(e){ fmtStatus('Bookings poll error'); }
  POLL_TIMER = setTimeout(pollBookings, 5000);
}

document.getElementById('control-form').addEventListener('submit', async e => { e.preventDefault(); await fetchMeta(); LAST_VERSION=null; await pollBookings(); });
document.getElementById('date').value = new Date().toISOString().slice(0,10);

async function initHospitals(){
  try {
    const res = await fetch(`${API_BASE}/api/hospitals`);
    const data = await res.json();
    HOSPITALS = data.hospitals || {};
    const sel = document.getElementById('hospital_select');
    sel.innerHTML = '';
    Object.keys(HOSPITALS).sort().forEach(code => {
      const opt = document.createElement('option');
      opt.value = code; opt.textContent = code; sel.appendChild(opt);
    });
  sel.onchange = async () => { await fetchMeta(); LAST_VERSION=null; await pollBookings(); };
  if(sel.options.length){ sel.selectedIndex = 0; }
  await fetchMeta();
  await pollBookings();
  } catch(e){ fmtStatus('Load hospitals failed'); }
}
initHospitals();
