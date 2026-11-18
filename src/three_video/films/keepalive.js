import * as T from 'three';

const ink='#262838';
const tau=Math.PI*2;
const ease=(t,a,b)=>T.MathUtils.smoothstep(t,a,b);

function surface(w,h){
    const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
    return [canvas,canvas.getContext('2d')];
}
function paintPath(c,points,color,stroke=ink,weight=5){
    c.beginPath();for(const [fn,...args] of points)c[fn](...args);
    if(color){c.fillStyle=color;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=weight;c.stroke();}
}
function oval(c,x,y,rx,ry,color,stroke=ink,width=5){
    c.beginPath();c.ellipse(x,y,rx,ry,0,0,tau);c.fillStyle=color;c.fill();
    if(stroke){c.strokeStyle=stroke;c.lineWidth=width;c.stroke();}
}
function wire(c,coords,color=ink,width=6){
    paintPath(c,coords.map(([x,y],i)=>[i?'lineTo':'moveTo',x,y]),null,color,width);
}
function sandwich(c,x,y,scale,bite){
    c.save();c.translate(x,y);c.scale(scale,scale);c.rotate(-.12);
    const triangle=(dy,col)=>paintPath(c,[['moveTo',-41,12+dy],['quadraticCurveTo',-45,8+dy,-36,0+dy],['lineTo',9,-27+dy],['quadraticCurveTo',14,-30+dy,20,-22+dy],['lineTo',40,6+dy],['quadraticCurveTo',46,13+dy,34,15+dy],['closePath']],col,ink,3);
    triangle(12,'#ce8a49');triangle(7,'#70aa61');triangle(3,'#e66a52');triangle(0,'#f9cf88');
    for(const [a,b] of [[-15,1],[2,-9],[12,5]])wire(c,[[a,b],[a+3,b-1]],'#b88258',2);
    if(bite){oval(c,31,-4,7,5,'#e3b67b',null);}
    c.restore();
}

export function actor(kind){
    const [cv,c]=surface(352,504);const tex=new T.CanvasTexture(cv);tex.colorSpace=T.SRGBColorSpace;
    const plane=new T.Mesh(new T.PlaneGeometry(1.85,2.65),new T.MeshBasicMaterial({map:tex,transparent:true,alphaTest:.015,depthWrite:true,side:T.DoubleSide}));
    const boss=kind==='boss',lead=kind==='max',skin=boss?'#f0bf9e':lead?'#f6ceaa':'#b97f63';
    const shirt=boss?'#585c91':lead?'#36a69b':'#db8d48';
    return {plane, draw(t,action,emotion,run=0){
        const swing=Math.sin(run*10),bounce=run?Math.abs(swing)*6:Math.sin(t*2.15)*1.7;
        c.clearRect(0,0,352,504);c.lineCap='round';c.lineJoin='round';
        c.save();c.translate(0,-bounce);
        const leg=(sign)=>{
            const dx=run?swing*sign*26:0;wire(c,[[166+sign*19,325],[167+sign*20+dx*.3,388],[165+sign*24+dx,440]],ink,12);
            oval(c,168+sign*24+dx,444,20,8,'#37394b',ink,4);
        };leg(-1);leg(1);
        paintPath(c,[['moveTo',148,227],['quadraticCurveTo',129,231,128,253],['lineTo',124,322],['quadraticCurveTo',161,342,210,323],['lineTo',205,250],['quadraticCurveTo',203,233,183,227],['closePath']],shirt);
        paintPath(c,[['moveTo',145,231],['lineTo',162,250],['lineTo',171,237],['lineTo',180,249],['lineTo',194,230]],'#fff0d8',ink,3);
        if(boss){paintPath(c,[['moveTo',170,240],['lineTo',178,248],['lineTo',174,257],['lineTo',184,295],['lineTo',172,312],['lineTo',160,296],['lineTo',168,256],['lineTo',163,248],['closePath']],'#ef806a',ink,3);}
        else{c.save();c.translate(182,272);c.rotate(-.1);c.fillStyle='#f2daa6';c.strokeStyle=ink;c.lineWidth=2;c.fillRect(-13,-1,27,19);c.strokeRect(-13,-1,27,19);c.fillStyle=ink;c.textAlign='center';c.font='bold 9px Arial';c.fillText(({max:'MAX',june:'JUNE',nell:'NELL',bea:'BEA'})[kind]||'JO',0,11);c.restore();}
        let left=[123,304],right=[217,303],le=[115,274],re=[221,272];
        if(action==='wave'){right=[242+13*Math.sin(t*10),177+5*Math.cos(t*10)];re=[236,246];}
        if(action==='point'){right=[285,216];re=[240,234];}
        if(action==='eat'||action==='offer'||action==='hold'){
            const reach=action==='offer'?48:0,up=action==='eat'?Math.max(0,Math.sin(t*2.6))*19:0;
            left=[148+reach,274-up];right=[192+reach,267-up];le=[110,277];re=[225+reach*.3,277];
        }
        if(action==='fold'){left=[186,265];right=[154,269];le=[120,288];re=[219,284];}
        if(run){right=[218+20*swing,261-14*swing];left=[120-21*swing,260+14*swing];}
        wire(c,[[134,246],le,left],ink,8);wire(c,[[197,245],re,right],ink,8);
        c.save();c.translate(168,179);c.rotate(emotion==='skeptic'?-.09:emotion==='panic'?.09:.028*Math.sin(t*1.6));
        // Hair silhouette and swept locks are distinct even in the exterior shot.
        oval(c,0,-19,67,72,skin,ink,6);
        oval(c,-64,-10,10,15,skin,ink,4);
        paintPath(c,boss?[
            ['moveTo',-63,-37],['quadraticCurveTo',-65,-93,2,-94],['quadraticCurveTo',57,-98,65,-45],['lineTo',46,-56],['quadraticCurveTo',15,-43,1,-71],['quadraticCurveTo',-21,-47,-58,-48],['closePath']
        ]:[['moveTo',-65,-33],['quadraticCurveTo',-70,-62,-50,-80],['lineTo',-64,-87],['quadraticCurveTo',-39,-92,-26,-84],['quadraticCurveTo',-18,-111,9,-99],['quadraticCurveTo',38,-111,53,-80],['quadraticCurveTo',72,-65,64,-37],['lineTo',46,-49],['lineTo',39,-65],['quadraticCurveTo',5,-43,-19,-56],['quadraticCurveTo',-34,-42,-54,-42],['closePath']],boss?'#655768':'#38313a',ink,5);
        wire(c,boss?[[-52,-64],[-33,-76],[-15,-80]]:[[-43,-71],[-24,-77],[-9,-88]],boss?'#b8acb7':'#8a6352',3.8);
        const blink=Math.cos(t*2.14+(boss?1:0))>.991;
        for(const s of [-1,1]){
            let x=s*23+4,y=-19;
            const rise=emotion==='skeptic'&&s===1?-8:0;
            wire(c,[[x-9,y-12+rise],[x+8,y-14+(emotion==='panic'?s*5:0)+rise]],ink,4.8);
            if(blink)wire(c,[[x-6,y+1],[x+6,y]],ink,4);
            else if(emotion==='panic'){oval(c,x,y+2,9,12,'#fff5df',ink,3);oval(c,x+1,y+3,3.7,5,ink,null);}
            else oval(c,x+(lead?3:-2),y+2,4.8,emotion==='smug'?3:8,ink,null);
        }
        if(boss){
            for(const x of [-19,28]){c.strokeStyle=ink;c.lineWidth=3;c.strokeRect(x-15,-28,31,21);}
            wire(c,[[-4,-21],[12,-21]],ink,3);
        }
        if(emotion==='panic')oval(c,6,17,10,13,'#793f48',ink,3);
        else paintPath(c,[['moveTo',-9,17],['quadraticCurveTo',4,emotion==='smug'?32:22,20,emotion==='skeptic'?16:13]],null,ink,4.5);
        c.restore();
        if(['eat','offer','hold'].includes(action))sandwich(c,(left[0]+right[0])/2,(left[1]+right[1])/2-4,.75,t>33);
        oval(c,...left,7,7,skin,ink,3);oval(c,...right,7,7,skin,ink,3);
        if(action==='point')wire(c,[[right[0],right[1]],[right[0]+12,right[1]-4]],ink,4);
        c.restore();tex.needsUpdate=true;
    }};
}

export function createFilm(canvas){
    const gl=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});gl.setSize(canvas.width,canvas.height,false);
    const scene=new T.Scene();scene.background=new T.Color('#adcabe');
    const cam=new T.PerspectiveCamera(32,canvas.width/canvas.height,.03,100);
    const hemi=new T.HemisphereLight(0xfff2dc,0xa5bed6,1.92);scene.add(hemi);
    const sun=new T.DirectionalLight(0xffe4ba,2);sun.position.set(-4,7,4);scene.add(sun);
    const group=p=>{const o=new T.Group();p.add(o);return o;};
    const box=new T.BoxGeometry(1,1,1),edge=new T.EdgesGeometry(box),finish=new Map();
    const pen=new T.LineBasicMaterial({color:'#313b47'});
    function tile(p,color,x,y,z,a,b,d,outline=true){
        if(!finish.has(color))finish.set(color,new T.MeshStandardMaterial({color,roughness:.85}));
        const obj=new T.Mesh(box,finish.get(color));obj.position.set(x,y,z);obj.scale.set(a,b,d);p.add(obj);
        if(outline)obj.add(new T.LineSegments(edge,pen));return obj;
    }
    function label(p,word,x,y,z,width,height,bg,fg,fontsize=76){
        const [cv,c]=surface(768,192);c.fillStyle=bg;c.fillRect(0,0,768,192);c.fillStyle=fg;c.textAlign='center';c.textBaseline='middle';c.font='bold '+fontsize+'px Arial';c.fillText(word,384,100,710);
        const tex=new T.CanvasTexture(cv);tex.colorSpace=T.SRGBColorSpace;
        const o=new T.Mesh(new T.PlaneGeometry(width,height),new T.MeshBasicMaterial({map:tex}));o.position.set(x,y,z);p.add(o);return o;
    }
    const office=group(scene);tile(office,'#286e81',0,-.12,0,16,.22,14);
    tile(office,'#9fcebd',0,2.3,-3.75,14,4.65,.13);
    for(let i=-6;i<=6;i++)tile(office,'#81b7a7',i,1.94,-3.66,.017,3.85,.012,false);
    tile(office,'#587c83',0,.22,-3.62,14,.26,.13);
    // The windows look through to a separate skyline.
    const windows=group(office);
    for(const x of [-3.25,-1.95,-.65]){
        tile(windows,'#e0ddd1',x,2.45,-3.56,1.36,2.25,.12);
        tile(windows,'#83b8d0',x,2.45,-3.46,1.24,2.08,.05);
        for(let i=0;i<5;i++){
            const ht=.48+.42*(1+Math.sin(i*3+x));
            tile(windows,'#6a97af',x-.5+.245*i,1.42+ht/2,-3.4,.22,ht,.025,false);
        }
        tile(windows,'#527482',x,2.45,-3.35,1.29,.04,.04);
    }
    function desk(x,z,monitor){
        tile(office,'#df9c70',x,.98,z,1.85,.13,.88);
        for(const s of [-1,1])for(const v of [-1,1])tile(office,'#415262',x+s*.78,.46,z+v*.32,.07,.92,.08);
        if(monitor){
            tile(office,'#3c4753',x,1.29,z,.1,.48,.095);
            tile(office,'#434d5a',x,1.62,z,.90,.57,.10);
            tile(office,'#98c5c5',x,1.62,z+.061,.81,.47,.016);
        }
    }
    desk(-.55,.13,false);desk(-3.5,-1.14,true);desk(2.7,-1.75,true);
    // Max's coffee and the pale rectangle left by the monitor make the absence visible.
    tile(office,'#eab68c',-.60,1.051,.07,.49,.007,.27,false);
    const mug=new T.Mesh(new T.CylinderGeometry(.085,.067,.15,20),new T.MeshStandardMaterial({color:'#f2c164'}));
    mug.position.set(-1.23,1.12,.19);office.add(mug);
    const handle=new T.Mesh(new T.TorusGeometry(.049,.014,8,20),mug.material);
    handle.position.set(-1.32,1.13,.19);office.add(handle);
    label(office,'STAFF ONLY',2.42,2.18,-3.39,1.01,.24,'#f0cb80','#364952');
    // Coiled lead where Max's monitor used to sit.
    const lead=new T.CatmullRomCurve3([[-.8,1.06,.08],[-.6,1.061,-.02],[-.55,1.061,.13],[-.72,1.062,.16],[-.81,1.062,.24]].map(p=>new T.Vector3(...p)));
    office.add(new T.Mesh(new T.TubeGeometry(lead,32,.012,7,false),new T.MeshBasicMaterial({color:ink})));
    tile(office,'#373c49',-.84,1.075,.25,.09,.035,.06);
    const sensor=group(office);sensor.position.set(1.14,2.48,-3.48);
    tile(sensor,'#e2d8b8',0,0,0,.51,.46,.18);
    const lens=new T.Mesh(new T.SphereGeometry(.14,20,12),new T.MeshStandardMaterial({color:'#b0c7c3',roughness:.3}));lens.scale.z=.45;lens.position.set(0,.03,.10);sensor.add(lens);
    const led=new T.Mesh(new T.SphereGeometry(.033,12,8),new T.MeshBasicMaterial({color:'#52e098'}));led.position.set(.16,-.14,.11);sensor.add(led);
    label(office,'OCCUPANCY',1.14,2.10,-3.39,.72,.14,'#304d58','#f2eac8');
    const printer=group(office);printer.position.set(1.48,0,-.85);
    tile(printer,'#435968',0,.39,0,.94,.77,.73);tile(printer,'#e3dbc7',0,.96,0,1.1,.45,.81);
    tile(printer,'#647b80',0,1.22,0,1,.09,.76);tile(printer,'#374756',0,1,.414,.76,.10,.015);
    const printout=group(printer);
    tile(printout,'#fff4da',0,0,0,.72,.42,.01);
    const promotion=label(printout,'HEAD OF MOTION',0,.018,.012,.69,.17,'#fff4da','#263944',80);
    printout.position.set(0,1,.44);
    const clock=group(office);clock.position.set(2.45,3.09,-3.49);
    const clockFace=new T.Mesh(new T.CircleGeometry(.35,48),new T.MeshBasicMaterial({color:'#ede4c9'}));clock.add(clockFace);
    for(let i=0;i<12;i++){let a=i*tau/12;tile(clock,'#354554',Math.sin(a)*.30,Math.cos(a)*.30,.009,.016,.05,.01,false).rotation.z=-a;}
    const minute=group(clock);tile(minute,'#334854',0,.12,.02,.022,.24,.012,false);
    tile(clock,'#a64939',-.085,.03,.036,.17,.03,.012,false).rotation.z=-.3;
    const chars=[actor('max'),actor('boss'),actor('worker')];chars.forEach(a=>office.add(a.plane));
    const facade=group(scene);facade.position.x=24;
    tile(facade,'#486578',0,2,-1,4,5.4,.55);
    const lit=new T.MeshBasicMaterial({color:'#ffdb88'}),off=new T.MeshBasicMaterial({color:'#354860'});
    const panes=[];
    for(let row=0;row<4;row++)for(let col=0;col<4;col++){
        const x=-1.43+col*.96,y=.25+row*1.16;
        const panel=tile(facade,'#bdced0',x,y,-.69,.83,.78,.075);panel.material=lit;panes.push(panel);
        tile(facade,'#35475a',x,y,-.63,.038,.79,.03);
        tile(facade,'#34465a',x,y-.42,-.64,.96,.09,.10);
        for(let j=0;j<2;j++){
            const head=new T.Mesh(new T.CircleGeometry(.073,16),new T.MeshBasicMaterial({color:'#334754'}));
            head.position.set(x+(j-.5)*.32,y-.07,-.62);facade.add(head);
            tile(facade,'#354356',head.position.x,y-.24,-.61,.12,.24,.03,false);
        }
    }
    label(facade,'NORTH BLOCK',0,4.52,-.63,2.08,.25,'#435968','#ede9d6');
    const shots=[
        [0,[.16,1.72,3.2],[-.55,1.41,-.13]],
        [3.5,[.4,1.91,5.95],[.06,1.58,-.68]],
        [6.1,[1.34,2.57,-.65],[1.14,2.47,-3.44]],
        [7.8,[-.14,1.73,2.26],[-.59,1.72,-.64]],
        [10.2,[1.74,1.35,.98],[1.47,1.07,-.8]],
        [12.6,[.72,1.95,1.92],[.62,1.74,-.88]],
        [16.7,[1.2,2.64,-1.28],[1.14,2.46,-3.44]],
        [20.9,[-.4,2.23,5.1],[-.27,1.62,-1.11]],
        [24.9,[-.23,1.95,2.1],[-.59,1.72,-.64]],
        [30.4,[-.30,1.55,1.50],[-.57,1.45,-.65]],
        [33,[24.2,2.65,7.95],[24,2.1,-.7]],
        [37.1,[-.65,2,1.60],[-.60,1.73,-.66]],
        [40.4,[.4,2.08,6.15],[.03,1.48,-.65]],
        [43.6,[.34,1.91,5.16],[.04,1.56,-.65]],
        [47,[1.29,2.49,-1.97],[1.14,2.48,-3.40]],
        [48.1,[24.16,2.49,7.35],[24,2.1,-.7]],
        [50.1,[1.45,1.42,2.55],[1.48,1.08,-.38]],
        [54.1,[1.49,1.30,2.26],[1.48,1.08,-.39]],
        [55.9,[-.52,1.95,1.85],[-.61,1.74,-.65]]
    ];
    const context=canvas.getContext('2d');
    return t=>{
        const k=shots.findLastIndex(s=>s[0]<=t),[start,eye,aim]=shots[k];
        const u=(t-start)/((shots[k+1]?.[0]??60)-start),outside=k===10||k===15;
        const dark=t>33.38&&t<47.63;
        scene.background.set(outside?'#b9c6d8':'#adcabe');
        office.visible=!outside;facade.visible=outside;
        const urgency=ease(t,40.4,41.65);
        chars[0].plane.position.set(-.61,1.03,-.64);
        chars[1].plane.position.set(t>38&&t<42?2.6-1.87*urgency:.72,1.03,-.85);
        chars[1].plane.visible=(t>=3&&t<=16.7)||(t>=40.4&&t<55.9);
        chars[2].plane.position.set(-2.9,1.02,-1.72);
        const eating=t>29.25&&t<45.9;
        const wave=(t>7.45&&t<10.9)||(t>24&&t<29)||(t>46.1&&t<48);
        chars[0].draw(t,wave?'wave':eating?(t>42.8?'offer':'eat'):t>54?'fold':'idle',t>54?'smug':t<7||t>30?'skeptic':'normal');
        chars[1].draw(t,t<7?'point':t>45.8?'hold':'idle',t>40.4&&t<47?'panic':'smug',t>40.4&&t<41.68?t-40.39:0);
        chars[2].draw(t,'idle',dark?'panic':'normal');
        led.material.color.set(dark?'#a3484b':'#5affad');
        minute.rotation.z=-t*.16;
        const pageRise=t<15?ease(t,10.3,11.2)*(1-ease(t,12.4,13.5)):ease(t,50.1,51.7);
        printout.visible=pageRise>.01;printout.scale.y=pageRise;
        promotion.visible=t>50;
        printout.position.y=1+pageRise*.15;
        panes.forEach((p,i)=>p.material=(dark&&t>33.38+(i%4)*.10)?off:lit);
        cam.position.fromArray(eye);cam.position.z-=u*.085;cam.lookAt(...aim);
        chars.forEach(a=>a.plane.rotation.y=Math.atan2(cam.position.x-a.plane.position.x,cam.position.z-a.plane.position.z)*.38);
        gl.render(scene,cam);context.drawImage(gl.domElement,0,0);
        if(dark&&!outside){context.fillStyle='rgba(24,37,92,.52)';context.fillRect(0,0,canvas.width,canvas.height);}
    };
}
