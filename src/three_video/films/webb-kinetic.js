import * as T from 'three';
import {smooth,mix} from './waterworks.js';
import {annotation,panel} from './kinetics.js';

const TAU=2*Math.PI;
export function makeModel(k,scene){
    const {group,mesh,box,rod,ring,disk}=k;
    const mirror=group(),left=group(mirror),right=group(mirror),fixed=group(mirror);
    const stand=group(),secondary=group(stand),actuators=group(),rays=group();
    const radius=.71,hinge=1.59;left.position.x=-hinge;right.position.x=hinge;
    const shape=new T.Shape();for(let n=0;n<6;n++){let a=n*Math.PI/3;n?shape.lineTo(radius*Math.cos(a),radius*Math.sin(a)):shape.moveTo(radius,0);}shape.closePath();
    const hex=new T.ExtrudeGeometry(shape,{depth:.105,bevelEnabled:true,bevelSize:.014,bevelThickness:.015,bevelSegments:1});
    const edges=new T.EdgesGeometry(hex,30),edgeMat=new T.LineBasicMaterial({color:0xffda72});
    const panels=[];
    for(let col=-2;col<=2;col++){const count=5-Math.abs(col);
        for(let row=0;row<count;row++){if(col===0&&row===2)continue;
            const x=col*radius*1.53,y=(row-(count-1)/2)*radius*1.77;
            const parent=col===-2?left:col===2?right:fixed;
            const segment=group(parent);segment.position.set(x-parent.position.x,y,.026*(x*x+y*y));
            const gold=[0xea8b05,0xffb72a,0xf7a213,0xd8900c,0xffc63f][(row+col+5)%5];
            const surface=mesh(hex,gold,0,0,0,segment);surface.add(new T.LineSegments(edges,edgeMat));
            const axis=new T.Vector3(-x*.023,-y*.023,1).normalize();surface.quaternion.setFromUnitVectors(new T.Vector3(0,0,1),axis);
            const film=new T.Shape();film.moveTo(-.60,-.06);film.lineTo(-.34,.42);film.lineTo(.13,.51);film.lineTo(-.43,-.34);film.closePath();
            mesh(new T.ShapeGeometry(film),gold===0xffc63f?0xffdf76:0xffc443,0,0,.131,segment);
            box(0x303455,0,0,-.18,.60,.89,.34,segment);
            panels.push(segment);
        }
    }
    // Sunshield layers are separated in this reconstruction so the five sheets read in silhouette.
    for(let j=0;j<5;j++){
        const q=new T.Shape();q.moveTo(0,-3.6);q.lineTo(2.3,-1.05);q.lineTo(2.1,2.0);q.lineTo(0,3.77);q.lineTo(-2.1,2);q.lineTo(-2.3,-1.05);q.closePath();
        const surface=mesh(new T.ShapeGeometry(q),[0x924486,0xc76a9d,0x6266a9,0x9c9ac8,0x929fb7][j],0,-3.13+j*.090,-.2,stand);
        surface.rotation.x=-Math.PI/2;
    }
    box(0x3b385b,0,-3.38,-.10,1.48,.72,1.55,stand);
    for(const [x,y] of [[-1.92,-1.36],[1.92,-1.36],[0,2.51]]){
        rod(0x869cb6,[x,y,.22],[0,0,3.03],.044,secondary);
    }
    disk(secondary,0,0,3.05,.29,.16,0x7386aa);disk(secondary,0,0,2.95,.24,.035,0xffc131);
    for(let n=0;n<6;n++){
        const a=TAU*n/6,X=.40*Math.cos(a),Y=.40*Math.sin(a);
        rod(0xaebbc6,[X*1.3,Y*1.3,-.62],[X,Y,-.21],.053,actuators);
        rod(0x29d9cc,[X,Y,-.3],[X*.9,Y*.9,-.04],.033,actuators);
    }
    disk(actuators,0,0,-.5,.15,.35,0xff6c54);
    actuators.position.set(0,0,0);
    const points=[[-1.12,.65,.23],[1.08,-.63,.23],[0,1.3,.23]];
    points.forEach(([x,y,z])=>{
        rod(0x55b7c7,[x,y,4.7],[x,y,z],.012,rays);rod(0x59c9ce,[x,y,z],[0,0,2.91],.018,rays);
    });
    rod(0xe0ab60,[0,0,2.91],[0,0,-.7],.021,rays);
    const dots=points.map(()=>mesh(new T.SphereGeometry(.087,10,8),0x8dffff,0,0,0,rays));
    const single=group();mesh(hex,0xffb928,0,0,.12,single);single.add(actuators);single.visible=false;
    const stars=new Float32Array(700*3);
    function noise(n){const f=Math.sin(n*41.321+2.71)*12453.78;return f-Math.floor(f);}
    for(let i=0;i<700;i++){stars[3*i]=(noise(i*3)-.5)*62;stars[3*i+1]=(noise(i*3+1)-.5)*70;stars[3*i+2]=-9-noise(i*3+2)*24;}
    const field=new T.BufferGeometry();field.setAttribute('position',new T.BufferAttribute(stars,3));
    scene.add(new T.Points(field,new T.PointsMaterial({color:0xcad6ff,size:.063,sizeAttenuation:true})));
    const locs=Array.from({length:18},(_,j)=>[95+(j%6)*97+noise(j)*22,824+Math.floor(j/6)*32+noise(j+52)*8]);
    const shots=[[0,0,0,2.6,1.5,12,7.3],[0,-.15,0,3.5,1.9,13,7.8],[0,.15,0,7,2,11,7.0],[0,.1,0,3.5,1.2,12,6.8],
        [1,.55,0,2,1,12,4.45],[0,.12,1.25,8,.6,8,6.85],[0,.15,0,.5,1,12,7.2],[0,.1,0,.2,1,12,6.9],
        [0,0,-.17,4.7,2.2,-8.2,2.2],[0,.1,0,1.0,.9,12,6.9],[0,0,0,1.6,.5,12,6.5],[0,-.3,0,3.6,1.5,13,7.8]];
    return {shots,update(i,s,t){
        let fold=0;if(i===2)fold=smooth(s,.8,10.8)*1.63;
        left.rotation.y=i===3?-1.63*(1-smooth(s,1,7)):-fold;
        right.rotation.y=i===3?1.63*(1-smooth(s,7,13)):fold;
        single.visible=i===8;mirror.visible=!single.visible;stand.visible=!single.visible;
        rays.visible=i===5;panels.forEach((p,j)=>p.rotation.y=i===7&&j===Math.floor(s*1.2)%18?.06*Math.sin(t*3.2):0);
        actuators.position.z=-.09+.06*Math.sin(t*1.1);
        dots.forEach((d,j)=>{const u=(t*.52+j/3)%1,[x,y,z]=points[j];
            if(u<.4)d.position.set(x,y,mix(4.7,z,u/.4));
            else if(u<.73){const f=(u-.4)/.33;d.position.set(x*(1-f),y*(1-f),mix(z,2.91,f));}
            else d.position.set(0,0,mix(2.91,-.7,(u-.73)/.27));});
    },overlay(c,i,s){
        if([0,6,7,9,10,11].includes(i)){
            const factor=i===9?1-smooth(s,1,12):i>=10?0:1;
            panel(c,i===7?'NIRCAM  /  IDENTIFY EACH SEGMENT':'NIRCAM  /  SIMULATED STAR IMAGE',ctx=>{
                locs.forEach(([x,y],j)=>{
                    const X=mix(360,x,factor),Y=mix(857,y,factor),active=i===7&&j===Math.floor(s*1.2)%18;
                    const size=active?12:i>=10?14-smooth(s,0,10)*5:7;
                    const halo=ctx.createRadialGradient(X,Y,0,X,Y,size);halo.addColorStop(0,active?'#ffe780':'#e3f2ff');halo.addColorStop(.32,active?'#ffbc29':'#85adff');halo.addColorStop(1,'rgba(94,106,233,0)');
                    ctx.fillStyle=halo;ctx.fillRect(X-size,Y-size,2*size,2*size);
                });
                if(i===11){ctx.strokeStyle='#bfcefb';ctx.lineWidth=1.2;for(let j=0;j<3;j++){const a=j*Math.PI/3;ctx.beginPath();ctx.moveTo(360-17*Math.cos(a),857-17*Math.sin(a));ctx.lineTo(360+17*Math.cos(a),857+17*Math.sin(a));ctx.stroke();}}
            });}
        if(i===1)annotation(c,'6.5 METRES','Primary mirror diameter','#ffca38');
        if(i===4)annotation(c,'GOLD COATING','Reflects infrared light','#ffca38');
        if(i===5)annotation(c,'LIGHT PATH','Primary → secondary → centre','#ffca38');
        if(i===8)annotation(c,'REAR CUTAWAY','Actuators adjust one segment','#ffca38');
    }};
}
