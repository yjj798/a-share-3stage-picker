# -*- coding: utf-8 -*-
"""HTML 页面模板（单文件自包含，无外部依赖）

占位符:
    __DATE__   交易日期
    __DATA__   JSON 数组（每只股票的完整数据）
    __FRESH__  数据新鲜度说明
"""
HTML_TPL = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股观察 · __DATE__</title>
<style>
:root{--bg:#0f1115;--panel:#161a21;--line:#252b36;--tx:#e8ecf3;--tx2:#9aa4b5;--up:#ff5d5d;--down:#2fbf71;--accent:#5b9dff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,"Microsoft YaHei",sans-serif;font-size:13px}
.wrap{max-width:1560px;margin:0 auto;padding:20px}
header{display:flex;align-items:baseline;gap:14px;margin-bottom:12px}
h1{font-size:19px;margin:0}.sub{color:var(--tx2);font-size:12px}
.tabs{display:flex;gap:4px;margin-bottom:14px;border-bottom:1px solid var(--line)}
.tab{padding:8px 20px;cursor:pointer;color:var(--tx2);font-size:13px;border-bottom:2px solid transparent}
.tab.on{color:var(--tx);border-bottom-color:var(--accent);font-weight:600}
.toolbar{display:flex;gap:10px;margin-bottom:12px;align-items:center}
input{background:var(--panel);border:1px solid var(--line);color:var(--tx);padding:8px 12px;border-radius:8px;font-size:13px;outline:none;width:280px}
input:focus{border-color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:10px;overflow:hidden}
th{background:#1b2029;color:var(--tx2);font-weight:600;text-align:left;padding:9px 12px;font-size:12px;position:sticky;top:0;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:var(--tx);border-right:1px solid #313949}th{border-right:1px solid #313949}
td{padding:8px 12px;border-top:1px solid var(--line);border-right:1px solid #2a3140;font-variant-numeric:tabular-nums;white-space:nowrap}
td:last-child,th:last-child{border-right:none}
tr:hover td{background:rgba(91,157,255,.06)}
.up{color:var(--up)}.down{color:var(--down)}
.namecell{display:flex;flex-direction:column;line-height:1.3}
a.name{text-decoration:none;color:var(--tx)}a.name:hover{text-decoration:underline;color:var(--tx)}
a.code{text-decoration:none}a.code:hover{color:var(--accent);text-decoration:underline}
.launchcell{line-height:1.25;font-size:11px}
.launchcell .mini{font-size:10px}
th{line-height:1.2}
.name{font-weight:600;font-size:14px}.code{color:var(--tx2);font-size:12px}
.score{font-size:16px;font-weight:700;line-height:1.1}
.scorecell{display:flex;flex-direction:column;gap:2px;cursor:help;font-size:12px;font-weight:600;line-height:1.2}
.ts{color:#d8c62c}.hs{color:var(--accent);text-decoration:none}
a.hs:hover{text-decoration:underline}
.nbtn{cursor:pointer}
.nbtn:hover{color:var(--accent)}
a.name.nbtn:hover{color:var(--tx)}
.concept{color:var(--tx2);font-size:11px;white-space:normal;max-width:700px}
.concept-link{color:var(--tx2);text-decoration:none;border-bottom:1px dotted var(--tx2)}
.concept-link:hover{color:var(--accent);border-bottom-color:var(--accent)}
.ranklink{color:var(--accent);text-decoration:none;font-size:11px;font-weight:600}
.ranklink:hover{text-decoration:underline}
.heatvals span{font-variant-numeric:tabular-nums}
.price-link{color:var(--tx);text-decoration:none;font-weight:600}
.price-link:hover{color:var(--accent);text-decoration:underline}
.cbonus{color:#ff6a2b;font-weight:700;background:rgba(255,106,43,.1);padding:1px 5px;border-radius:4px}
/* 热榜页 */
.hothead{margin-bottom:10px}
.hotstat{font-size:13px;color:var(--tx2);margin-bottom:4px}
.hotstat b{color:var(--tx)}
.hotwrap{overflow-x:auto}
.hottable{font-size:11px}
.hottable th{padding:6px 4px;text-align:center;font-size:10px}
.hottable td{padding:5px 4px;text-align:center;border-right:1px solid #2a3140}
.hottable td:nth-child(2){text-align:left;white-space:nowrap}
.dcell{font-variant-numeric:tabular-nums;font-weight:600;position:relative;min-width:26px}
.arrup{font-size:8px;margin-left:1px}
.hbar{display:flex;flex-direction:column;align-items:flex-start;gap:3px}
.hbarchart{display:flex;align-items:flex-end;gap:1px;width:200px;height:26px;border-bottom:1px solid var(--line)}
.hbarchart i{flex:1;min-width:2px;border-radius:1px 1px 0 0;opacity:.85}
.mini{font-size:10px;color:var(--tx2)}
.tip{cursor:help;border-bottom:1px dotted var(--tx2)}
.kbtn{background:rgba(91,157,255,.12);border:1px solid rgba(91,157,255,.35);color:var(--accent);padding:2px 9px;border-radius:5px;font-size:11px;cursor:pointer}
.kbtn:hover{background:rgba(91,157,255,.25)}
.page{display:none}.page.on{display:block}
/* 弹窗 */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:99;align-items:center;justify-content:center}
.modal.on{display:flex}
.mbox{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;max-width:1100px;width:95%;max-height:90vh;overflow:auto}
.mhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.mhead h2{font-size:16px;margin:0}
.close{background:none;border:0;color:var(--tx2);font-size:22px;cursor:pointer}
.legend{display:flex;gap:14px;font-size:11px;color:var(--tx2);margin:8px 0}
.legend i{display:inline-block;width:12px;height:3px;vertical-align:middle;margin-right:4px}
/* 概念/热榜页 */
.cgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
.ccard{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:12px}
.ccard .cn{font-weight:600;font-size:13px}.ccard .cs{color:var(--accent);font-size:12px;margin-top:4px}
.ccard .cl{color:var(--tx2);font-size:11px;margin-top:6px}
</style></head><body><div class="wrap">
<header><h1>A股观察</h1><span class="sub">__DATE__ · 股池 + 技术 + 热度 + 概念 · <span id="fresh"></span></span></header>
<div class="tabs">
<div class="tab on" data-p="pool">股池</div>
<div class="tab" data-p="concept">概念</div>
<div class="tab" data-p="hot">热榜</div>
</div>

<div class="page on" id="p-pool">
<div class="toolbar"><input id="q" placeholder="搜索名称/代码/概念"><span id="cnt" class="mini"></span><span class="mini">评分=技术(0~100)+热度(0~50) · 点表头排序 · 点"日线"看技术图</span></div>
<table><thead><tr>
<th style="width:110px">股票</th><th style="width:58px" data-k="total">总分</th><th style="width:78px">技/热</th><th style="width:62px" data-k="close">股价</th><th style="width:62px" data-k="pct">涨跌</th>
<th style="width:100px" data-k="launch">放量日/距今</th>
<th style="width:300px">热度20日 <span style="font-weight:400;font-size:10px;white-space:nowrap"><span style="color:#ff0033">■</span>10 <span style="color:#ff2b2b">■</span>50 <span style="color:#ff6a2b">■</span>100 <span style="color:#ffb02e">■</span>200 <span style="color:#ffe14d">■</span>500 <span style="color:#5ce1a0">■</span>千 <span style="color:#4a9eff">■</span>2千</span></th><th>概念</th>
</tr></thead><tbody id="tb"></tbody></table>
</div>

<div class="page" id="p-concept"><div id="conceptBox" class="cgrid"></div></div>
<div class="page" id="p-hot"><div id="hotBox"></div></div>

</div>
<div class="modal" id="modal"><div class="mbox">
<div class="mhead"><h2 id="mtitle"></h2><button class="close" onclick="closeModal()">×</button></div>
<div class="legend"><span><i style="background:#ff5d5d"></i>收盘</span><span><i style="background:#d8c62c"></i>MA5</span><span><i style="background:#5b9dff"></i>MA10</span><span><i style="background:#a78bfa"></i>MA20</span><span><i style="background:#6b7280"></i>MA60</span><span style="color:#ff2b2b">★=建仓日(涨停/放量)</span></div>
<div id="mchart"></div>
<div id="minfo" class="mini" style="margin-top:10px"></div>
</div></div>
<script>
const DATA=__DATA__;
let sortK='total',sortD=-1;
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
// 概念点击→同花顺F10概念页（带该股代码）
function conceptLinks(r){
  const cs=(r.concepts||[]).slice(0,4);
  if(!cs.length) return '';
  const mc=r.mc||33;
  const url='https://basic.10jqka.com.cn/astockpc/astockmain/index.html#/concept?code='+r.code+'&marketid='+mc+'&code_name='+encodeURIComponent(r.name);
  return cs.map(c=>'<a class="concept-link" href="'+url+'" target="_blank" rel="noopener" title="查看'+esc(r.name)+'的同花顺概念">'+esc(c)+'</a>').join(' / ');
}
function render(){
  const q=document.getElementById('q').value.trim().toLowerCase();
  let rows=DATA.filter(r=>!q||r.name.toLowerCase().includes(q)||r.code.includes(q)||(r.concepts||[]).some(c=>c.toLowerCase().includes(q)));
  rows.sort((a,b)=>((a[sortK]||0)-(b[sortK]||0))*sortD);
  document.getElementById('cnt').textContent=rows.length+' 只';
  const mx=Math.max(...DATA.map(r=>r.heat||0),1);
  document.getElementById('tb').innerHTML=rows.map(r=>{
    const gc=r.total>=120?'#ff2b2b':r.total>=90?'#ff6a2b':r.total>=70?'#d8c62c':r.total>=50?'#5b9dff':'#8a94a6';
    const pcls=r.pct>0?'up':r.pct<0?'down':'';
    const ps=r.pct>0?'+':'';
    const tip='技术: '+r.tdet+' | 热度: '+r.hdet+(r.launch?' | 建仓:'+r.launch:'')+(r.hlow?' | 最低热度#'+r.hlow:'');
    const series=r.heat_series||[];
    let bars='';
    if(series.length){
      // series 是 [[日期, 排名]] 或 [排名]（兼容旧数据）
      const vals=series.map(x=>Array.isArray(x)?x[1]:x);
      const smax=Math.max(...vals),smin=Math.min(...vals);
      bars=series.map(x=>{
        const v=Array.isArray(x)?x[1]:x;
        const dt=Array.isArray(x)?x[0]:'';
        const h=(smax-v)/(smax-smin||1)*100;
        // 冷→热 七档配色（排名越小越热）
        let col;
        if(v<=10)      col='#ff0033';  // 前10：最红
        else if(v<=50) col='#ff2b2b';  // 前50：红
        else if(v<=100)col='#ff6a2b';  // 前100：橙
        else if(v<=200)col='#ffb02e';  // 前200：橙黄
        else if(v<=500)col='#ffe14d';  // 前500：黄
        else if(v<=1000)col='#5ce1a0'; // 前1000：青绿
        else if(v<=2000)col='#4a9eff'; // 前2000：蓝
        else           col='#5a6478';  // 2000后：冷灰蓝
        const dtxt=dt?dt.slice(4,6)+'-'+dt.slice(6,8):'';
        return '<i style="height:'+Math.max(6,h)+'%;background:'+col+'" title="'+(dtxt?dtxt+' ':'')+'热度#'+v+'"></i>';
      }).join('');
    }
    return '<tr>'
      +'<td><div class="namecell"><a class="name nbtn" href="https://finance.baidu.com/stock/ab-'+r.code+'" target="_blank" rel="noopener" title="百度股市通行情">'+esc(r.name)+'</a><br><a class="code nbtn" href="https://www.xueqiu.com/S/'+((r.mc||33)==17?'SH':'SZ')+r.code+'" target="_blank" rel="noopener" title="雪球行情">'+r.code+'</a></div></td>'
      +'<td title="'+esc(tip)+'"><span class="score" style="color:'+gc+'">'+r.total+'</span></td>'
      +'<td><div class="scorecell">'
      +'<span class="ts nbtn" onclick="showK(\''+r.code+'\')" title="技术:'+esc(r.tdet)+'｜点击查看日线图">技 '+r.tech+'</span>'
      +'<a class="hs nbtn" href="https://ms.10jqka.com.cn/thsHotDetail/?name='+encodeURIComponent(r.name)+'&code='+r.code+'&marketCode='+(r.mc||33)+'" target="_blank" rel="noopener" title="热度:'+esc(r.hdet)+(r.hrank?' 当前#'+r.hrank:'')+'｜点击查看同花顺热度页">热 '+r.heat+'</a></div></td>'
      +'<td><a class="price-link" href="https://stockpage.10jqka.com.cn/'+r.code+'/" target="_blank" rel="noopener" title="实时价'+(r.rt_time?'('+r.rt_time.slice(8,14)+')':'')+'｜点击同花顺行情页">'+Number(r.close).toFixed(2)+'</a></td>'
      +'<td class="'+pcls+'">'+ps+Number(r.pct).toFixed(1)+'%</td>'
      +'<td class="launchcell nbtn" onclick="showK(\''+r.code+'\')" title="点击查看日线技术图">'+(r.launch?'<span>'+r.launch.slice(4,6)+'-'+r.launch.slice(6,8)+'</span><br><span class="mini">'+(r.launch_days!=null?r.launch_days+'天前':'-')+'</span>':'<span class="mini">—</span>')+'</td>'
      +'<td><div class="hbar"><div class="hbarchart" title="20日热度：左旧→右新，悬停看具体排名｜颜色由冷到热">'+bars+'</div></div></td>'
      +'<td class="concept">'+(r.cbonus>0?'<span class="cbonus" title="'+esc(r.ctag||'')+'">🔥+'+r.cbonus+'</span> ':'')+conceptLinks(r)+'</td>'
      +'</tr>';
  }).join('');
}
// 日K SVG图：收盘线 + MA5/10/20/60 + 建仓星标 + 量柱
function showK(code){
  const r=DATA.find(x=>x.code===code); if(!r)return;
  const k=r.kline||[]; if(!k.length){alert('无日线数据');return}
  document.getElementById('mtitle').textContent=r.name+' '+code+' · 近'+k.length+'日';
  const W=1040,H=340,PL=48,PR=20,PT=20,PB=70;
  const iw=W-PL-PR, ih=H-PT-PB;
  // 价格范围
  let hi=-1e9,lo=1e9;
  k.forEach(p=>{ [p[1],p[3],p[4],p[5],p[6]].forEach(v=>{if(v!=null&&v>0){hi=Math.max(hi,v);lo=Math.min(lo,v)}}) });
  const pad=(hi-lo)*0.05; hi+=pad; lo-=pad;
  const X=i=>PL+i*(iw/k.length);
  const Y=v=>PT+ih-(v-lo)/(hi-lo)*ih;
  // 网格
  let g='';
  for(let t=0;t<=4;t++){
    const v=lo+(hi-lo)*t/4, y=Y(v);
    g+='<line x1="'+PL+'" y1="'+y+'" x2="'+(W-PR)+'" y2="'+y+'" stroke="#252b36" stroke-width="1"/>';
    g+='<text x="'+(PL-6)+'" y="'+(y+4)+'" fill="#9aa4b5" font-size="10" text-anchor="end">'+v.toFixed(2)+'</text>';
  }
  // 均线路径
  function path(idx,color){
    let d='';
    k.forEach((p,i)=>{ const v=p[idx]; if(v!=null&&v>0){ d+=(d?'L':'M')+X(i).toFixed(1)+','+Y(v).toFixed(1) } });
    return '<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="1.4" opacity=".9"/>';
  }
  let ma=path(6,'#6b7280')+path(5,'#a78bfa')+path(4,'#5b9dff')+path(3,'#d8c62c');
  // 收盘折线
  let cl='';
  k.forEach((p,i)=>{ cl+=(i?'L':'M')+X(i).toFixed(1)+','+Y(p[1]).toFixed(1) });
  ma+='<path d="'+cl+'" fill="none" stroke="#ff5d5d" stroke-width="2"/>';
  // 建仓星标 + 涨停点
  k.forEach((p,i)=>{
    if(p[8]===1){
      ma+='<circle cx="'+X(i)+'" cy="'+Y(p[1])+'" r="5" fill="#ff2b2b" stroke="#fff" stroke-width="1"/>';
      ma+='<text x="'+X(i)+'" y="'+(Y(p[1])-10)+'" fill="#ff2b2b" font-size="12" text-anchor="middle">★'+p[2].toFixed(0)+'%</text>';
    }
  });
  // 量柱（底部）
  const vmax=Math.max(...k.map(p=>p[7]||0),1);
  k.forEach((p,i)=>{
    const h=(p[7]||0)/vmax*44;
    const col=p[2]>0?'rgba(255,93,93,.6)':'rgba(47,191,113,.6)';
    ma+='<rect x="'+(X(i)-3)+'" y="'+(H-24-h)+'" width="6" height="'+h+'" fill="'+col+'"/>';
  });
  // 日期轴
  for(let t=0;t<k.length;t+=Math.max(1,Math.floor(k.length/6))){
    ma+='<text x="'+X(t)+'" y="'+(H-6)+'" fill="#9aa4b5" font-size="9" text-anchor="middle">'+k[t][0].slice(4)+'</text>';
  }
  document.getElementById('mchart').innerHTML='<svg width="'+W+'" height="'+H+'" style="max-width:100%">'+g+ma+'</svg>';
  document.getElementById('minfo').textContent='建仓标志:'+(r.launch||'无')+' | 技术明细:'+r.tdet+' | 当前热度#'+(r.hrank||'-')+' 最低#'+(r.hlow||'-');
  document.getElementById('modal').classList.add('on');
}
function closeModal(){document.getElementById('modal').classList.remove('on')}
document.getElementById('modal').onclick=function(e){if(e.target===this)closeModal()};
// tabs
function renderConcept(){
  const counts={};
  DATA.forEach(r=>(r.concepts||[]).forEach(c=>{counts[c]=(counts[c]||0)+1}));
  const arr=Object.entries(counts).sort((a,b)=>b[1]-a[1]);
  document.getElementById('conceptBox').innerHTML=arr.slice(0,120).map(([c,n])=>{
    const codes=DATA.filter(r=>(r.concepts||[]).includes(c)).slice(0,5);
    return '<div class="ccard"><div class="cn">'+esc(c)+'</div><div class="cs">'+n+' 只</div><div class="cl">'+codes.map(r=>esc(r.name)).join(' · ')+'</div></div>';
  }).join('');
}
// 颜色档（冷→热）
function heatColor(v){
  return v<=10?'#ff0033':v<=50?'#ff2b2b':v<=100?'#ff6a2b':v<=200?'#ffb02e'
        :v<=500?'#ffe14d':v<=1000?'#5ce1a0':v<=2000?'#4a9eff':'#5a6478';
}
function renderHot(){
  // 全部有热度的票，按当前排名排序
  const arr=DATA.filter(r=>r.hrank!=null).sort((a,b)=>a.hrank-b.hrank);
  let h='<div class="hothead">'
       +'<div class="hotstat">当前有热度 <b>'+arr.length+'</b> 只 · 前50 <b>'+arr.filter(r=>r.hrank<=50).length+'</b> 只 · 前200 <b>'+arr.filter(r=>r.hrank<=200).length+'</b> 只</div>'
       +'<div class="mini">20日每日排名明细（左旧→右新），颜色由冷到热；↓箭头=比前一日降温</div></div>';
  h+='<div class="hotwrap"><table class="hottable"><thead><tr><th>当前<br>排名</th><th>股票</th><th>热度分</th><th>总评分</th>';
  // 20列表头用日期简写
  const sample=arr[0]&&arr[0].heat_series||[];
  sample.forEach(x=>{const dt=Array.isArray(x)?x[0]:'';h+='<th>'+dt.slice(6,8)+'</th>';});
  h+='<th>趋势</th></tr></thead><tbody>';
  arr.slice(0,300).forEach(r=>{
    const s=r.heat_series||[];
    h+='<tr><td><b style="color:'+heatColor(r.hrank)+'">#'+r.hrank+'</b></td>'
      +'<td><span class="name">'+esc(r.name)+'</span><br><span class="code">'+r.code+'</span></td>'
      +'<td class="hs" style="color:'+(r.heat>=35?'#ff5d5d':r.heat>=20?'#ffb02e':r.heat>0?'#5ce1a0':'#5a6478')+'">'+r.heat+'</td>'
      +'<td>'+r.total+'</td>';
    // 20个日格
    s.forEach((x,i)=>{
      const v=Array.isArray(x)?x[1]:x;
      const prev=i>0?(Array.isArray(s[i-1])?s[i-1][1]:s[i-1]):null;
      const up=prev!=null&&v<prev;   // 排名变小=升温
      h+='<td class="dcell" style="background:'+heatColor(v)+'22;color:'+heatColor(v)+'" title="热度#'+v+(up?' ↑升温':' ↓降温')+'">'+v+(up?'<span class="arrup">↑</span>':'')+'</td>';
    });
    // 趋势：最近5日 vs 前5日
    const vals=s.map(x=>Array.isArray(x)?x[1]:x);
    let trend='—',tc='#9aa4b5';
    if(vals.length>=10){
      const a=vals.slice(-10,-5).reduce((p,c)=>p+c,0)/5;
      const b=vals.slice(-5).reduce((p,c)=>p+c,0)/5;
      if(b<a*0.7){trend='升温↑';tc='#ff5d5d';}
      else if(b>a*1.4){trend='降温↓';tc='#4a9eff';}
      else {trend='平稳→';tc='#9aa4b5';}
    }
    h+='<td style="color:'+tc+';font-weight:600">'+trend+'</td>';
    h+='</tr>';
  });
  document.getElementById('hotBox').innerHTML=h+'</tbody></table></div>';
}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('on'));
  t.classList.add('on');
  document.getElementById('p-'+t.dataset.p).classList.add('on');
  if(t.dataset.p==='concept')renderConcept();
  if(t.dataset.p==='hot')renderHot();
});
document.querySelectorAll('th[data-k]').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; if(sortK===k)sortD=-sortD;else{sortK=k;sortD=-1} render();
});
document.getElementById('q').oninput=render;
render();
</script></body></html>'''


