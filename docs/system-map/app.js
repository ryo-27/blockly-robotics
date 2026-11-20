/* 図・解説・ファイル索引は同じ概念IDを参照し、視点を変えても説明を共有する。 */
(() => {
  "use strict";
  const data = window.SYSTEM_MAP;
  const $ = (selector) => document.querySelector(selector);
  const nodes = new Map(data.nodes.map((node) => [node.id, node]));
  const kinds = {client:"端末",server:"サーバー",data:"保存・状態",robot:"ESP32・出力",config:"設定",planned:"未実装",test:"検証"};
  const viewMemory = new Map();
  const dimensions = {width:1040,height:740,nodeWidth:236,nodeHeight:118};
  let state = {view:"overview",node:"api",edge:null,file:null};
  let zoom = 1;
  let currentStep = 0;
  let activeView;
  let initialRender = true;

  function el(tag, attributes = {}, children = []) {
    const element = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (name === "class") element.className = value;
      else if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
      else if (value !== null && value !== undefined) element.setAttribute(name, value);
    }
    for (const child of [].concat(children)) {
      if (child !== null && child !== undefined) element.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return element;
  }
  function svg(tag, attributes = {}, children = []) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([name,value]) => element.setAttribute(name,value));
    children.forEach((child) => element.append(child));
    return element;
  }
  function route(view, options = {}) {
    const params = new URLSearchParams();
    if (options.node) params.set("node", options.node);
    if (options.edge !== null && options.edge !== undefined) params.set("edge", options.edge);
    if (options.file) params.set("file", options.file);
    return "#" + view + (params.size ? "?" + params : "");
  }
  function navigate(view, options = {}) { location.hash = route(view, options); }
  function announce(text) { $("#announcement").textContent = text; }
  function viewForNode(id) {
    const current = data.views.find((view) => view.id === state.view);
    if (current?.positions.some((position) => position.id === id)) return current.id;
    return data.views.find((view) => view.positions.some((position) => position.id === id))?.id || "files";
  }
  function selectNode(id) {
    navigate(viewForNode(id), {node:id});
    if (matchMedia("(max-width: 800px)").matches) {
      setTimeout(() => $("#inspector").scrollIntoView({behavior:"auto",block:"start"}), 0);
    }
  }
  const iconNames = {client:'monitor',server:'server',data:'database',robot:'chip',config:'sliders',planned:'clock',test:'check'};
  function icon(name, extraClass = '') {
    const paths = {
      monitor:['M3 4h18v12H3z','M8 21h8','M12 16v5'],
      server:['M4 3h16v7H4z','M4 14h16v7H4z','M7 6.5h.01M7 17.5h.01','M11 6.5h6M11 17.5h6'],
      database:['M20 5c0 2-3.6 3.5-8 3.5S4 7 4 5s3.6-3.5 8-3.5S20 3 20 5Z','M4 5v14c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5V5','M4 12c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5'],
      chip:['M6 6h12v12H6z','M9 9h6v6H9z','M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4'],
      sliders:['M4 5h16M4 12h16M4 19h16','M8 2v6M16 9v6M10 16v6'],
      clock:['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z','M12 6v6l4 2'],
      check:['M20 6 9 17l-5-5'],
      flow:['M5 3v12a4 4 0 0 0 4 4h11','M16 15l4 4-4 4','M2 3h6M2 9h6'],
      input:['M4 4h6M4 4v16h6','M10 12h11M17 8l4 4-4 4'],
      output:['M14 4h6v16h-6','M3 12h11M10 8l4 4-4 4'],
      code:['M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16'],
      file:['M14 2H5v20h14V7z','M14 2v6h5'],
      arrow:['M4 12h16M14 6l6 6-6 6'],
      chevron:['M9 5l7 7-7 7'],
      info:['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z','M12 11v6M12 7h.01'],
      layers:['m12 3 10 5-10 5L2 8z','m2 12 10 5 10-5M2 16l10 5 10-5']
    };
    return svg('svg',{viewBox:'0 0 24 24',class:'icon '+extraClass,fill:'none',stroke:'currentColor','stroke-width':1.6,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},(paths[name]||paths.file).map(d=>svg('path',{d})));
  }
  function rich(text) {
    const fragment = document.createDocumentFragment();
    // 技術的な識別子をコードとして示し、説明の日本語と読み分ける。
    const pattern = /(?:GET|POST|DELETE)\s+\/[A-Za-z0-9_/{}/.-]+|(?:uvicorn|uv|pio|python3)\s+[\x20-\x7e]+|https?:\/\/[^、。\n]+|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b|[A-Za-z_][A-Za-z0-9_.]*\([^()\n]*\)|[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.{}*-]+)+\/?|[A-Za-z0-9_-]+\.(?:h|cpp|py|json|ini|toml|lock|js|html|css)\b|[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*|\b(?:IDLE|OFFLINE|NULL|LOW|COMMIT|ROLLBACK)\b/g;
    let start=0;
    for(const match of text.matchAll(pattern)) {
      fragment.append(document.createTextNode(text.slice(start,match.index)));
      fragment.append(el('code',{class:'inline-code'},match[0]));
      start=match.index+match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(start)));
    return fragment;
  }
  function syntax(text) {
    const fragment=document.createDocumentFragment();
    const tokens=/(\/\/.*$|#.*$)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')|\b(class|def|return|if|else|elif|for|in|with|as|from|import|const|constexpr|void|bool|int|uint32_t|uint8_t|true|false|True|False|None|try|except|raise|yield|async|await)\b|\b(\d+(?:\.\d+)?)\b/g;
    let offset=0;
    for(const match of text.matchAll(tokens)) {
      fragment.append(document.createTextNode(text.slice(offset,match.index)));
      fragment.append(el('span',{class:match[1]?'syntax-comment':match[2]?'syntax-string':match[3]?'syntax-keyword':'syntax-number'},match[0]));
      offset=match.index+match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(offset)));
    return fragment;
  }
  function sectionTitle(title,name,extra) {
    return el('h3',{class:'section-title'},[icon(name),title,extra?el('span',{class:'section-count'},extra):null]);
  }
  function sourceLines(path, line = 1, full = false) {
    const lines=data.sources[path], from=full?0:Math.max(0,line-5), to=full?lines.length:Math.min(lines.length,line+18);
    const code=el('pre',{class:'code-block',tabindex:0,'aria-label':path+' のソース'});
    for(let i=from;i<to;i++)code.append(el('span',{class:'code-line'+(i+1===line?' focus':'')},[
      el('em',{'aria-hidden':'true'},i+1),syntax(lines[i]||' ')
    ]));
    return code;
  }
  function references(sources) {
    const section=el('section',{class:'detail-section reference-section','data-section':'source'},sectionTitle('実装コード','code',sources.length));
    sources.forEach(source=> {
      const filename=source.path.split('/').at(-1), directory=source.path.includes('/')?source.path.slice(0,-filename.length):'リポジトリルート';
      section.append(el('details',{class:'source-reference'},[
        el('summary',{},[
          icon('file','file-icon'),el('span',{class:'source-heading'},[
            el('span',{class:'source-filename'},filename),el('span',{class:'source-directory'},directory)
          ]),el('span',{class:'line-badge'},'L'+source.line),icon('chevron','disclosure-icon')
        ]),
        el('div',{class:'source-symbol'},[el('span',{},'参照箇所'),el('code',{},source.find)]),
        sourceLines(source.path,source.line),
        el('button',{class:'source-open',onclick:()=>navigate('files',{file:source.path})},['ファイル全体を読む',icon('arrow')])
      ]));
    });return section;
  }
  function flowDiagram(node) {
    if(!node.flow?.length)return null;
    return el('section',{class:'detail-section flow-section','data-section':'flow'},[
      sectionTitle('処理の流れ','flow'),
      el('ol',{class:'mini-flow',role:'list'},node.flow.map((step,index)=>el('li',{},[
        el('span',{class:'flow-number'},index+1),el('div',{class:'flow-step-content'},[
          el('strong',{},step.label),el('code',{},step.code)
        ])
      ])))
    ]);
  }
  function ioDiagram(node) {
    const part=(title,name,items)=>el('div',{class:'io-part'},[
      el('h4',{},[icon(name),title]),el('ul',{},items.map(item=>el('li',{},rich(item))))
    ]);
    return el('section',{class:'detail-section io-section'},[
      sectionTitle('入力と出力','layers'),el('div',{class:'io-grid'},[
        part('入力・起点','input',node.inputs),icon('arrow','io-arrow'),part('出力・結果','output',node.outputs)
      ])
    ]);
  }
  function conditions(node) {
    if(!node.conditions?.length)return null;
    return el('section',{class:'detail-section conditions-section'},[
      sectionTitle('条件と結果','flow'),el('div',{class:'condition-list'},node.conditions.map(item=>el('div',{class:'condition-row'},[
        el('span',{class:'condition-when'},rich(item.when)),icon('arrow'),el('span',{class:'condition-result '+item.tone},rich(item.result))
      ])))
    ]);
  }
  function detailHeader(title,kind,summary,status='実装済み') {
    return el('header',{class:'detail-header'},[
      el('div',{class:'detail-kind'},[el('span',{class:'detail-icon'},icon(iconNames[kind]||(kind==='接続'?'flow':'file'))),el('span',{},kinds[kind]||kind),el('span',{class:'implementation-status '+(status==='未実装'?'is-planned':'')},status)]),
      el('h2',{id:'detail-title',class:'detail-title'},title),el('p',{class:'detail-summary'},rich(summary))
    ]);
  }
  function description(text) {
    const sentences=text.match(/[^。]+。?|。/g)||[text], paragraphs=[];
    for(let i=0;i<sentences.length;i+=2)paragraphs.push(el('p',{},rich(sentences.slice(i,i+2).join(''))));
    return el('section',{class:'detail-section description-section'},[
      sectionTitle('この処理の役割','info'),el('div',{class:'detail-body'},paragraphs)
    ]);
  }
  function relatedLinks(title,items) {
    return el('section',{class:'detail-section'},[
      sectionTitle(title,'layers'),el('div',{class:'connection-links'},items.map(item=>el('button',{onclick:item.action},[item.title,icon('arrow')])))
    ]);
  }
  function nodeDetail(node) {
    const content=$('#detail-content');$('#inspector').dataset.kind=node.kind;
    content.replaceChildren(detailHeader(node.title,node.kind,node.summary,node.status==='planned'?'未実装':'実装済み'));
    [flowDiagram(node),conditions(node),ioDiagram(node),description(node.body)].filter(Boolean).forEach(section=>content.append(section));
    if(node.facts?.length)content.append(el('section',{class:'detail-section facts-section','data-section':'settings'},[
      sectionTitle('設定と動作','sliders'),el('dl',{class:'facts'},node.facts.map(([key,value])=>el('div',{},[
        el('dt',{},rich(key)),el('dd',{},rich(value))
      ])))
    ]));
    content.append(references(node.sources));
    const jumps = [['flow','flow','処理'],['settings','sliders','設定'],['source','code','コード']]
      .filter(([section]) => content.querySelector('[data-section="'+section+'"]'));
    content.querySelector('.detail-header').append(el('nav',{class:'detail-jumps','aria-label':'解説内の移動'},jumps.map(([section,name,label]) =>
      el('button',{onclick:() => {
        const target=content.querySelector('[data-section="'+section+'"]');
        $('#inspector').scrollTo({top:target.offsetTop-16,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
      }},[icon(name),label])
    )));
    const views=data.views.filter(view=>view.positions.some(position=>position.id===node.id));
    if(views.length)content.append(relatedLinks('この要素を図で見る',views.map(view=>({title:view.title,action:()=>navigate(view.id,{node:node.id})}))));
    content.append(el('p',{class:'detail-note'},[icon('info'),'ソースは生成時のスナップショットです。変更後はマップを再生成してください。']));
    $('#inspector').scrollTop=0;
  }
  function edgeDetail(edge) {
    const source=nodes.get(edge.source),target=nodes.get(edge.target);
    const kind={call:'呼び出し・HTTP',data:'データの読み書き',config:'設定・ビルド依存',control:'制御・検証'}[edge.kind];
    $('#inspector').dataset.kind=edge.kind==='call'?'server':edge.kind;
    $('#detail-content').replaceChildren(
      detailHeader(edge.label,'接続',kind),
      el('section',{class:'detail-section edge-journey'},[
        sectionTitle('接続の向き','flow'),
        ...[source,target].map((node,index)=>el('div',{class:'journey-stop'},[
          el('span',{class:'flow-number'},index+1),icon(iconNames[node.kind]),el('div',{},[
            el('small',{},index===0?'送信元・起点':'接続先・処理'),el('button',{onclick:()=>selectNode(node.id)},node.title)
          ])
        ]))
      ]),description(edge.description||target.body),
      references([...source.sources.slice(0,1),...target.sources.slice(0,1)]),
      el('p',{class:'detail-note'},[icon('info'),'実線は処理やデータの経路、破線は設定の依存です。'])
    );$('#inspector').scrollTop=0;
  }
  function fileDetail(path) {
    const related=data.nodes.filter(node=>node.sources.some(source=>source.path===path));
    $('#inspector').dataset.kind='file';
    $('#detail-content').replaceChildren(
      detailHeader(path.split('/').at(-1),'ファイル',path,'公開ソース'),
      el('p',{class:'file-snapshot'},[icon('info'),'生成時のソース全文。秘密設定やDBの内容は収録していません。']),
      relatedLinks('関連する処理',related.map(node=>({title:node.title,action:()=>selectNode(node.id)}))),
      el('div',{class:'file-raw'},sourceLines(path,1,true))
    );$('#inspector').scrollTop=0;
  }
  function edgeGeometry(from,to) {
    const w=dimensions.nodeWidth,h=dimensions.nodeHeight;
    let sx,sy,tx,ty,path,lx,ly;
    const dx=to.x-from.x,dy=to.y-from.y;
    if (Math.abs(dy)<20) {
      sx=from.x+(dx>0?w:0); sy=from.y+h/2; tx=to.x+(dx>0?0:w); ty=to.y+h/2;
      path=`M${sx} ${sy} L${tx} ${ty}`;lx=(sx+tx)/2;ly=sy-13;
    } else if (Math.abs(dx)<20) {
      sx=from.x+w/2; sy=from.y+(dy>0?h:0);tx=to.x+w/2;ty=to.y+(dy>0?0:h);
      path=`M${sx} ${sy} L${tx} ${ty}`;lx=sx+67;ly=(sy+ty)/2;
    } else {
      sx=from.x+w/2;sy=from.y+(dy>0?h:0);tx=to.x+w/2;ty=to.y+(dy>0?0:h);
      const mid=(sy+ty)/2;
      path=`M${sx} ${sy} L${sx} ${mid} L${tx} ${mid} L${tx} ${ty}`;
      lx=(sx+tx)/2;ly=mid-12;
    }
    return {path,lx,ly};
  }
  function drawGraph(view) {
    const edgeRoot=$("#edges"),groupRoot=$("#groups"),nodeRoot=$("#nodes");
    edgeRoot.replaceChildren();groupRoot.replaceChildren();nodeRoot.replaceChildren();
    const defs=svg("defs");
    [["arrow","#777780"],["arrow-data","#1f8a3d"],["arrow-config","#6e6e73"],["arrow-control","#63557e"]].forEach(([id,color]) =>
      defs.append(svg("marker",{id,viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:6,markerHeight:6,orient:"auto-start-reverse"},[svg("path",{d:"M0 0 L10 5 L0 10 Z",fill:color})])));
    edgeRoot.append(defs);
    (view.groups||[]).forEach((group) => groupRoot.append(el("div",{class:"group",style:`left:${group.x}px;top:${group.y}px;width:${group.w}px;height:${group.h}px`},el("strong",{},group.title))));
    const positions=new Map(view.positions.map((position)=>[position.id,position]));
    view.edges.forEach((edge,index) => {
      const geometry=edgeGeometry(positions.get(edge.source),positions.get(edge.target));
      const labelWidth=Math.max(60,edge.label.length*10.5+14);
      const label=svg("text",{x:geometry.lx,y:geometry.ly+4,"text-anchor":"middle"});label.textContent=edge.label;
      const group=svg("g",{class:"connection","data-edge":index,"data-kind":edge.kind,tabindex:0,role:"button","aria-label":nodes.get(edge.source).title+"から"+nodes.get(edge.target).title+"："+edge.label},[
        svg("path",{class:"hit",d:geometry.path}),svg("path",{class:"line",d:geometry.path}),
        svg("rect",{x:geometry.lx-labelWidth/2,y:geometry.ly-11,width:labelWidth,height:22,rx:4}),label
      ]);
      const open=()=>navigate(view.id,{edge:index});
      group.addEventListener("click",open);
      group.addEventListener("keydown",(event)=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();open();}});
      edgeRoot.append(group);
    });
    view.positions.forEach((position)=> {
      const node=nodes.get(position.id);
      nodeRoot.append(el("button",{class:"map-node","data-id":node.id,"data-kind":node.kind,style:`left:${position.x}px;top:${position.y}px`,onclick:()=>selectNode(node.id)},[
        el("span",{class:"node-heading"},[icon(iconNames[node.kind],'node-icon'),el("span",{},[el("span",{class:"node-kind"},kinds[node.kind]),el("strong",{},node.title)])]),
        el("span",{class:"summary"},node.summary)
      ]));
    });
  }
  function highlight() {
    if(!activeView)return;
    const edge = state.edge !== null ? activeView.edges[state.edge] : null;
    const related = new Set(edge ? [edge.source,edge.target] : [state.node]);
    if(!edge) activeView.edges.forEach((e)=>{if(e.source===state.node||e.target===state.node){related.add(e.source);related.add(e.target);}});
    document.querySelectorAll(".map-node").forEach((button)=> {
      const id=button.dataset.id;
      button.classList.toggle("selected",id===state.node&&!edge);
      button.classList.toggle("related",related.has(id)&&id!==state.node);
      button.classList.toggle("dimmed",!related.has(id));
      button.setAttribute("aria-pressed",String(id===state.node&&!edge));
    });
    document.querySelectorAll(".connection").forEach((group)=>{
      const e=activeView.edges[Number(group.dataset.edge)];
      const isRelated=edge?Number(group.dataset.edge)===state.edge:e.source===state.node||e.target===state.node;
      group.classList.toggle("selected",edge&&isRelated);group.classList.toggle("related",isRelated);group.classList.toggle("dimmed",!isRelated);
      group.setAttribute("aria-pressed",String(Boolean(edge&&isRelated)));
    });
  }
  function setZoom(value) {
    zoom=Math.max(.4,Math.min(1.8,value));
    $("#world").style.transform=`scale(${zoom})`;
    $("#sizer").style.width=dimensions.width*zoom+"px";
    $("#sizer").style.height=dimensions.height*zoom+"px";
    $("#zoom-value").textContent=Math.round(zoom*100)+"%";
  }
  function fit() {
    const viewport=$("#viewport");
    // 狭い画面では文字を極端に縮めず、横スクロールで読む。
    const minimum=matchMedia("(max-width: 800px)").matches ? .78 : .56;
    setZoom(Math.max(minimum,Math.min(1,(viewport.clientWidth-16)/dimensions.width,(viewport.clientHeight-16)/dimensions.height)));
    viewport.scrollTo(0,0);
  }
  function drawSteps(view) {
    $("#step-list").replaceChildren(...view.steps.map((step,index)=>el("button",{
      "aria-pressed":String(index===currentStep),onclick:()=>{currentStep=index;navigate(view.id,{node:step.node});updateStep();}
    },[el("span",{},String(index+1).padStart(2,"0")),step.title])));
    updateStep();
  }
  function updateStep() {
    if(!activeView)return;
    const step=activeView.steps[currentStep];
    $("#step-text").textContent=step.text;
    $("#step-counter").textContent=`${String(currentStep+1).padStart(2,"0")} / ${String(activeView.steps.length).padStart(2,"0")}`;
    $("#step-list").querySelectorAll("button").forEach((button,index)=>button.setAttribute("aria-pressed",String(index===currentStep)));
    $("#next-step").textContent=currentStep===activeView.steps.length-1?"最初の手順へ ↺":"次の手順 →";
  }
  function drawFileIndex() {
    const groups=[
      ["画面・Blockly",(p)=>p.startsWith("apps/web/")],
      ["サーバー",(p)=>p.startsWith("apps/server/app/")],
      ["ESP32",(p)=>p.startsWith("firmware/")],
      ["設定・依存関係・検証",(p)=>p.startsWith("config/")||p===".gitignore"||p.startsWith("apps/server/")],
      ["設計・運用",()=>true]
    ];
    const remaining=new Set(Object.keys(data.sources));
    const root=$("#file-index");
    root.replaceChildren(el("p",{class:"file-heading"},"公開ソースだけを収録。ファイルから担当する処理と設定の意味へ移動できます。"));
    groups.forEach(([title,test])=> {
      const files=[...remaining].filter(test);if(!files.length)return;
      const section=el("section",{class:"file-group"},el("h2",{},title));
      files.forEach((path)=> {
        remaining.delete(path);
        const related=data.nodes.filter((node)=>node.sources.some((source)=>source.path===path));
        section.append(el("div",{class:"file-row"},[
          el("div",{},[el("code",{},path),el("p",{},related.map((node)=>node.title).join(" / ")||"共有定義・参照資料")]),
          el("div",{class:"file-actions"},[
            ...(related.slice(0,2).map((node)=>el("button",{onclick:()=>selectNode(node.id)},node.title))),
            el("button",{onclick:()=>navigate("files",{file:path})},"ソースを読む")
          ])
        ]));
      });root.append(section);
    });
    root.append(el("section",{class:"file-group"},[
      el("h2",{},"ローカル設定・生成物（内容は収録しません）"),
      ...[["config/local.json","サーバーの個体・トークン設定","server-config"],["firmware/include/robot_config.h","機体のWi-Fi・接続先・トークン設定","firmware-config"],["data/workshop.sqlite3","個体・参加者・保存プログラム・予約・実行・イベントを保持","database"]].map(([path,text,node])=>el("div",{class:"file-row"},[
        el("div",{},[el("code",{},path),el("p",{},text)]),el("div",{class:"file-actions"},el("button",{onclick:()=>selectNode(node)},"設定・動作を見る"))
      ]))
    ]));
  }
  function parseLocation() {
    const [view,query=""]=location.hash.slice(1).split("?");
    const params=new URLSearchParams(query);
    const validView=view==="files"||data.views.some((v)=>v.id===view)?view:"overview";
    const current=data.views.find((v)=>v.id===validView);
    const edgeRaw=params.get("edge");
    const edge=edgeRaw!==null&&/^\d+$/.test(edgeRaw)&&current?.edges[Number(edgeRaw)]?Number(edgeRaw):null;
    let node=params.get("node");
    if(!nodes.has(node)||(current&&!current.positions.some((p)=>p.id===node)))node=current?.steps[0].node||"gitignore";
    const file=params.get("file");
    return {view:validView,node,edge,file:validView==="files"&&Object.hasOwn(data.sources,file)?file:null};
  }
  function render() {
    const previous=state, next=parseLocation();
    const viewChanged=initialRender||previous.view!==next.view;
    if(!initialRender)viewMemory.set(previous.view,{zoom,x:$("#viewport").scrollLeft,y:$("#viewport").scrollTop,node:previous.node,step:currentStep});
    state=next;activeView=data.views.find((v)=>v.id===state.view);
    const files=state.view==="files";
    $("#viewport").hidden=files;$("#file-index").hidden=!files;$("#legend").hidden=files;$("#map-tools").hidden=files;$("#walkthrough").hidden=files;
    $("#view-title").textContent=files?"ファイル索引":activeView.title;
    $("#view-description").textContent=files?"ファイル名から、その責務・設定項目・実際の動作を引く。":activeView.description;
    $("#view-number").textContent="SYSTEM / "+String(files?data.views.length+1:data.views.indexOf(activeView)+1).padStart(2,"0");
    document.title=(files?"ファイル索引":activeView.title)+" · Blockly Robotics";
    $("#views").querySelectorAll("a").forEach((a)=>{
      if(a.dataset.view===state.view)a.setAttribute("aria-current","page");else a.removeAttribute("aria-current");
    });
    if(!files) {
      if(viewChanged) {
        currentStep=viewMemory.get(state.view)?.step||0;
        drawGraph(activeView);drawSteps(activeView);
        const saved=viewMemory.get(state.view);
        if(saved){setZoom(saved.zoom);$("#viewport").scrollTo(saved.x,saved.y);}else fit();
      }
      const matching=activeView.steps.findIndex((step)=>step.node===state.node);
      if(matching>=0){currentStep=matching;updateStep();}
      highlight();
    }
    if(state.file)fileDetail(state.file);
    else if(state.edge!==null)edgeDetail(activeView.edges[state.edge]);
    else nodeDetail(nodes.get(state.node));
    announce((files?"ファイル索引":activeView.title)+"、"+(state.file||nodes.get(state.node).title)+"を表示");
    initialRender=false;
  }
  [...data.views,{id:"files",title:"ファイル索引"}].forEach((view)=> {
    const link=el("a",{href:route(view.id),"data-view":view.id},view.title);
    link.addEventListener("click",(event)=> {
      event.preventDefault();const saved=viewMemory.get(view.id);navigate(view.id,saved?{node:saved.node}:{});
    });$("#views").append(link);
  });
  $(".phase").textContent=data.phase;
  drawFileIndex();
  $("#source-version").textContent="SOURCE "+data.sourceDigest;
  $("#zoom-in").addEventListener("click",()=>setZoom(zoom+.1));
  $("#zoom-out").addEventListener("click",()=>setZoom(zoom-.1));
  $("#fit").addEventListener("click",fit);
  $("#next-step").addEventListener("click",()=> {
    currentStep=(currentStep+1)%activeView.steps.length;
    navigate(activeView.id,{node:activeView.steps[currentStep].node});updateStep();
  });
  $("#search").addEventListener("input",(event)=> {
    const query=event.target.value.trim().toLocaleLowerCase();const result=$("#search-results");result.replaceChildren();result.hidden=!query;
    if(!query)return;
    const matches=data.nodes.filter((node)=>JSON.stringify(node).toLocaleLowerCase().includes(query)).slice(0,8);
    const files=Object.keys(data.sources).filter((path)=>path.toLocaleLowerCase().includes(query)).slice(0,4);
    matches.forEach((node)=>result.append(el("button",{onclick:()=>{result.hidden=true;selectNode(node.id);}},[node.title,el("small",{},node.summary)])));
    files.forEach((path)=>result.append(el("button",{onclick:()=>{result.hidden=true;navigate("files",{file:path});}},[path.split("/").at(-1),el("small",{},path)])));
    if(!matches.length&&!files.length)result.append(el("p",{},"一致する機能・ファイルはありません。"));
  });
  $("#search").addEventListener("keydown",(event)=> {
    if(event.key==="Escape"){$("#search-results").hidden=true;event.target.blur();}
    if(event.key==="ArrowDown"){$("#search-results button")?.focus();event.preventDefault();}
  });
  document.addEventListener("click",(event)=>{if(!event.target.closest(".search-box"))$("#search-results").hidden=true;});
  document.addEventListener("keydown",(event)=> {
    if(event.key==="/"&&!event.target.closest("input,textarea,pre")){event.preventDefault();$("#search").focus();}
    if(event.key==="Escape")$("#search-results").hidden=true;
  });
  const viewport=$("#viewport");let drag=null;
  viewport.addEventListener("pointerdown",(event)=> {
    if(event.button!==0||event.pointerType==="touch"||event.target.closest("button,.connection"))return;
    drag={x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(event.pointerId);viewport.classList.add("dragging");
  });
  viewport.addEventListener("pointermove",(event)=>{if(drag){viewport.scrollLeft=drag.left-(event.clientX-drag.x);viewport.scrollTop=drag.top-(event.clientY-drag.y);}});
  const endDrag=()=>{drag=null;viewport.classList.remove("dragging");};
  viewport.addEventListener("pointerup",endDrag);viewport.addEventListener("pointercancel",endDrag);
  viewport.addEventListener("wheel",(event)=>{if(event.ctrlKey||event.metaKey){event.preventDefault();setZoom(zoom+(event.deltaY < 0 ? .06 : -.06));}},{passive:false});
  window.addEventListener("hashchange",render);
  render();
})();
