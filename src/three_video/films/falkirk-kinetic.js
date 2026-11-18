import * as T from 'three';
import {smooth,mix} from './waterworks.js';
import {annotation,panel} from './kinetics.js';

export function makeModel(k){
    const {group,mesh,box,rod,ring,disk,gear}=k;
    const night=0x163a67,steel=0x1c839e,lime=0xa9ed35,water=0x028cae,orange=0xff6543;
    box(0x399a72,0,-.81,0,54,1,58);
    box(water,0,.27,8.7,17,.12,15.4);
    for(let i=0;i<19;i++){
        const x=-21+i*2.3,z=-14-Math.sin(i*2.2)*3;
        const hill=mesh(new T.IcosahedronGeometry(1,1),i%2?0x298a83:0x399b77,x,.12,z);
        hill.scale.set(4,2.2+(i%4)*.62,3.6);
    }
    for(let j=0;j<28;j++){const x=(j%2?1:-1)*(5+(j%4)*.8),z=-9+(j*.81)%21;
        mesh(new T.ConeGeometry(.39,1.26,8),j%2?0x14816c:0x1e947a,x,.28,z);}
    const centerY=2.73,R=2.22,ax=group();ax.position.y=centerY;
    for(const z of [-2.08,2.08]){
        for(const sign of [-1,1]){
            rod(night,[sign*1.20,-.27,z],[0,centerY,z],.16);
            const g=group(ax);g.position.z=z;
            // Tapered rotating booms surround the open passenger gondolas.
            rod(steel,[sign*.09,-R-.30,0],[sign*.41,0,0],.145,g);
            rod(steel,[sign*.41,0,0],[sign*.09,R+.30,0],.145,g);
        }
        disk(ax,0,0,z,.47,.29,night);ring(ax,0,0,z+.17,.35,.07,lime);
        for(const side of [-1,1]){
            ring(ax,0,side*R,z,.81,.12,steel);
            ring(ax,0,side*R,z+.025,.83,.035,lime);
        }
    }
    rod(night,[0,centerY,-2.26],[0,centerY,2.26],.28);
    const cogs=[gear(ax,0,0,2.34,.35,16,orange),gear(ax,0,R*.51,2.23,.43,17,night)];
    const holders=[],gates=[];
    for(const sign of [-1,1]){
        const g=group(ax);g.position.y=sign*R;holders.push(g);
        box(night,0,-.37,0,1.59,.19,4.14,g);box(water,0,-.22,0,1.40,.10,4.04,g);
        for(const a of [-1,1]){
            box(steel,a*.78,-.10,0,.12,.44,4.1,g);
            box(lime,a*.78,.13,0,.126,.06,4.1,g);
            for(let n=0;n<8;n++)box(night,a*.80,.24,-1.85+n*.53,.038,.25,.038,g);
            rod(night,[a*.80,.37,-2],[a*.80,.37,2],.025,g);
        }
        const door=box(orange,0,-.10,1.99,1.52,.51,.11,g);gates.push(door);
    }
    const boat=group(holders[0]);
    const hull=mesh(new T.SphereGeometry(1,28,14),0xffbc17,0,-.12,0,boat);hull.scale.set(.45,.25,1.44);
    box(0xffbd16,0,.09,.05,.82,.34,2.02,boat);box(0x137d9d,0,.27,.05,.75,.2,1.79,boat);
    for(let j=0;j<5;j++)box(0xffffbc,0,.255,-.70+j*.38,.76,.20,.032,boat);
    box(0xffdd39,0,.40,.02,.86,.09,1.96,boat);
    const top=centerY+R-.21;
    box(night,0,top-.26,-8.1,2,.45,12.05);box(water,0,top,-8.1,1.73,.08,12.05);
    for(const x of [-.94,.94]){
        box(steel,x,top+.03,-8.1,.15,.38,12.05);
        for(let j=0;j<4;j++)rod(night,[x,0,-4-j*2.1],[x,top-.30,-4-j*2.1],.19);
    }
    const wake=[];
    for(let j=0;j<55;j++)wake.push(box(j%3?0x3bb8c6:0x99dfd4,0,-.173,0,.052,.012,.3+(j%4)*.13));
    const shots=[[0,2.65,0,4,3,13,8.5],[0,2,-2.5,9,6,12,11.4],[0,2.8,-2,6,5,11,9.5],[0,1,1,4,3.2,12,5.2],[0,.72,1,3,4,11,4.6],[0,2.65,0,3,1.2,14,7.9],[0,.75,.8,2,2,10,5.5],[0,2.7,0,3,1.4,13,7.75],[1.2,3.5,0,4,2,13,7.3],[0,4.7,-2.4,5,4,9,6.5],[0,3.6,-5.0,6.5,5,12,9.5],[0,2.65,0,5,2,13,8.6]];
    return {shots,update(i,s,t){
        let phase=i===0?smooth(s,0,13):i<=6?0:i===7?.58*smooth(s,0,14):i===8?.58+.42*smooth(s,0,13):i<=10?1:smooth(s,0,14);
        ax.rotation.z=phase*Math.PI;holders.forEach(g=>g.rotation.z=-ax.rotation.z);
        boat.position.z=i===3?mix(3.1,0,smooth(s,1,11)):i===9?-3.7*smooth(s,3,14):i===10?-4.8-s*.29:0;
        gates.forEach(g=>g.position.y=i===3?-.66:i===6?mix(-.66,-.10,smooth(s,1.8,8)):i>=9&&i<=10?-.66:-.10);
        cogs[0].rotation.z=-ax.rotation.z;cogs[1].rotation.z=2*ax.rotation.z;
        wake.forEach((w,j)=>w.position.set(-7.7+(j*2.719)%15,.337,1.1+(j*.77+t*.19)%14));
    },overlay(c,i,s){
        if(i===4||i===5){panel(c,'FLOATING LOAD  /  MASS BALANCE',ctx=>{
            const q=smooth(s,1,9);ctx.fillStyle='#19c4db';ctx.fillRect(65,834,240*(1-.32*q),20);
            ctx.fillStyle='#ffd02d';ctx.fillRect(65+240*(1-.32*q),834,240*.32*q,20);
            ctx.fillStyle='#19c4db';ctx.fillRect(390,834,240,20);
            ctx.fillStyle='white';ctx.font='18px Arial';ctx.fillText('Water + boat',65,889);ctx.fillText('Water only',390,889);});}
        if(i===2||i===10){panel(c,i===2?'FORMER ROUTE  /  11 LOCKS':'UPPER ROUTE  /  SCHEMATIC',ctx=>{
            const steps=i===2?11:2;ctx.strokeStyle='#4de8e3';ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(70,882);
            for(let j=0;j<steps;j++){const x=70+(j+1)*540/steps,y=882-(j+1)*58/steps;ctx.lineTo(x,y+58/steps);ctx.lineTo(x,y);}ctx.stroke();});}
        if(i===7||i===8)annotation(c,'HALF A TURN','Gondolas stay level','#b5ff53');
        if(i===1||i===9)annotation(c,'24 METRES','Basin to aqueduct','#b5ff53');
    }};
}
