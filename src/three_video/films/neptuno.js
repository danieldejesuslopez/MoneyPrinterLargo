import * as T from 'three';

const TAU = Math.PI * 2;
const smooth = (t,a,b) => T.MathUtils.smoothstep(t,a,b);
function random(seed) {
    return () => ((seed=(Math.imul(seed,1664525)+1013904223)>>>0) / 4294967296);
}

function atmosphereMap(uranus) {
    const c=document.createElement('canvas'); c.width=1024; c.height=512;
    const ctx=c.getContext('2d'), data=ctx.createImageData(c.width,c.height);
    const rng=random(8196), grid=Float32Array.from({length:256*256},()=>rng());
    function noise(x,y) {
        const ix=Math.floor(x),iy=Math.floor(y),fx=x-ix,fy=y-iy;
        const at=(a,b)=>grid[(a&255)+256*(b&255)];
        const u=fx*fx*(3-2*fx),v=fy*fy*(3-2*fy);
        return T.MathUtils.lerp(T.MathUtils.lerp(at(ix,iy),at(ix+1,iy),u),T.MathUtils.lerp(at(ix,iy+1),at(ix+1,iy+1),u),v);
    }
    for(let y=0;y<c.height;y++)for(let x=0;x<c.width;x++){
        const u=x/c.width,v=y/c.height, latitude=(v-.5)*Math.PI;
        const wave=4*noise(u*13,v*18)+.8*Math.sin(u*TAU*3+v*29);
        const streak=noise(u*22,v*190+wave*2)*.65+noise(u*57,v*460+wave*4)*.35;
        const belts=Math.sin(v*64+wave*.48)*.40+Math.sin(v*135+wave*.8)*.12;
        const cirrus=Math.max(0,streak-.57)*Math.max(0,Math.sin(v*68+wave))*130;
        const d=uranus?8*(streak+belts):29*(streak+belts)+cirrus;
        const base=uranus?[107,174,180]:[55,115,154];
        const i=4*(x+y*c.width);
        base.forEach((b,j)=>data.data[i+j]=b+d+(j===0?6:10)*Math.cos(latitude));
        data.data[i+3]=255;
    }
    ctx.putImageData(data,0,0);const map=new T.CanvasTexture(c);map.colorSpace=T.SRGBColorSpace;
    map.wrapS=T.RepeatWrapping;return map;
}

export function createFilm(canvas){
    const w=canvas.width,h=canvas.height,ctx=canvas.getContext('2d');
    const gl=new T.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});gl.setSize(w,h,false);
    const scene=new T.Scene(),cam=new T.PerspectiveCamera(38,w/h,.04,100);
    const sphere=new T.SphereGeometry(1,96,56),cube=new T.BoxGeometry(1,1,1);
    const rng=random(1846), mats=new Map();
    const group=p=>{const g=new T.Group();p.add(g);return g;};
    function mat(c){if(!mats.has(c))mats.set(c,new T.MeshStandardMaterial({color:c,roughness:.52}));return mats.get(c);}
    function mesh(p,geo,m,x,y,z,a=1,b=a,c=a){
        const o=new T.Mesh(geo,typeof m==='string'?mat(m):m);
        o.position.set(x,y,z);o.scale.set(a,b,c);p.add(o);return o;
    }
    function bar(p,col,first,last,r,r2=r){
        const a=new T.Vector3(...first),b=new T.Vector3(...last),delta=b.clone().sub(a);
        const o=mesh(p,new T.CylinderGeometry(r2,r,delta.length(),32),col,0,0,0);
        o.position.copy(a).lerp(b,.5);o.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),delta.normalize());return o;
    }
    function path(p,pts,col,opacity=1){
        const line=new T.Line(new T.BufferGeometry().setFromPoints(pts),new T.LineBasicMaterial({color:col,transparent:true,opacity}));p.add(line);return line;
    }
    const bright=c=>new T.MeshBasicMaterial({color:c});
    scene.add(new T.AmbientLight(0xb3cadc,.32));
    const key=new T.DirectionalLight(0xffefce,2.65);key.position.set(-4,4,3);scene.add(key);
    const fill=new T.DirectionalLight(0x95d6ff,.84);fill.position.set(4,1,-4);scene.add(fill);
    const globes=group(scene),orbit=group(scene),scope=group(scene),sheet=group(scene);
    const planets=[true,false].map(u=>{
        const p=group(globes);
        mesh(p,sphere,new T.MeshStandardMaterial({map:atmosphereMap(u),roughness:1}),0,0,0);
        const halo=new T.ShaderMaterial({transparent:true,depthWrite:false,side:T.FrontSide,blending:T.AdditiveBlending,
            vertexShader:'varying vec3 N;varying vec3 V;void main(){vec4 pos=modelViewMatrix*vec4(position,1.);N=normalize(normalMatrix*normal);V=normalize(-pos.xyz);gl_Position=projectionMatrix*pos;}',
            fragmentShader:'varying vec3 N;varying vec3 V;void main(){float edge=pow(1.-max(dot(normalize(N),normalize(V)),0.),4.8);float sun=.28+.72*max(0.,dot(normalize(N),normalize(vec3(-.7,.4,.3))));gl_FragColor=vec4(.45,.88,1.,edge*sun*.77);}'});
        mesh(p,sphere,halo,0,0,0,1.007);
        return p;
    });
    function orbitLine(radius,color){
        return path(orbit,Array.from({length:257},(_,i)=>new T.Vector3(radius*Math.cos(i*TAU/256),0,radius*Math.sin(i*TAU/256))),color,.79);
    }
    for(const [r,c] of [[.57,'#695e50'],[1.03,'#7b7361'],[1.59,'#7caca8'],[2.36,'#6693ad']])orbitLine(r,c);
    mesh(orbit,sphere,bright('#ffe6aa'),0,0,0,.147);
    const beads=[.57,1.03,1.59,2.36].map((r,i)=>mesh(orbit,sphere,bright(['#e3b086','#c6b8ac','#a5e0d4','#8cbfe7'][i]),r,0,0,[.054,.067,.105,.095][i]));
    const pull=path(orbit,[new T.Vector3(),new T.Vector3(1,0,0)],'#ebac68');
    pull.frustumCulled=false;
    // A nineteenth-century refractor with rack, counterweight, eyepiece and graduated circles.
    const brass='#cc9554', wood='#714d33', metal='#445653';
    for(let i=0;i<3;i++){
        const a=i*TAU/3+.13;
        bar(scope,wood,[Math.cos(a)*.15,-.06,Math.sin(a)*.15],[Math.cos(a)*.88,-1.58,Math.sin(a)*.88],.075,.044);
    }
    bar(scope,brass,[0,-.37,0],[0,.31,0],.17);
    const barrel=group(scope);barrel.position.y=.42;barrel.rotation.z=-.79;
    bar(barrel,metal,[0,-.78,0],[0,1.28,0],.144,.205);
    for(const [y,r] of [[-.80,.157],[-.32,.177],[.22,.20],[.85,.215],[1.22,.231],[1.32,.235]]){
        bar(barrel,brass,[0,y-.038,0],[0,y+.038,0],r);
    }
    bar(barrel,'#211d20',[0,-1.11,0],[0,-.76,0],.068,.106);
    bar(barrel,'#75a6a8',[0,1.32,0],[0,1.338,0],.212);
    bar(barrel,brass,[.26,.1,0],[.26,.69,0],.047);
    for(const y of [.15,.55]) bar(barrel,brass,[.13,y,0],[.25,y,0],.018);
    mesh(scope,new T.TorusGeometry(.25,.023,12,64),brass,0,.3,.22);
    for(let i=0;i<32;i++){
        let a=i*TAU/32;const o=mesh(scope,cube,brass,.227*Math.cos(a),.3+.227*Math.sin(a),.22,.035,.008,.016);o.rotation.z=a;
    }
    const chart=document.createElement('canvas');chart.width=768;chart.height=960;
    const paper=chart.getContext('2d');paper.fillStyle='#dfd2b4';paper.fillRect(0,0,768,960);
    for(let i=0;i<28000;i++){paper.fillStyle=rng()>.5?'#73583718':'#ffefba24';paper.fillRect(rng()*768,rng()*960,2,1);}
    paper.fillStyle='#353e40';paper.font='italic 48px Georgia';paper.fillText('Carte du ciel',74,103);
    paper.font='22px Georgia';paper.fillText('Berlin, septembre 1846',76,146);
    paper.strokeStyle='#818678';paper.lineWidth=1;
    for(let i=0;i<12;i++){paper.beginPath();paper.moveTo(65,202+i*57);paper.lineTo(710,192+i*57);paper.stroke();}
    for(let i=0;i<10;i++){paper.beginPath();paper.moveTo(70+i*69,194);paper.quadraticCurveTo(94+i*63,542,70+i*69,838);paper.stroke();}
    for(let i=0;i<345;i++){const x=74+rng()*623,y=200+rng()*622,r=.7+rng()**3*3.4;paper.fillStyle='#434e53';paper.beginPath();paper.arc(x,y,r,0,TAU);paper.fill();}
    paper.strokeStyle='#59554c';paper.strokeRect(46,44,676,866);
    const map=new T.CanvasTexture(chart);map.colorSpace=T.SRGBColorSpace;
    mesh(sheet,new T.PlaneGeometry(2.15,2.69),new T.MeshBasicMaterial({map,side:T.DoubleSide}),0,0,0);
    const sky=document.createElement('canvas');sky.width=w;sky.height=h;const bg=sky.getContext('2d');
    const grad=bg.createLinearGradient(0,0,w*.8,h);grad.addColorStop(0,'#101c2b');grad.addColorStop(.56,'#132634');grad.addColorStop(1,'#081321');bg.fillStyle=grad;bg.fillRect(0,0,w,h);
    for(let i=0;i<1450;i++){
        const x=rng()*w,y=rng()*h,r=(.22+rng()**5*1.4)*w/864;
        bg.fillStyle='rgba(202,216,220,'+(.2+rng()*.65)+')';bg.beginPath();bg.arc(x,y,r,0,TAU);bg.fill();
    }
    const field=Array.from({length:75},()=>[rng(),rng(),.5+rng()**2*2.6]);
    function text(str,x,y,size,color='#f6ecce',font='Georgia',align='left'){
        ctx.textAlign=align;ctx.fillStyle=color;ctx.font=size*w+'px '+font;ctx.fillText(str,x*w,y*h);
    }
    function stroke(x1,y1,x2,y2,col,weight=1){
        ctx.strokeStyle=col;ctx.lineWidth=weight*w/864;ctx.beginPath();ctx.moveTo(x1*w,y1*h);ctx.lineTo(x2*w,y2*h);ctx.stroke();
    }
    function dot(x,y,r,c){ctx.fillStyle=c;ctx.beginPath();ctx.arc(x*w,y*h,r*w,0,TAU);ctx.fill();}
    const shots=[
        [0,'neptune','El planeta','que faltaba'],[8,'uranus','Una pista en Urano',''],
        [14.8,'residual','La posición esperada',''],[30.4,'orbit','Las órbitas conocidas',''],
        [46,'paper','París, 1846',''],[61.3,'force','Un tirón invisible',''],
        [78.3,'orbit','Dos cálculos independientes',''],[92.4,'scope','Berlín','23 de septiembre de 1846'],
        [108,'ocular','A través del ocular',''],[125.5,'compare','El punto que sobra',''],
        [143,'motion','La noche siguiente',''],[157,'neptune','Neptuno',''],
        [173,'uranus','Todo empezó en Urano','']
    ];
    function starChart(mode,t,u){
        const cx=.5,cy=.46,r=w*.425;ctx.save();ctx.beginPath();ctx.arc(cx*w,cy*h,r,0,TAU);ctx.clip();
        ctx.fillStyle='#0b1925';ctx.fillRect(0,0,w,h);
        for(const [x,y,s] of field){dot(.07+x*.86,.22+y*.48,s*.0018,'#e4e2c9');}
        const dx=mode==='motion'?.045*smooth(u,.26,.72):0;
        const px=.56+dx,py=.437;
        const apparent=mode!=='compare'||(u*3)%1>.4;
        if(apparent)dot(px,py,.0062,'#b4dadd');
        if(u>.45 && mode!=='ocular'){
            ctx.beginPath();ctx.strokeStyle='#edb477';ctx.lineWidth=2;ctx.arc(px*w,py*h,.034*w,0,TAU);ctx.stroke();
        }
        ctx.restore();
        for(const f of [1,1.018]){ctx.beginPath();ctx.strokeStyle='#8e815e';ctx.lineWidth=1.6;ctx.arc(cx*w,cy*h,r*f,0,TAU);ctx.stroke();}
        for(let i=0;i<80;i++){const a=i*TAU/80;const end=r*(i%5?1.026:1.047);ctx.beginPath();ctx.moveTo(cx*w+r*Math.cos(a),cy*h+r*Math.sin(a));ctx.lineTo(cx*w+end*Math.cos(a),cy*h+end*Math.sin(a));ctx.stroke();}
        text(mode==='compare'?(apparent?'Vista por el telescopio':'Carta de referencia'):mode==='motion'?(dx>.022?'Segunda observación':'Primera observación'):'Campo estelar',.5,.737,.033,'#c9cabb','Georgia','center');
    }
    return t=>{
        const k=shots.findLastIndex(v=>v[0]<=t),[start,mode,title,sub]=shots[k],u=(t-start)/((shots[k+1]?.[0]??180)-start);
        ctx.drawImage(sky,0,0);globes.visible=['neptune','uranus'].includes(mode);orbit.visible=['orbit','force'].includes(mode);
        scope.visible=mode==='scope';sheet.visible=mode==='paper';cam.position.set(0,.15,5);cam.lookAt(0,0,0);
        if(globes.visible){
            planets.forEach((p,i)=>{p.visible=i===(mode==='uranus'?0:1);p.rotation.set(.13,t*.035,i?-.32:.96);});
            globes.scale.setScalar(k===0?1.02+u*.32:1.09+.09*Math.sin(u*3));
            globes.position.set(k===0?.29*(1-u):-.07,-.02,0);
        }
        if(orbit.visible){
            cam.position.set(.42+u*.6,5.1,4.6);cam.lookAt(0,0,0);orbit.rotation.y=.14;
            const a=t*.045;
            beads.forEach((p,i)=>{const r=[.57,1.03,1.59,2.36][i];
                const angle=i===3?(mode==='force'?a+1.05-2.10*u:a+.5):i===2?a:a*(2.4-i*.55)+i;
                p.position.set(r*Math.cos(angle),0,r*Math.sin(angle));});
            const data=pull.geometry.attributes.position;
            for(let i=0;i<2;i++){const p=beads[i+2].position;data.setXYZ(i,p.x,p.y,p.z);}data.needsUpdate=true;
            pull.visible=mode==='force'; orbit.scale.setScalar(.76);
        }
        if(scope.visible){scope.rotation.y=-.68+u*1.03;barrel.rotation.z=-.89+.27*smooth(u,.08,.9);cam.position.set(.3,.35,5.1-u*.46);cam.lookAt(0,-.05,0);}
        if(sheet.visible){sheet.rotation.set(-.14+u*.08,-.25+u*.27,.07-u*.12);cam.position.z=5.6-u*.8;}
        gl.render(scene,cam);ctx.drawImage(gl.domElement,0,0);
        if(['ocular','compare','motion'].includes(mode))starChart(mode,t,u);
        if(mode==='residual'){
            const y=.49;
            for(let i=0;i<10;i++)stroke(.13+i*.08,y-.125,.13+i*.08,y+.125,'#284654');
            stroke(.10,y,.91,y,'#8e9fa2',2);
            const draw=(color,off)=>{ctx.beginPath();ctx.lineWidth=3*w/864;ctx.strokeStyle=color;
                for(let i=0;i<=180;i++){const f=i/180,x=.12+f*.76,py=y-.049*Math.sin(f*4)+off*.10*Math.sin(f*6);if(i===0)ctx.moveTo(x*w,py*h);else ctx.lineTo(x*w,py*h);}ctx.stroke();};
            draw('#c9d0c0',0);draw('#ecc08d',1);
            const f=.04+.9*u;dot(.12+.76*f,y-.049*Math.sin(f*4)+.10*Math.sin(f*6),.009,'#ffc882');
            text('Calculada',.14,.665,.035,'#c9d0c0');text('Observada',.54,.665,.035,'#ecc08d');
        }
        ctx.save();ctx.shadowColor='#091321';ctx.shadowBlur=10;
        if(k===0){
            text(title,.08,.17,.094);text(sub,.08,.235,.094);
        }else{
            text(title,.08,.16,title.length>22?.051:.063);
            if(sub)text(sub,.082,.203,.036,'#c8ba99');
        }
        if(orbit.visible||mode==='residual')text('Esquema · distancias y desvíos exagerados',.5,.72,.026,'#b9c7ca','Georgia','center');
        if(mode==='neptune'&&k>0)text('Menos de 1° del punto predicho',.5,.715,.037,'#f0d193','Georgia','center');
        ctx.restore();
    };
}
