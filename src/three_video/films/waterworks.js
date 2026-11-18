import * as T from 'three';
import {makeWaal} from './waal-landscape.js';
import {makeBarrier} from './thames-model.js';
import {makeLocks} from './gatun-model.js';

export const smooth=(x,a,b)=>T.MathUtils.smoothstep(x,a,b);
export const mix=T.MathUtils.lerp;
export const palette={water:0x2c8291,waterLight:0x8fd3cd,land:0xa1ae80,tree:0x54745d,cream:0xf1dbc0,brick:0xc48368,concrete:0xb0aa96,ink:0x253c44,amber:0xf6b76d};
export function toolkit(scene){
    const cube=new T.BoxGeometry(), mats=new Map();
    const group=(parent=scene)=>{const obj=new T.Group();parent.add(obj);return obj;};
    const mesh=(geometry,color,x,y,z,parent=scene)=>{
        if(!mats.has(color))mats.set(color,new T.MeshStandardMaterial({color,roughness:.82,side:T.DoubleSide}));
        const obj=new T.Mesh(geometry,mats.get(color));obj.position.set(x,y,z);parent.add(obj);return obj;
    };
    const box=(color,x,y,z,w,h,d,parent)=>{const obj=mesh(cube,color,x,y,z,parent);obj.scale.set(w,h,d);return obj;};
    const rod=(color,a,b,r,parent)=>{
        const start=new T.Vector3(...a),end=new T.Vector3(...b),dir=end.clone().sub(start);
        const m=mesh(new T.CylinderGeometry(r,r,dir.length(),8),color,...start.clone().lerp(end,.5).toArray(),parent);
        m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),dir.normalize());return m;
    };
    const ribbon=(color,left,right,start,end,y,parent)=>{
        const pts=[],indices=[];
        for(let i=0;i<=110;i++){
            const z=mix(start,end,i/110);pts.push(left(z),y,z,right(z),y,z);
            if(i){const a=2*i;indices.push(a-2,a,a-1,a-1,a,a+1);}
        }
        const g=new T.BufferGeometry();g.setAttribute('position',new T.Float32BufferAttribute(pts,3));g.setIndex(indices);g.computeVertexNormals();return mesh(g,color,0,0,0,parent);
    };
    const crown=new T.IcosahedronGeometry(1,0),roof=new T.ConeGeometry(1,1,4);roof.rotateY(Math.PI/4);
    const tree=(x,y,z,scale=1,parent)=>{
        box(0x70674b,x,y+.19*scale,z,.09*scale,.38*scale,.09*scale,parent);
        const m=mesh(crown,palette.tree,x,y+.55*scale,z,parent);m.scale.set(.31*scale,.4*scale,.3*scale);
    };
    const house=(x,y,z,s,color=palette.brick,parent)=>{
        box(palette.cream,x,y+.27*s,z,.69*s,.54*s,.84*s,parent);
        const r=mesh(roof,color,x,y+.71*s,z,parent);r.scale.set(.54*s,.36*s,.66*s);
        box(palette.ink,x+.12*s,y+.28*s,z+.43*s,.15*s,.19*s,.015,parent);
    };
    const vessel=(size=1)=>{
        const g=group(),hull=new T.Shape();hull.moveTo(-.48,-1.45);hull.lineTo(.48,-1.45);hull.lineTo(.48,.96);hull.quadraticCurveTo(.36,1.45,0,1.7);hull.quadraticCurveTo(-.36,1.45,-.48,.96);hull.closePath();
        const geo=new T.ExtrudeGeometry(hull,{depth:.35,bevelEnabled:true,bevelSize:.06,bevelThickness:.05,bevelSegments:1});geo.rotateX(-Math.PI/2);mesh(geo,0x903f36,0,0,0,g);
        box(0x344d56,0,.35,.05,.89,.12,2.35,g);for(let i=0;i<4;i++)for(let j=0;j<2;j++)box([0xd39351,0x58847f,0xab604f][(i+j)%3],(j-.5)*.41,.50,-.78+i*.40,.36,.27,.34,g);
        box(0xe4ddcc,0,.63,1.0,.79,.55,.49,g);box(0x364f58,0,.74,.74,.64,.12,.02,g);g.scale.setScalar(size);return g;
    };
    return {group,mesh,box,rod,ribbon,tree,house,vessel};
}

function drawInset(ctx,type,k){
    const left=52,right=665,col='#eab16a',water='#75c8cb';
    ctx.fillStyle='rgba(23,44,50,.95)';ctx.fillRect(30,666,660,216);
    function label(str,x,y,size=19,c='#faf1d9'){ctx.fillStyle=c;ctx.font='bold '+size+'px Arial';ctx.fillText(str,x,y);}
    function path(points,color,width=3){
        ctx.beginPath();points.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();
    }
    if(type==='canal'){
        label('ORIGINAL LOCK ROUTE',left,700);
        const profile=[[0,0],[.10,0],[.10,.33],[.15,.33],[.15,.67],[.20,.67],[.20,1],[.69,1],[.69,.66],[.79,.66],[.79,.33],[.85,.33],[.85,0],[1,0]];
        const points=profile.map(([a,b])=>[left+a*(right-left),815-75*b]);path(points,water,4);
        let y=0;for(const [a,b] of profile)if(a<=k)y=b;
        ctx.fillStyle=col;ctx.beginPath();ctx.arc(left+k*(right-left),815-75*y,8,0,Math.PI*2);ctx.fill();
        label('Atlantic',left,850,18);label('Gatun Lake   +26 m',242,733,19);label('Pacific',594,850,18);
    }else if(type==='forecast'||type==='decision'){
        label(type==='forecast'?'CONTROL ROOM INPUTS':'CLOSURE DECISION',left,701);
        ['River flow','Astronomical tide','Storm surge'].forEach((n,i)=>{
            const x=left+i*208;label(n,x,750,18);
            const pts=Array.from({length:39},(_,j)=>[x+j*4.5,808-(i===0?j*.68+3*Math.sin(j*.2):i===1?14*Math.sin(j/9-.8):26*Math.exp(-((j-23)**2)/75))]);
            path(pts,i===1?water:i===2?col:'#b6c7a2',3);path([[x,825],[x+174,825]],'#768987',1);
        });
        label(type==='forecast'?'Illustrative signals':'The duty controller weighs all three.',left,861,17,'#c5d1cb');
    }else if(type==='tide'){
        label('FORECAST SURGE TIMING',left,701);const pts=[];
        for(let j=0;j<100;j++){const u=j/99;pts.push([left+u*600,804+26*Math.cos((u-.15)*Math.PI*1.7)]);}
        path(pts,water,4);
        [[.17,'Close near low tide'],[.76,'Expected peak']].forEach(([u,n])=>{const x=left+u*600;path([[x,736],[x,830]],col,2);label(n,x-74,859,16);});
    }else if(type==='level'){
        label('HIGH-WATER REDUCTION',left,700);
        [['Target',27,'#98b8b2'],['Achieved',35,col]].forEach(([name,value,color],i)=>{
            let y=744+i*64;label(name,left,y,19);ctx.fillStyle=color;ctx.fillRect(175,y-16,value*10.2*smooth(k,0,.45),18);label(value+' cm',545,y,21,color);
        });
    }
}

export function createFilm(canvas,film){
    const width=canvas.width,height=canvas.height;
    const renderer=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
    renderer.setSize(width,height,false);renderer.outputColorSpace=T.SRGBColorSpace;
    const scene=new T.Scene();scene.background=new T.Color(0xa8b7ad);
    scene.add(new T.HemisphereLight(0xfff2df,0xb7c9c5,2.0));
    const sun=new T.DirectionalLight(0xffe7c9,2.6);sun.position.set(-12,19,6);scene.add(sun);
    const camera=new T.OrthographicCamera(-6,6,10,-10,.1,140);
    const model=({waal:makeWaal,thames:makeBarrier,gatun:makeLocks})[film.episode](toolkit(scene));
    const ctx=canvas.getContext('2d'),W=720,H=1280,projected=new T.Vector3();
    const location={waal:'NIJMEGEN  /  NETHERLANDS',thames:'WOOLWICH  /  LONDON',gatun:'GATUN  /  PANAMA'}[film.episode];
    const source={waal:'Climate-ADAPT  /  Rijkswaterstaat',thames:'Environment Agency  /  The Thames Barrier',gatun:'Panama Canal Authority  /  Design of the Locks'}[film.episode];
    function txt(str,x,y,font,col='#fff5dd'){ctx.fillStyle=col;ctx.font=font;ctx.fillText(str,x,y);}
    function flag(tag){
        const [name,x,y,z,dx,dy]=tag;projected.set(x,y,z).project(camera);
        const cx=(projected.x+1)*W/2,cy=(1-projected.y)*H/2;
        if(cy<230||cy>890||cx<8||cx>W-8)return;
        ctx.font='bold 19px Arial';const w=ctx.measureText(name).width+22;
        const bx=Math.max(19,Math.min(W-w-32,cx+dx)),by=Math.max(236,Math.min(850,cy+dy));
        ctx.strokeStyle='#f9f2db';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(cx,cy);ctx.lineTo(bx+w/2,by+14);ctx.stroke();
        ctx.fillStyle='#ffe4a8';ctx.beginPath();ctx.arc(cx,cy,4,0,Math.PI*2);ctx.fill();
        ctx.fillStyle='#233b41';ctx.fillRect(bx,by,w,30);txt(name,bx+11,by+21,'bold 19px Arial');
    }
    return time=>{
        const i=Math.min(11,Math.floor(time/15)),s=time-i*15;
        const data=model.frame(i,s,time),[x,y,z]=data.aim,[ex,ey,ez]=data.eye,span=data.span;
        camera.left=-span/2;camera.right=span/2;camera.top=span*H/W/2;camera.bottom=-camera.top;camera.updateProjectionMatrix();
        camera.position.set(x+ex,y+ey,z+ez);camera.lookAt(x,y,z);camera.updateMatrixWorld();
        renderer.render(scene,camera);ctx.setTransform(width/W,0,0,height/H,0,0);ctx.drawImage(renderer.domElement,0,0,W,H);
        const gradient=ctx.createLinearGradient(0,0,0,280);gradient.addColorStop(0,'#162b32');gradient.addColorStop(.59,'rgba(22,43,50,.94)');gradient.addColorStop(1,'rgba(22,43,50,0)');
        ctx.fillStyle=gradient;ctx.fillRect(0,0,W,280);
        txt(location,34,62,'bold 20px Arial','#efbd7a');
        txt(data.head,32,125,'bold 48px Georgia');
        ctx.fillStyle='#eebd79';ctx.fillRect(34,149,56,4);
        (data.tags||[]).forEach(flag);
        const shade=ctx.createLinearGradient(0,850,0,H);shade.addColorStop(0,'rgba(21,39,44,0)');shade.addColorStop(.49,'rgba(21,39,44,.87)');shade.addColorStop(1,'#15272c');
        ctx.fillStyle=shade;ctx.fillRect(0,850,W,H-850);
        if(data.diagram)drawInset(ctx,data.diagram,data.progress);
        if(data.note){ctx.fillStyle='#ffdb9b';ctx.font='bold 27px Arial';ctx.fillText(data.note,34,962,645);}
        txt(i===11?source:'SCHEMATIC RECONSTRUCTION',34,1195,'18px Arial','#c2d0ca');
        ctx.fillStyle='#526562';ctx.fillRect(34,1215,652,3);ctx.fillStyle='#edb671';ctx.fillRect(34,1215,652*time/180,3);
    };
}
