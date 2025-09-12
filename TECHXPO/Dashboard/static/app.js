const API_BASE = ''; // Use relative path to call API on the same host
let HOSPITALS = {};
let META = null; // Expect code-centric meta: {departments_by_code:{code:{name,doctors}}, slots:{...}}
let LAST_VERSION = null;
let POLL_TIMER = null;

// Inject minimal styles for held state if not already present
(() => {
  if(document.getElementById('held-style-tag')) return;
  const style = document.createElement('style');
  style.id='held-style-tag';
  style.textContent = `#table-container td.held{background:#ffeb99 !important;color:#444;font-weight:600;}
  #legend-held{display:inline-block;padding:2px 6px;background:#ffeb99;border:1px solid #f0d060;margin-left:6px;font-size:12px;border-radius:3px;}`;
  document.head.appendChild(style);
})();

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

function applyBookings(bookings, holds){
  // Reset previous state cells
  document.querySelectorAll('#table-container td.booked, #table-container td.held').forEach(td => { td.className='free'; td.textContent=''; });
  if(bookings){
    for(const code in bookings){
      const docs = bookings[code];
      for(const doc in docs){
        for(const slot of docs[doc]){
          const cell = document.querySelector(`#table-container td[data-code="${encodeURIComponent(code)}"][data-doc="${encodeURIComponent(doc)}"][data-slot="${slot}"]`);
          if(cell){ cell.classList.remove('free'); cell.classList.add('booked'); cell.textContent='X'; }
        }
      }
    }
  }
  if(holds){
    for(const code in holds){
      const docs = holds[code];
      for(const doc in docs){
        for(const slot of docs[doc]){
          const cell = document.querySelector(`#table-container td[data-code="${encodeURIComponent(code)}"][data-doc="${encodeURIComponent(doc)}"][data-slot="${slot}"]`);
          if(cell && !cell.classList.contains('booked')){ cell.classList.remove('free'); cell.classList.add('held'); cell.textContent='H'; }
        }
      }
    }
  }
  assignCellHandlers();
}

function attachCellHandlersBasic(){ assignCellHandlers(); }

function assignCellHandlers(){
  const hospital_code = document.getElementById('hospital_select').value;
  const date = document.getElementById('date').value || new Date().toISOString().slice(0,10);
  document.querySelectorAll('#table-container td[data-slot]').forEach(td => {
    // Overwrite previous handler
    td.onclick = null;
    if(td.classList.contains('booked')){
      td.onclick = async () => {
        await showVisitDetail(hospital_code, date, decodeURIComponent(td.dataset.doc), td.dataset.slot);
      };
    } else if(td.classList.contains('free')) {
      td.onclick = () => {
        openBookingDialog({
          hospital_code,
          date,
          department: decodeURIComponent(td.dataset.dep),
          department_code: td.dataset.code ? decodeURIComponent(td.dataset.code) : null,
          doctor_name: decodeURIComponent(td.dataset.doc),
          slot_time: td.dataset.slot
        });
      };
    }
  });
}

async function showVisitDetail(hospital_code, date, doctor_name, slot_time){
  fmtStatus('Load visit detail...');
  try {
    const url = `${API_BASE}/api/visit_detail?hospital_code=${encodeURIComponent(hospital_code)}&date=${encodeURIComponent(date)}&doctor_name=${encodeURIComponent(doctor_name)}&slot_time=${encodeURIComponent(slot_time)}`;
    const res = await fetch(url);
    if(res.status === 404){
      showModal({title: 'Thông báo', body: '<p>Chưa có dữ liệu wrap-up cho lịch này (đang xử lý hoặc chưa finalize).</p>'});
      return;
    }
    const data = await res.json();
    const p = data.payload || {};
    const booking = p.booking || {};
    let summaryRaw = data.summary || '';
    let summary = '';
    if(summaryRaw){
      const escapeHtml = (str='') => str
        .replace(/&/g,'&amp;')
        .replace(/</g,'&lt;')
        .replace(/>/g,'&gt;')
        .replace(/"/g,'&quot;')
        .replace(/'/g,'&#39;');
      
      // Gộp thành 1 dòng để cắt theo cặp 'Nhãn:'
      summaryRaw = summaryRaw.replace(/[\r\n]+/g,' ').replace(/\s{2,}/g,' ').trim();
      
      // Chuyển list JSON ["A","B"] -> A, B
      summaryRaw = summaryRaw.replace(/\[\s*"([^\]]+?)"\s*\]/g,(m,inner)=> inner.split(/"\s*,\s*"/).join(', '));
      
      // Regex cải thiện: cho phép nhãn dài hơn và nhiều ký tự đặc biệt hơn
      const re = /([A-Za-zÀ-ỹĐđ][A-Za-zÀ-ỹ0-9 ,\/()'"%-]{1,150}?):\s*/g;
      let match; 
      const labels=[]; // collect label metadata
      
      while((match = re.exec(summaryRaw))){ 
        const label = match[1].trim();
        // Bỏ qua các pattern không phải nhãn thực sự
        if(/^[0-9]{1,2}:[0-9]{2}$/.test(label)) continue; // thời gian
        if(/^[0-9]+$/.test(label)) continue; // chỉ số
        if(label.length < 3) continue; // quá ngắn
        
        labels.push({label, start:match.index, end:re.lastIndex}); 
      }
      
      const rows=[];
      for(let i=0;i<labels.length;i++){
        const cur = labels[i];
        const next = labels[i+1];
        let value = summaryRaw.slice(cur.end, next ? next.start : summaryRaw.length).trim();
        
        // Loại bỏ value quá ngắn hoặc rỗng
        if(!value || value.length < 2) continue;
        
        rows.push({label:cur.label, value});
      }
      
      if(rows.length === 0){
        // Fallback: chia câu -> mỗi câu 1 dòng (không có nhãn)
        const sentences = summaryRaw.split(/(?<=[.!?])\s+/).filter(s => s.trim().length > 0);
        summary = `<table class="summary-table">${sentences.map(s=>`<tr><td colspan='2'>${escapeHtml(s)}</td></tr>`).join('')}</table>`;
      } else {
        summary = `<table class="summary-table">${rows.map(r=>`<tr><th>${escapeHtml(r.label)}</th><td>${escapeHtml(r.value)}</td></tr>`).join('')}</table>`;
      }
    }
    
    let body = '';
    body += `<div class='kv'><span>Bệnh nhân:</span><strong>${p.patient_name || booking.patient_name || '(?)'}</strong></div>`;
    body += `<div class='kv'><span>Điện thoại:</span><strong>${p.phone || booking.phone || '(?)'}</strong></div>`;
    body += `<div class='kv'><span>Bác sĩ:</span><strong>${booking.doctor_name || p.doctor_name || '(?)'}</strong></div>`;
    body += `<div class='kv'><span>Thời gian:</span><strong>${booking.slot_time || p.slot_time || slot_time}</strong></div>`;
    
    if(p.summary_struct){
      try { 
        const ss = typeof p.summary_struct === 'string' ? JSON.parse(p.summary_struct) : p.summary_struct; 
        if(ss.tentative_diagnoses){ 
          body += `<div class='kv'><span>Chẩn đoán sơ bộ:</span><strong>${Array.isArray(ss.tentative_diagnoses)? ss.tentative_diagnoses.join(', '): ss.tentative_diagnoses}</strong></div>`;
        } 
      }catch(e){}
    }
    
    if(summary){ body += `<hr><div class='wrap-summary'>${summary}</div>`; }
    showModal({title:'Phiếu thăm khám', body});
  }catch(e){ 
    showModal({title:'Lỗi', body:'<p>Lỗi lấy dữ liệu visit.</p>'}); 
  }
}

function ensureModalStyles(){
  if(document.getElementById('modal-style-tag')) return;
  const style = document.createElement('style');
  style.id='modal-style-tag';
  style.textContent = `
  .modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.35);display:flex;align-items:center;justify-content:center;z-index:9999;}
  .modal{background:#fff;min-width:340px;max-width:520px;width:55%;border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.25);animation:pop .25s ease;display:flex;flex-direction:column;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;}
  .modal-header{padding:12px 16px;border-bottom:1px solid #eee;display:flex;align-items:center;justify-content:space-between;font-weight:600;font-size:15px;}
  .modal-close{background:none;border:none;font-size:18px;cursor:pointer;line-height:1;color:#555;}
  .modal-body{padding:16px;max-height:60vh;overflow:auto;font-size:14px;}
  .modal-footer{padding:10px 16px;border-top:1px solid #eee;text-align:right;}
  .modal-footer button{padding:6px 14px;border:1px solid #1976d2;background:#1976d2;color:#fff;border-radius:4px;cursor:pointer;font-size:13px;}
  .modal-footer button:hover{background:#125ea6;border-color:#125ea6;}
  .kv{display:flex;gap:6px;margin:4px 0;font-size:13px;text-align:left;}
  .kv span{color:#555;min-width:125px;}
  .wrap-summary{white-space:pre-wrap;font-size:13px;line-height:1.4;color:#222;text-align:left;}
  .wrap-summary .sum-line{font-size:13px;line-height:1.45;}
  .wrap-summary .sum-line strong{font-weight:600;color:#111;display:inline-block;min-width:140px;}
  .wrap-summary .sum-line .sum-val{color:#444;font-weight:400;}
  .wrap-summary .summary-table{width:100%;border-collapse:collapse;margin-top:4px;font-size:13px;}
  .wrap-summary .summary-table th{background:#f5f7fa;text-align:left !important;vertical-align:top;padding:4px 6px;font-weight:600;width:38%;border:1px solid #e2e5e9;}
  .wrap-summary .summary-table td{padding:4px 6px;border:1px solid #e2e5e9;color:#333;text-align:left !important;}
  .wrap-summary .summary-table tr:nth-child(even) th{background:#eef2f6;}
  .wrap-summary .summary-table tr:hover td,.wrap-summary .summary-table tr:hover th{background:#fffbe6;}
  @keyframes pop{0%{transform:scale(.92);opacity:0;}100%{transform:scale(1);opacity:1;}}
  `;
  document.head.appendChild(style);
}

function showModal({title, body, actions}={}){
  ensureModalStyles();
  const overlay = document.createElement('div');
  overlay.className='modal-overlay';
  const modal = document.createElement('div');
  modal.className='modal';
  modal.innerHTML = `
    <div class='modal-header'><div>${title||''}</div><button class='modal-close' aria-label='Đóng'>&times;</button></div>
    <div class='modal-body'>${body||''}</div>
    <div class='modal-footer'><button class='modal-close-btn'>Đóng</button></div>
  `;
  const closeAll = ()=> overlay.remove();
  modal.querySelector('.modal-close').onclick = closeAll;
  modal.querySelector('.modal-close-btn').onclick = closeAll;
  overlay.onclick = (e)=>{ if(e.target === overlay) closeAll(); };
  document.addEventListener('keydown', function esc(e){ if(e.key==='Escape'){ closeAll(); document.removeEventListener('keydown', esc);} });
  overlay.appendChild(modal); document.body.appendChild(overlay);
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
      applyBookings(data.bookings, data.holds);
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
