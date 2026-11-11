"""Framework-agnostic spatial relationship audits, backed by browser geometry.

Generic explicit contract:
  data-aae-align-with="#other-id" data-aae-align="center-y|center-x|center|left|right|top|bottom"
  data-aae-tolerance="6" (optional, CSS pixels)
  data-aae-node on the outer diagram node
  data-aae-port-role="anchor|button|handle", data-aae-port-side="in|out"
Known graph adapters: React Flow and generic port-anchor / port-handle markup.
No click, no mutation and no credentials/form values are read.
"""
from typing import Any, Dict

SPATIAL_EXTRACTOR_JS = r"""
(() => {
  const findings = [], observed = [];
  const MAX = 120, MAX_ISSUES = 40;
  const fixed = n => Math.round(n * 10) / 10;
  const rect = el => {
    const r = el.getBoundingClientRect();
    return {left:r.left, right:r.right, top:r.top, bottom:r.bottom,
            width:r.width, height:r.height, cx:r.left+r.width/2, cy:r.top+r.height/2};
  };
  const visible = el => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    const c = getComputedStyle(el);
    if (c.visibility === 'hidden' || c.display === 'none' || Number(c.opacity) === 0) return false;
    return !el.closest('[hidden],[inert],[aria-hidden="true"]');
  };
  const selector = el => {
    if (el.id && /^[a-zA-Z][\w-]*$/.test(el.id)) return '#' + el.id;
    const parts = [];
    for (let cur=el; cur && cur !== document.body && parts.length < 5; cur=cur.parentElement) {
      let s = cur.localName;
      const sib = cur.parentElement ? [...cur.parentElement.children].filter(x=>x.localName===cur.localName) : [];
      if (sib.length>1) s += ':nth-of-type(' + (sib.indexOf(cur)+1) + ')';
      parts.unshift(s);
    }
    return 'body > ' + parts.join(' > ');
  };
  const push = (code, severity, el, message, detail, recommendation) => {
    if (findings.length >= MAX_ISSUES) return;
    findings.push({
      id: code + '-' + (findings.length+1), ruleId: code,
      category:'layout', severity, title:message, target:selector(el),
      evidence:'OBSERVED: ' + detail,
      recommendation, acceptance:'Sai lệch giữa các tâm/cạnh không vượt quá ngưỡng, tại cùng viewport và trạng thái.',
      confidence:'observed', source:'spatial-relations'
    });
  };
  const delta = (a,b) => fixed(Math.abs(a-b));
  const checks = ['center','center-x','center-y','left','right','top','bottom'];

  // Explicit cross-component contracts work on ordinary web/desktop-like UIs,
  // independent of any framework or class names.
  for (const el of [...document.querySelectorAll('[data-aae-align-with]')].slice(0,MAX)) {
    if (!visible(el)) continue;
    const ref = el.getAttribute('data-aae-align-with');
    let target;
    try { target = ref ? document.querySelector(ref) : null; } catch { target = null; }
    if (!visible(target)) continue;
    const axis = el.getAttribute('data-aae-align') || 'center-y';
    if (!checks.includes(axis)) continue;
    const tolerance = Math.min(30,Math.max(1,Number(el.getAttribute('data-aae-tolerance')) || 6));
    const a=rect(el), b=rect(target);
    const errors = {
      center:Math.hypot(a.cx-b.cx,a.cy-b.cy),
      'center-x':delta(a.cx,b.cx), 'center-y':delta(a.cy,b.cy),
      left:delta(a.left,b.left), right:delta(a.right,b.right),
      top:delta(a.top,b.top), bottom:delta(a.bottom,b.bottom)
    };
    const deviation=fixed(errors[axis]);
    observed.push({kind:'explicit-alignment',element:selector(el),with:selector(target),axis,
      deviation, tolerance, verified:deviation<=tolerance});
    if (deviation>tolerance)
      push('SPATIAL-ALIGN', 'high', el, 'Hai thành phần liên quan bị lệch vị trí',
        'Quan hệ '+axis+', lệch '+deviation+'px (cho phép '+tolerance+'px); đối chiếu '+selector(target),
        'Căn lại hai thành phần theo quan hệ khai báo hoặc điều chỉnh constraint của layout.');
  }

  const nodes = '[data-aae-node],.react-flow__node,.canvas-node,.diagram-node,[data-graph-node]';
  const portAnchors = '[data-aae-port-role="anchor"],.single-port-anchor,[class*="port-anchor"]';
  let examined=0;
  for (const anchor of [...document.querySelectorAll(portAnchors)]) {
    if (examined >= MAX) break;
    if (!visible(anchor)) continue;
    const node=anchor.closest(nodes);
    if (!node || !visible(node)) continue;
    const button=anchor.querySelector('[data-aae-port-role="button"],button,[role="button"]');
    const handles=[...anchor.querySelectorAll('[data-aae-port-role="handle"],.react-flow__handle,[class*="port-handle"]')].filter(visible);
    if (!button || !visible(button) || handles.length===0) continue;
    examined++;
    const n=rect(node), a=rect(anchor), b=rect(button);
    const handle=handles.map(el=>({el,r:rect(el)})).sort((x,y)=>
      Math.hypot(x.r.cx-b.cx,x.r.cy-b.cy)-Math.hypot(y.r.cx-b.cx,y.r.cy-b.cy))[0];
    const h=handle.r;
    const mismatch=fixed(Math.hypot(b.cx-h.cx,b.cy-h.cy));
    const tolerance=6; // measured screen pixels; handles should overlay their control
    const rawSide=(anchor.getAttribute('data-aae-port-side')||'').toLowerCase();
    const tokens=(' '+(anchor.className?.baseVal||anchor.className||'')+' ')
      .toLowerCase().split(/\s+/);
    const side = ['in','input','left'].includes(rawSide) ? 'in'
      : ['out','output','right'].includes(rawSide) ? 'out'
      : tokens.some(x=>['in','input','left'].includes(x)) ? 'in'
      : tokens.some(x=>['out','output','right'].includes(x)) ? 'out' : null;
    const declaredCenter=anchor.getAttribute('data-aae-layout')==='edge-center'
      || anchor.getAttribute('data-aae-align')==='edge-center'
      || tokens.includes('single-port-anchor');
    const edgeX=side==='in'?n.left:n.right;
    const edgeDeviation=side?delta(b.cx,edgeX):null;
    const centerDeviation=delta(b.cy,n.cy);
    observed.push({kind:'port-handle',node:selector(node),anchor:selector(anchor),
      side,buttonCenter:{x:fixed(b.cx),y:fixed(b.cy)},
      handleCenter:{x:fixed(h.cx),y:fixed(h.cy)},
      nodeCenterY:fixed(n.cy),mismatch,edgeDeviation,centerDeviation,
      declaredCenter,verified:mismatch<=tolerance&&(!declaredCenter ||
        (side!==null&&edgeDeviation<=6&&centerDeviation<=6))});
    if (mismatch > tolerance)
      push('SPATIAL-PORT-HANDLE','high',anchor,'Cổng điều khiển lệch điểm kết nối thật',
        'Tâm nút và handle lệch '+mismatch+'px; nút '+selector(button)+', handle '+selector(handle.el)+
        ', node '+selector(node)+'.',
        'Đồng bộ tọa độ tâm nút và handle (flex order, left/right, transform) để dây đi đúng vị trí.');
    if (declaredCenter && side && (edgeDeviation > 6 || centerDeviation > 6))
      push('SPATIAL-PORT-EDGE','medium',anchor,'Cổng kết nối lệch khỏi cạnh hoặc tâm node',
        'Cạnh '+side+', lệch cạnh '+edgeDeviation+'px, lệch tâm dọc '+centerDeviation+
        'px trong viewport; node '+selector(node)+'.',
        'Căn cổng vào cạnh trái hoặc phải và giữa chiều cao node theo hợp đồng layout.');
  }
  return {status:'completed',checked:observed.length,findings,relations:observed,
    coverage:{explicit:observed.filter(x=>x.kind==='explicit-alignment').length,
              ports:observed.filter(x=>x.kind==='port-handle').length},
    limits:['Chỉ kiểm tra DOM nhìn thấy, tối đa 120 quan hệ.',
      'Không đo canvas/WebGL thuần hoặc Shadow DOM đóng.',
      'Không suy đoán căn giữa nếu node có nhiều cổng hoặc không khai báo hợp đồng.']};
})()
"""

def audit_spatial_relations(page: Any) -> Dict[str, Any]:
    """Read-only geometry assertions; caller records each viewport separately."""
    return page.evaluate(SPATIAL_EXTRACTOR_JS)
