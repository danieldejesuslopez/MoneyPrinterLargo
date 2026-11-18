import * as T from 'three';
import {actor} from './keepalive.js';

const TAU=Math.PI*2, smooth=(n,a,b)=>T.MathUtils.smoothstep(n,a,b);
const v=(x,y,z)=>new T.Vector3(x,y,z);
const color=c=>new T.MeshBasicMaterial({color:c});
const ink='#293747', bone='#ffedce', navy='#273f50', teal='#548a88', red='#cf6656', brass='#cf9959';

function canvas(w,h){const cv=document.createElement('canvas');cv.width=w;cv.height=h;return [cv,cv.getContext('2d')];}
function artMaterial(cv){const map=new T.CanvasTexture(cv);map.colorSpace=T.SRGBColorSpace;return new T.MeshBasicMaterial({map,side:T.DoubleSide});}
function art(w,h,draw){const [cv,c]=canvas(w,h);draw(c,w,h);return artMaterial(cv);}
function signArt(title,subtitle,bg,fg){
    return art(740,280,(c,w,h)=>{
        c.fillStyle=bg;c.fillRect(0,0,w,h);c.strokeStyle=fg;c.lineWidth=3;c.strokeRect(17,17,w-34,h-34);
        c.textAlign='center';c.textBaseline='middle';c.fillStyle=fg;
        c.font='bold '+(subtitle?'78':'94')+'px Arial';c.fillText(title,w/2,subtitle?h*.42:h*.5,w*.85);
        if(subtitle){c.font='36px Georgia';c.fillText(subtitle,w/2,h*.72,w*.86);}
    });
}

export function createFilm(canvasOut,film){
    const id=film.episode,gl=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
    gl.setSize(canvasOut.width,canvasOut.height,false);
    const scene=new T.Scene(),cam=new T.PerspectiveCamera(36,canvasOut.width/canvasOut.height,.025,150);
    scene.background=new T.Color();
    const materials=new Map(),cube=new T.BoxGeometry(1,1,1),sphere=new T.SphereGeometry(1,30,20);
    const edges=new T.EdgesGeometry(cube),pen=new T.LineBasicMaterial({color:'#394b53',transparent:true,opacity:.62});
    function group(p,x=0,y=0,z=0){const g=new T.Group();p.add(g);g.position.set(x,y,z);return g;}
    function mat(c){if(typeof c!=='string')return c;if(!materials.has(c))materials.set(c,new T.MeshStandardMaterial({color:c,roughness:.8}));return materials.get(c);}
    function shape(p,g,c,x,y,z,a=1,b=a,d=a){const o=new T.Mesh(g,mat(c));p.add(o);o.position.set(x,y,z);o.scale.set(a,b,d);return o;}
    function box(p,c,x,y,z,a,b,d,outline=true){const o=shape(p,cube,c,x,y,z,a,b,d);if(outline)o.add(new T.LineSegments(edges,pen));return o;}
    function egg(p,c,x,y,z,a,b,d){return shape(p,sphere,c,x,y,z,a,b,d);}
    function rod(p,c,a,b,r,r2=r){const start=v(...a),end=v(...b),delta=end.clone().sub(start);const o=shape(p,new T.CylinderGeometry(r2,r,delta.length(),24),c,0,0,0);o.position.copy(start).lerp(end,.5);o.quaternion.setFromUnitVectors(v(0,1,0),delta.normalize());return o;}
    function plate(p,m,x,y,z,w,h){return shape(p,new T.PlaneGeometry(w,h),m,x,y,z);}
    function label(p,title,sub,x,y,z,w,h,bg=navy,fg=bone){return plate(p,signArt(title,sub,bg,fg),x,y,z,w,h);}
    function textureStrip(p,x,y,z,c,w){box(p,c,x,y,z,w,.075,.05,false);}
    const key=new T.DirectionalLight(0xffe4bf,2.0);key.position.set(-3,5,4);scene.add(key);
    scene.add(new T.HemisphereLight(0xdcefff,0xb4a1a0,1.55));
    const stage=group(scene);
    const props=[], sets=[], motions=[];
    function set(){const g=group(stage);sets.push(g);return g;}
    function prop(g,x,y,z){const o=group(g,x,y,z);props.push(o);return o;}
    function floor(p,c){box(p,c,0,-.09,0,20,.18,16);}
    function wall(p,c){box(p,c,0,2.1,-3,15,4.2,.2);textureStrip(p,0,.30,-2.87,navy,15);}
    function seatedBench(p,x,z){box(p,'#b47d55',x,.52,z,2.3,.13,.48);box(p,'#c89a69',x,.86,z-.22,2.3,.47,.08);for(let s of [-1,1])box(p,navy,x+s*.88,.24,z,.11,.52,.34);}
    function lightbulb(p,x,y,z){rod(p,ink,[x,y+.26,z],[x,y+.56,z],.022);shape(p,new T.ConeGeometry(.27,.22,24),brass,x,y+.15,z);egg(p,color('#ffdc95'),x,y+.05,z,.14,.065,.14);}
    function lunch(p,x,y,z){
        const g=group(p,x,y,z);box(g,red,0,0,0,.51,.30,.32);box(g,'#e49472',0,.16,0,.54,.065,.34);
        box(g,brass,0,.06,.176,.09,.105,.025);
        rod(g,ink,[-.12,.2,0],[-.12,.29,0],.018);rod(g,ink,[.12,.2,0],[.12,.29,0],.018);rod(g,ink,[-.12,.29,0],[.12,.29,0],.018);return g;
    }
    function umbrella(p,x,y,z){
        const g=group(p,x,y,z);rod(g,ink,[0,-.49,0],[0,.33,0],.021);
        shape(g,new T.ConeGeometry(.16,.75,9), '#eab443',0,-.09,0);
        const crook=new T.Mesh(new T.TorusGeometry(.075,.024,8,24,Math.PI),mat('#e5a64a'));crook.position.set(.075,.33,0);g.add(crook);return g;
    }
    function boat(p,x,y,z,scale,col){
        const g=group(p,x,y,z);g.scale.setScalar(scale);
        egg(g,col,0,.02,0,.77,.22,.29);box(g,'#efd7a0',.04,.23,0,.72,.34,.43);
        for(let i=0;i<3;i++)box(g,navy,-.20+i*.22,.26,.224,.15,.16,.02,false);
        box(g,red,.04,.44,0,.85,.085,.49);rod(g,brass,[.26,.49,0],[.26,.88,0],.028);return g;
    }
    function sea(p,c){box(p,c,0,-.22,-.2,18,.18,11,false);
        for(let i=0;i<70;i++){const x=Math.sin(i*89.12)*8,z=Math.cos(i*7.23)*4;
            box(p,i%2?'#6ca8ae':'#528f9d',x,-.11,z,.13+(i%6)*.09,.009,.022,false);}}
    function terrain(p){
        for(let i=0;i<7;i++){const x=-7+i*2.14;egg(p,i%2?'#465f6a':'#526b73',x,-.13,-4.5,2.1,.62+(i%3)*.35,1.24);}
    }
    const max=actor(id==='tomorrow'?'june':id==='harbour'?'nell':'bea'),partner=actor('boss'),other=actor('worker');
    const actors=[max,partner,other];actors.forEach(a=>stage.add(a.plane));
    let beats;
    if(id==='tomorrow'){
        const station=set();floor(station,'#63838c');wall(station,'#a9c5bb');
        for(let i=-6;i<7;i++){box(station,'#819d99',i*.96,2.1,-2.87,.018,4,.03,false);}
        textureStrip(station,0,2.74,-2.85,'#c88d5b',14);
        label(station,'NORTH STREET','LOST PROPERTY',-1,2.65,-2.78,1.9,.61);
        for(let i=0;i<7;i++)for(let j=0;j<2;j++){
            const x=1.23+(i-2)*.62,y=.7+j*1.02;box(station,'#548488',x,y,-2.54,.59,.96,.36);
            for(let k=0;k<3;k++)textureStrip(station,x,y+.3-k*.045,-2.35,navy,.28);
            box(station,brass,x+.15,y-.07,-2.33,.045,.13,.03);
        }
        const locker=prop(station,.88,1.42,-1.13);
        box(locker,navy,0,0,-.19,.98,1.15,.45);
        const door=group(locker,-.49,0,.05);
        box(door,teal,.49,0,0,.96,1.13,.065);
        label(door,'09','FOUND ITEMS',.49,.18,.044,.58,.28);
        box(door,brass,.82,-.18,.07,.08,.15,.05);
        const umb=umbrella(locker,0,-.03,.10);const tin=lunch(locker,0,-.22,.19);
        const tag=label(locker,'LAST CHANCE','FRIDAY  /  23:58',0,.05,.23,.77,.42,'#f0ce84',ink);
        const ticket=label(locker,'06:14','TOMORROW',0,-.20,.25,.50,.27,'#f4db9d',ink);
        motions.push(t=>{door.rotation.y=-1.49*(.28+.72*smooth(t%15,1.1,5));umb.visible=t<46;tin.visible=t>=46&&t<90;tag.visible=t>=90&&t<165;ticket.visible=t>=28&&t<46;});
        const rail=set();floor(rail,'#a2a2a0');wall(rail,'#77969b');seatedBench(rail,-.25,-.65);
        label(rail,'NORTH STREET','PLATFORM   2',.7,2.55,-2.81,2.7,.70);
        box(rail,'#e1ba67',0,.015,.88,12,.026,.16,false);
        const train=group(rail);box(train,'#597782',0,1.16,-2.18,12,2.25,.76);
        textureStrip(train,0,.80,-1.775,red,12);
        for(let i=-5;i<=5;i++){
            box(train,'#f0d5a4',i*1.08,1.54,-1.77,.82,.61,.03);
            box(train,ink,i*1.08,1.55,-1.75,.032,.61,.01,false);
        }
        const platformProp=prop(rail,.67,1.02,-.28);const u2=umbrella(platformProp,0,0,0);const tin2=lunch(platformProp,0,0,0);
        const bottle=rod(platformProp,'#789051',[.34,-.10,.05],[.34,.23,.05],.085);
        const lid=rod(platformProp,brass,[.34,.23,.05],[.34,.28,.05],.094);
        motions.push(t=>{bottle.visible=lid.visible=t>=150;u2.visible=t<60;tin2.visible=t>=60;train.position.x= (1-smooth(t%15,0,7))*(-6.5)+smooth(t%15,11,15)*4;});
        const archive=set();floor(archive,'#647d84');wall(archive,'#c4bc9e');for(let j=0;j<3;j++)for(let i=0;i<7;i++){
            box(archive,brass,-3+i*.95,.65+j*.84,-2.69,.83,.60,.39);
            label(archive,String(101+i+j*7),'FILED',-3+i*.95,.66+j*.84,-2.48,.48,.19,'#f2e1b2',ink);
        }
        const counter=prop(archive,.60,1.02,-.54);box(counter,teal,0,-.51,0,1.4,.94,.76);
        const archiveTin=lunch(counter,0,.17,0);const tel=label(counter,'CALLING…','CONTACT ON FILE',0,.41,.14,.58,.32,navy,bone);
        motions.push(t=>{tel.visible=t<90||t>130;archiveTin.rotation.y=.24*Math.sin(t*.8);});
        beats=[[0,0],[2,2],[0,0],[1,1],[2,2],[2,2],[0,0],[0,0],[1,1],[2,2],[1,1],[0,0]];
    } else if(id==='harbour'){
        const gallery=set();floor(gallery,'#c5946a');wall(gallery,'#738e88');
        for(let i=-5;i<6;i++){box(gallery,brass,i*1.06,.82,-2.86,.026,1.64,.035,false);}
        textureStrip(gallery,0,1.62,-2.84,'#c0a275',14);
        const painting=prop(gallery,.34,1.92,-1.90);
        const painted=art(640,480,(c,w,h)=>{
            const sky=c.createLinearGradient(0,0,0,h);sky.addColorStop(0,'#819eb5');sky.addColorStop(.64,'#eac394');sky.addColorStop(.65,'#62909b');sky.addColorStop(1,'#315b69');c.fillStyle=sky;c.fillRect(0,0,w,h);
            for(let i=0;i<120;i++){c.strokeStyle=i%2?'#f1d7a844':'#30526933';c.lineWidth=2+i%4;const x=(i*199)%640,y=(i*91)%480;c.beginPath();c.moveTo(x,y);c.lineTo(x+12+i%28,y-1);c.stroke();}
            c.fillStyle='#626e68';c.beginPath();c.moveTo(0,170);c.lineTo(61,193);c.lineTo(95,268);c.lineTo(185,310);c.lineTo(0,327);c.fill();
            c.fillStyle='#7a716e';c.beginPath();c.moveTo(640,200);c.lineTo(570,237);c.lineTo(535,289);c.lineTo(444,310);c.lineTo(640,327);c.fill();
        });
        const frame=group(painting);box(frame,brass,0,0,-.09,2.03,1.48,.18);box(frame,'#543f32',0,0,.018,1.89,1.35,.045);plate(frame,painted,0,0,.055,1.78,1.24);
        const magnifier=group(painting,0,-.09,.36);
        shape(magnifier,new T.TorusGeometry(.20,.023,12,40),brass,0,0,0);
        rod(magnifier,navy,[.12,-.16,0],[.34,-.40,0],.031);
        const glass=shape(magnifier,new T.CircleGeometry(.19,32),new T.MeshBasicMaterial({color:'#b6e3de',transparent:true,opacity:.17}),0,0,.001);
        motions.push(t=>{magnifier.visible=t>=30&&t<45;magnifier.position.x=.26*Math.sin(t*.5);});
        const ferry=boat(frame,.15,-.20,.09,.30,red);
        label(gallery,'THE HARBOUR','Gallery Two',.34,.90,-1.86,.82,.26,'#ecd6a8',ink);
        const otherFrame=box(gallery,brass,-2.65,2.08,-2.68,1.29,.95,.13);plate(otherFrame,painted,0,0,.51,.88,.81);
        for(let i=0;i<4;i++)lightbulb(gallery,-3.3+i*1.75,3.43,-1.95);
        motions.push(t=>{ferry.visible=t>14?Math.floor(t/4)%3!==1:t<6.5;ferry.position.y=-.21+.018*Math.sin(t*2);frame.rotation.y=t>=59&&t<75?.50*smooth(t,60,68):.05*Math.sin(t*.45);});
        const room=set();floor(room,'#736c60');wall(room,'#697775');
        for(let j=0;j<4;j++){box(room,'#997654',-1.9+j*.85,.44,-2.15,.76,.88,.69);box(room,'#debe92',-1.9+j*.85,.46,-1.79,.18,.81,.015);}
        const optics=prop(room,.35,1.65,-.85);
        rod(optics,ink,[-.98,0,0],[.99,.05,-.3],.17);
        for(const x of [-.92,-.10,.64,.92])rod(optics,brass,[x,-.005,-.15*x-.14],[x+.06,.005,-.15*x-.14],.192);
        const mirror=box(optics,'#a9d7d6',.80,.15,-.34,.32,.50,.06);mirror.rotation.y=.45;
        const ray=rod(optics,color('#f7c688'),[-1.35,0,.035],[-.9,0,0],.025);
        motions.push(t=>{ray.scale.x=.85+.15*Math.sin(t*3);mirror.rotation.y=.40+.07*Math.sin(t*.7);});
        const port=set();sea(port,'#426e7b');terrain(port);
        for(let i=-4;i<4;i++)box(port,'#a18062',i*.55,.22,-1.7,.52,.15,.76);
        const real=prop(port,.33,.57,-.50);const redBoat=boat(real,0,0,0,1.06,red);const bin=box(real,'#507859',.93,.22,.12,.28,.46,.27);
        const moon=egg(port,color('#ffd995'),-1.9,3.10,-4,.40,.40,.09);
        motions.push(t=>{redBoat.position.y=.047*Math.sin(t*2);redBoat.rotation.z=.045*Math.sin(t*1.2);bin.visible=t>149;moon.position.x=-1.9+.12*Math.sin(t*.14);});
        beats=[[0,0],[0,0],[0,0],[0,0],[0,0],[0,0],[1,1],[1,1],[2,2],[2,2],[2,2],[0,0]];
    } else {
        const tower=set();sea(tower,'#356579');terrain(tower);
        const lighthouse=prop(tower,.38,.25,-.55);
        rod(lighthouse,'#f4d5ae',[0,0,0],[0,2.23,0],.52,.34);rod(lighthouse,'#b9524c',[0,.64,0],[0,1.05,0],.47,.434);
        rod(lighthouse,ink,[0,2.23,0],[0,2.34,0],.52);
        for(let i=0;i<6;i++){const a=i*TAU/6;rod(lighthouse,ink,[.33*Math.cos(a),2.33,.33*Math.sin(a)],[.33*Math.cos(a),2.82,.33*Math.sin(a)],.025);}
        egg(lighthouse,color('#ffe3a4'),0,2.55,0,.22,.22,.22);
        shape(lighthouse,new T.ConeGeometry(.51,.30,12),'#a35046',0,2.95,0);
        const sweep=group(lighthouse,0,2.56,0);
        const beam=shape(sweep,new T.ConeGeometry(.42,3.1,20,1,true),new T.MeshBasicMaterial({color:'#ffe5a1',transparent:true,opacity:.13,depthWrite:false,side:T.DoubleSide}),1.58,0,0);beam.rotation.z=Math.PI/2;
        motions.push(t=>sweep.rotation.y=t*1.22);
        const hill=set();floor(hill,'#64776a');terrain(hill);
        box(hill,'#777c78',0,.015,.42,8,.05,.95);
        const shelter=prop(hill,.5,1.23,-.96);
        for(const x of [-.83,.83])rod(shelter,teal,[x,-1.22,-.21],[x,1,-.21],.065);
        box(shelter,teal,0,1.03,-.16,2.02,.15,.97);box(shelter,'#91a7a0',0,-.02,-.37,1.62,1.93,.06);
        label(shelter,'QUARRY ROAD','LAST BUS  18:10',0,.55,-.31,1.24,.36);
        box(shelter,brass,0,-.65,-.09,1.56,.10,.43);
        const mirror=box(shelter,'#e4bd7a',.49,1.38,-.15,.58,.57,.12);mirror.rotation.z=-.10;
        const gleam=box(shelter,color('#fce6a4'),.49,1.38,-.076,.48,.48,.011,false);
        for(let i=0;i<4;i++){const x=-2.2-i*.52;egg(hill,'#d8d5c4',x,.43,-2.14,.27,.23,.18);egg(hill,ink,x+.20,.46,-2,.10,.135,.10);}
        motions.push(t=>{gleam.visible=Math.sin(t*6)>-.2;});
        const quarry=set();sea(quarry,'#4b7682');terrain(quarry);
        const van=prop(quarry,.40,.15,-.44);
        box(van,'#b18c51',0,.32,0,1.72,.77,.80);box(van,'#e2bc79',-.15,.83,0,1.35,.45,.78);
        box(van,'#6e9daa',-.58,.83,.40,.37,.32,.014);
        label(van,'ISLAND POST','No. 04',.27,.43,.414,.88,.32,'#ae754c','#f9eaca');
        for(const x of [-.59,.6])egg(van,ink,x,.02,.32,.19,.19,.13);
        const parcel=box(van,'#527b9e',.34,1.18,0,.32,.25,.25);const rescue=boat(quarry,-2,.10,.35,.69,red);
        motions.push(t=>{rescue.position.x=-2.6+1.73*smooth(t,135,151);rescue.position.y=.11+.06*Math.sin(t*1.8);parcel.rotation.y=.1*Math.sin(t);});
        beats=[[0,0],[1,1],[0,0],[1,1],[1,1],[1,1],[2,2],[2,2],[0,0],[2,2],[2,2],[1,1]];
    }
    const out=canvasOut.getContext('2d');
    // Each beat moves from a situating shot to evidence, then a character reaction.
    return t=>{
        const b=Math.min(11,Math.floor(t/15)),local=t%15,[s,p]=beats[b];
        sets.forEach((set,i)=>set.visible=i===s);motions.forEach(fn=>fn(t));
        const outside=(id==='harbour'&&s===2)||(id==='inland'&&s!==1);
        scene.background.set(id==='inland'?(b>10?'#b5c0ac':'#455b72'):outside?'#dfc1a9':id==='tomorrow'?'#a7c4bb':'#b5b99f');
        const object=props[p].getWorldPosition(new T.Vector3());
        let focusY=object.y;
        if(id==='inland')focusY=s===0?1.94:s===2?.95:1.96;
        if(id==='harbour'&&s===2)focusY=.88;
        if(id==='tomorrow'&&s===1)focusY=1.12;
        const heroX=-.67+.09*Math.sin(t*.5);
        max.plane.position.set(heroX,1.10,-.20);
        partner.plane.position.set(.92,1.12,.03);other.plane.position.set(-2.0,1.09,-1.45);
        const duo=(id==='tomorrow'&&[4,5,8,9,10].includes(b))||(id==='harbour'&&[0,1,2,4,5,10].includes(b))||(id==='inland'&&[7,9,10].includes(b));
        if(id==='inland'&&s===2){partner.plane.position.set(.30,2.30,-.5);partner.plane.scale.setScalar(.83);}
        else partner.plane.scale.setScalar(1);
        partner.plane.visible=duo;other.plane.visible=id==='tomorrow'&&s===1;
        const reaction=(b===0||b===4||b===7)?'panic':b===11?'smug':'skeptic';
        const gesture=local>5&&local<9?'point':local>11?'fold':b%3===2?'wave':'idle';
        max.draw(t,gesture,reaction,local<1.8&&b%3===1?local+.05:0);
        if(duo)partner.draw(t,local>7?'point':'fold',b<6?'skeptic':'smug');if(other.plane.visible)other.draw(t,'idle','normal');
        const shot=local<4.5?0:local<9.2?1:local<12.0?2:3;
        const target=v(0,1.52,-.62),eye=v(.30,2.12,5.8);
        if(shot===0&&outside){eye.set(1.3,2.33,6.45);target.set(0,1.44,-.7);}
        if(shot===1){
            target.set(object.x,focusY,object.z);eye.set(object.x+.20+.14*Math.sin(t*.44),focusY+.26,object.z+(id==='harbour'&&s===0?4.9:id==='inland'&&s===0?5.75:3.85));
            // Prop inserts must be unobstructed by the foreground performers.
            actors.forEach(a=>a.plane.visible=false);
        }else if(shot===2){
            const ch=duo&&b%2?partner:max;const pos=ch.plane.position;
            target.set(pos.x,pos.y+.52,pos.z);eye.set(pos.x+.18,pos.y+.64,pos.z+2.05);
        }else if(shot===3){target.set(duo?.12:-.28,1.58,-.48);eye.set(-.51,1.93,duo?5.15:4.72);}
        eye.z-=.10*(local%4)/4;cam.position.copy(eye);cam.lookAt(target);
        actors.forEach(a=>a.plane.rotation.y=.45*Math.atan2(eye.x-a.plane.position.x,eye.z-a.plane.position.z));
        max.plane.visible=shot!==1;
        gl.render(scene,cam);out.drawImage(gl.domElement,0,0);
        if(t<4.3){
            const W=canvasOut.width,H=canvasOut.height;out.save();out.globalAlpha=1-smooth(t,3.5,4.3);
            out.fillStyle='rgba(22,36,43,.85)';out.fillRect(0,H*.06,W,H*.093);
            out.fillStyle='#f5e4bf';out.font='bold '+Math.round(W*.052)+'px Georgia';out.textAlign='center';
            out.fillText(film.title,W*.5,H*.119,W*.93);out.restore();
        }
    };
}
