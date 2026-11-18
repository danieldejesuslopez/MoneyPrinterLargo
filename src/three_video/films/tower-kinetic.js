import * as T from 'three';
import {smooth,mix} from './waterworks.js';
import {annotation,panel} from './kinetics.js';

export function makeModel(k){
    const {group,mesh,box,rod,gear,disk,ring}=k,stone=0xf2b59a,night=0x18395d,blue=0x0873bd,hot=0xff5735,gold=0xffb22b;
    const world=group(),architecture=group(world),near=group(architecture),engine=group(),leaves=[],cogs=[];
    box(0x046c97,0,-1.05,0,42,.16,46,world);
    for(let j=0;j<34;j++){
        const x=-18+(j%17)*2.24,z=-9-Math.floor(j/17)*2.3,y=1.1+Math.abs(Math.sin(j*4))*.94;
        box(j%2?0x8767aa:0xa36fa0,x,y/2-.96,z,1.31,y,1.45,world);
    }
    const pierGroups=[];
    for(const side of [-1,1]){
        const x=2.48*side,g=group(architecture);pierGroups.push(g);
        box(0xb06966,x,-.18,0,1.53,1.80,2.52,g);
        for(const z of [-.87,.87]){
            const part=z>0?near:g;
            box(stone,x,2.12,z,1.11,4.68,.54,part);
            box(0xc98078,x,3.57,z,1.21,.19,.67,part);
            box(0xf8cfad,x,4.26,z,1.19,.29,.68,part);
            for(const dx of [-.46,.46]){
                const turret=mesh(new T.CylinderGeometry(.17,.21,.86,8),stone,x+dx,4.63,z,part);
                mesh(new T.ConeGeometry(.23,.64,8),blue,x+dx,5.34,z,part);
                rod(gold,[x+dx,5.62,z],[x+dx,5.90,z],.019,part);
            }
            mesh(new T.ConeGeometry(.59,.75,4),0x345a84,x,4.78,z,part).rotation.y=Math.PI/4;
            for(let m=0;m<3;m++)for(const xx of [-.25,.25]){
                box(night,x+xx,1.66+m*.72,z+(z>0?.278:-.278),.17,.42,.03,part);
            }
        }
        box(0xb3b5b9,x+side*3.4,.56,0,5.8,.24,1.28,architecture);
        for(const z of [-.67,.67]){
            let prev=[x,3.8,z];for(let j=1;j<=12;j++){const f=j/12,X=x+side*f*5.8,Y=.82+3.0*(1-f)**2;
                rod(blue,prev,[X,Y,z],.060,architecture);
                if(j%2===0)rod(blue,[X,.67,z],[X,Y,z],.031,architecture);prev=[X,Y,z];}
        }
        const arm=group(world);arm.position.set(x,.63,0);leaves.push([arm,side]);
        box(0x344e69,-side*1.24,.01,0,2.48,.17,1.26,arm);
        for(const z of [-.62,.62]){
            box(hot,-side*1.24,.01,z,2.48,.18,.055,arm);
            rod(blue,[0,.29,z],[-side*2.46,.29,z],.036,arm);
            for(let n=0;n<10;n++)box(blue,-side*n*.26,.16,z,.026,.29,.03,arm);
        }
        box(hot,side*.57,-.26,0,.96,.65,1.01,arm);
        for(let n=0;n<7;n++)box(0xc03832,side*.57,-.54+n*.096,.514,.92,.018,.023,arm);
        disk(arm,0,0,.69,.17,.14,gold);
        // The curved rack is concentric with the pivot; the drive gear meshes at its rim.
        for(let n=0;n<27;n++){
            const a=mix(-Math.PI*.68,Math.PI*.22,n/26);
            const X=side*.76*Math.cos(a),Y=.76*Math.sin(a);
            box(0xffb136,X,Y,.56,.11,.095,.18,arm).rotation.z=side*a;
        }
        cogs.push(gear(world,x+side*.89,.49,.63,.236,11,blue));
    }
    for(const z of [-.67,.67]){
        box(0x1c83be,0,3.54,z,5.05,.27,.3,architecture);
        for(let j=0;j<20;j++)box(0xb8edf2,-2.36+j*.25,3.55,z+.16,.13,.13,.015,architecture);
    }
    const ship=k.vessel(.74);world.add(ship);
    rod(night,[0,.49,0],[0,2.72,0],.037,ship);
    const flag=mesh(new T.PlaneGeometry(.47,.19),hot,.22,2.55,0,ship);
    // Detached Victorian engine-room cutaway with moving flywheel, piston and accumulator.
    box(night,0,-1,0,9,.30,4,engine);box(0x763b63,0,1,-1.55,9,4,.21,engine);
    const boiler=mesh(new T.CylinderGeometry(.80,.80,1.72,36),0xe97439,-2.8,.04,0,engine);boiler.rotation.z=Math.PI/2;
    disk(engine,-2.88,.03,.61,.48,.18,night);disk(engine,-2.88,.03,.73,.30,.05,hot);
    const fly=gear(engine,-.56,.15,.15,.80,23,0x009aaf);
    const piston=box(gold,-1.32,.16,.27,.48,.19,.22,engine);
    const link=rod(hot,[-1.20,.15,.35],[-.56,.55,.35],.085,engine);
    box(0x287da2,1.87,.08,0,1.15,1.93,.96,engine);
    const pistonTop=box(hot,1.87,1.14,0,1.28,.62,1.05,engine);
    const ram=box(0xb6e6ed,1.87,.92,0,.37,1.24,.4,engine);
    for(let n=0;n<5;n++)box(gold,1.87,-.63+n*.35,.492,.79,.037,.03,engine);
    rod(0x18b4cb,[-.4,-.52,.54],[1.87,-.52,.54],.10,engine);
    const pulses=Array.from({length:9},()=>mesh(new T.SphereGeometry(.063,8,6),0xbaff55,0,-.52,.65,engine));
    const shots=[[0,1.65,0,3.4,2.4,12,8.25],[0,1.7,0,5,4,12,10.1],[-2.05,1,0,2,2.3,12,6.0],[-2.40,.55,0,1.2,2,12,4.0],[-2.12,.42,0,.5,1.1,12,4.5],[-2.77,.52,0,.6,1,12,3.5],[-1.25,.02,0,1.4,2.8,12,6.9],[1.10,.68,0,2,1.5,12,5.2],[0,.35,0,2.4,2.4,12,7.6],[-2.1,.54,0,2,2.5,12,5.2],[0,1.85,0,3.2,3.1,12,8.9],[0,1.45,0,3.6,2.3,12,8.0]];
    return {shots,update(i,s,t){
        const mech=i>=6&&i<=8;
        world.visible=!mech;engine.visible=mech;
        near.visible=![2,3,4,5,9].includes(i);
        pierGroups[0].visible=![2,3,4,5,9].includes(i);
        let f=i===10?1-smooth(s,8,15):i===0?smooth(s,1,10):i===1?.06:i===4?smooth(s,1,12):.05+.91*smooth(s,1,13);
        leaves.forEach(([arm,side],j)=>{arm.rotation.z=-side*f*1.30;cogs[j].rotation.z=side*f*4.63;});
        ship.position.set(0,-.78,i===10?mix(3.2,-4,smooth(s,0,10)):5-smooth(s,0,15)*2.0);ship.visible=[0,1,10,11].includes(i);
        fly.rotation.z=t*1.8;piston.position.x=-1.41+.25*Math.cos(t*1.8);link.rotation.z=.85+.25*Math.sin(t*1.8);
        const h=i===7?smooth(s,2,11):i===8?1-smooth(s,1,12):.30;
        ram.position.y=.65+h*.72;pistonTop.position.y=1.11+h*.72;
        pulses.forEach((p,j)=>p.position.x=-.37+((t*.9+j*.27)%2.24));
    },overlay(c,i,s){
        if(i===3||i===4)annotation(c,'422 TONNES','Counterweight per leaf','#ff936a');
        if(i===5||i===9)annotation(c,'COG  →  RACK','Rotation lifts the roadway','#ff936a');
        if(i===6||i===8)panel(c,'ORIGINAL DRIVE  /  FOLLOW THE ENERGY',ctx=>{
            const words=['Steam','Pump','Water','Drive'];for(let j=0;j<4;j++){
                const x=65+j*150;ctx.fillStyle=j===Math.floor(s/3)%4?'#ffd02e':'#71d7e0';
                ctx.beginPath();ctx.arc(x+35,843,7,0,Math.PI*2);ctx.fill();ctx.font='bold 20px Arial';ctx.fillText(words[j],x,887);
                if(j<3){ctx.fillStyle='#49718a';ctx.fillRect(x+54,841,95,3);}
            }});
    }};
}
